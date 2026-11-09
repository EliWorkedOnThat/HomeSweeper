#This file keeps console output from crashing the app. Scraped titles carry emoji (🏡) and Albanian letters; on a Windows console
#whose output is redirected or piped, Python writes cp1252, and printing an emoji raised UnicodeEncodeError in the middle of saving
#a listing, which dropped the rest of the turn. Call utf8_output() first thing in every entry point (Server, Engine, the launcher).
#Imports
import sys

#Function that switches stdout/stderr to UTF-8 (unprintable characters become "?" instead of raising)
def utf8_output():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
