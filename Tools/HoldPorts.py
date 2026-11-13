#This file is a dev tool that occupies a range of ports, so you can check that the server skips them and moves on to the next free one.
#Run from the project root:  py -3.14 -m Tools.HoldPorts            (holds 8000-8005)
#                            py -3.14 -m Tools.HoldPorts 8000 8010  (holds 8000-8010)
#then start the server in a second console (py -3.14 -m Scraper.Server): it should say it uses the first port after the range.
#Each port is held on a different address (0.0.0.0, 127.0.0.1, ::, ::1), the ways another program can take it, so every case of
#Instance.port_taken() is tested. Ctrl+C frees them all.
#Imports
import socket
import sys
import time
from rich import print
from Scraper.Console import utf8_output

#Global Variables
ADDRESSES = ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::"), (socket.AF_INET6, "::1"))

#Function that listens on every port from first to last (one address each, taking turns) and keeps them until Ctrl+C
def hold_ports(first, last):
    held = []
    for index, port in enumerate(range(first, last + 1)):
        family, address = ADDRESSES[index % len(ADDRESSES)]
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.bind((address, port))
            sock.listen()
        except OSError as error:
            sock.close()
            print(f"[yellow]Port {port} on {address}: could not hold it ({error.strerror}), it's probably already taken[/yellow]")
            continue
        held.append(sock)
        print(f"[cyan]Holding port {port} on {address}[/cyan]")
    print(f"[green]Holding {len(held)} port(s). Start the server in another console; it should use {last + 1}. Ctrl+C to free them.[/green]")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for sock in held:
            sock.close()
        print("[green]Ports freed.[/green]")

if __name__ == "__main__":
    utf8_output()
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 8005
    hold_ports(first, last)
