#This file keeps HomeSweeper to one run per PC and finds the server a free port. Works on Windows, macOS and Linux.
#- One run: the server (and a terminal Engine turn) holds an OS lock on Data/server.lock for as long as it runs. A second start
#  can't get it and is refused; it never touches the running one. Two runs would scrape into the same database at once. The OS
#  drops the lock when the process ends, even after a crash, so a leftover file never blocks the next start.
#- Free port: tries START_PORT first and then the next ones, so the address stays the same unless another program has the port.
#Imports
import errno
import json
import os
import socket
import sys
from rich import print
from Scraper.DB import DB_FILE

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

#Global Variables
START_PORT = 8000 #The port tried first
PORT_ATTEMPTS = 50 #How many ports to try after it (8000-8049) before giving up
LOCK_FILE = DB_FILE.parent / "server.lock" #Held locked by the running server, never read
ADDRESS_FILE = DB_FILE.parent / "server.json" #The running server's address, for a second start to open
#A probe bind that fails with one of these means another program has the port (EACCES: Windows reserves some port ranges for Hyper-V/WSL)
PORT_TAKEN_ERRORS = {errno.EADDRINUSE, errno.EACCES, getattr(errno, "WSAEADDRINUSE", None), getattr(errno, "WSAEACCES", None)} - {None}

#Function that takes the one-instance lock. Returns the open lock file (keep it until the server stops), or None if a server is
#already running. Locks only the first byte; Windows allows locking past the end of an empty file.
def acquire_lock():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "a+b")
    try:
        lock.seek(0)
        if sys.platform == "win32":
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    try:
        ADDRESS_FILE.unlink(missing_ok=True) #Left by a run that was killed; the address belongs to whoever holds the lock
    except OSError:
        pass
    return lock

#Function that refuses a second run: says what is already running and exits, leaving the running one alone
def refuse_second_run():
    url = running_url()
    where = f"the server at {url}" if url else "a run (the server starting up, or a terminal Engine turn)"
    print(f"[red]HomeSweeper is already running: {where}. Close it first, then start again.[/red]")
    sys.exit(1)

#Function that makes Ctrl+C, Ctrl+Break and `kill` end the server with a normal exit, so its `finally` (close the port, release the
#lock, delete server.json) always runs. uvicorn shuts down gracefully on these, then sends the same signal again to the handler that
#was there before; Python's default for SIGTERM/SIGBREAK kills the process on the spot (skipping the `finally`, exit code 3 seen on
#Windows), and for SIGINT it raises KeyboardInterrupt with a traceback.
def exit_cleanly_on_signals():
    import signal
    def stop(signum, frame):
        sys.exit(0)
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"): #SIGBREAK exists on Windows only
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)

#Function that releases the lock and removes the address file when the run stops. Close the server's socket before calling it.
def release_lock(lock):
    try:
        ADDRESS_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        lock.seek(0)
        if sys.platform == "win32":
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    lock.close()

#Function that saves the running server's address for a second start to find. Only the lock holder calls it.
def save_address(url):
    temporary = ADDRESS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
    os.replace(temporary, ADDRESS_FILE) #Never leaves a half-written file for a second start to read

#Function that returns the running server's address, or None if it hasn't saved it yet (still starting) or the file can't be read
def running_url():
    try:
        return json.loads(ADDRESS_FILE.read_text(encoding="utf-8"))["url"]
    except (OSError, ValueError, KeyError, TypeError):
        return None

#Helper function that checks whether another program uses a port on any address of this PC. It test-binds the wildcard address
#(0.0.0.0, and :: for IPv6) without listening, which is instant and shows no firewall prompt. Binding 127.0.0.1 alone isn't enough:
#Windows lets it succeed while another program listens on 0.0.0.0, and would quietly steal that program's local traffic.
def port_taken(port):
    for family, address in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue #No IPv6 on this PC
        try:
            if sys.platform == "win32":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1) #Without it Windows can share the port
            else:
                #Ignores the TIME_WAIT a just-stopped server leaves for ~1 min (else a quick restart moves to 8001), still fails on a
                #live listener. On macOS it lets this probe pass a listener on 127.0.0.1 only; bind_free_port()'s real bind catches that.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((address, port))
        except OSError as error:
            if error.errno in PORT_TAKEN_ERRORS or getattr(error, "winerror", None) in PORT_TAKEN_ERRORS:
                return True
            #Any other error (e.g. IPv6 switched off) says nothing about the port
        finally:
            probe.close()
    return False

#Function that binds the server's socket on the first free port from START_PORT. Returns the bound socket, which uvicorn takes
#over (it starts listening), so no other program can grab the port between this check and the server start.
def bind_free_port(host):
    for port in range(START_PORT, START_PORT + PORT_ATTEMPTS):
        if port_taken(port):
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) #A restart can reuse the port at once (as uvicorn does)
            sock.bind((host, port))
            return sock
        except OSError:
            sock.close() #Taken between the check and the bind, try the next one
    raise OSError(f"No free port between {START_PORT} and {START_PORT + PORT_ATTEMPTS - 1}")
