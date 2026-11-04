#Imports
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from rich import print
from datetime import datetime, timedelta, timezone
import html
import json
import random
import re
from Scraper.config import Demo_Environment, Rest_Time, Headless, Listings_Per_Turn

#Global Variables 
SITE_URL = "https://duashpi.al/kerko-prona?sort=new&page=1"
DEMO_LISTING_LIMIT = 5 #How many listings to scrape per turn when Demo_Environment is True
PROCESSED_UPIDS = set() #Everything this turn skips: already in the DB, scraped this turn, or remembered below
SCRAPED_LISTINGS = []

#Kept between turns while the program runs (reset_state() leaves them alone), a restart just starts from page 1 again
RESUME_PAGE = 1 #Deepest page reached so far, once a turn catches up with the new posts it jumps here for the older ones
AGENCY_UPIDS = set() #Agency listings we skipped, so "Jo Listime Agjensie" doesn't treat them as new every turn
FAILED_UPIDS = set() #Listings that failed (e.g. no JSON-LD), not reopened until the program restarts
STOP_CHECK = None #The engine's should_stop function for the current turn (None = never stop early)

