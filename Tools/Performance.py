#This file will esentially just act as a timer for now and time each full run
import time
from datetime import datetime as dt
from rich import print

#Functions to start and stop the clock and calculate the time it took for a full run
def start_timer():
    start_time = dt.now()
    print(f"[purple]Started: {start_time:%H:%M:%S}[/purple]")
    return start_time

def stop_timer():
    end_time = dt.now()
    print(f"[purple]Finished: {end_time:%H:%M:%S}[/purple]")
    return end_time

def run_time(start_time, end_time):
    elapsed = end_time - start_time          # a timedelta
    seconds = elapsed.total_seconds()        # e.g. 873.42
    minutes, secs = divmod(int(seconds), 60)
    print(f"[purple]Run took {minutes} min {secs} s[/purple]")
    return elapsed
