#Imports
import requests
import httpx
from rich import print
import time 
import json
from bs4 import BeautifulSoup as bs

#The url we will send the request to
URL = "https://www.merrjep.al/njoftime/imobiliare-vendbanime/ne-shitje-kerkoj-te-blej-me-qera-kerkohet-me-qera-nderrohet"

#Use headers to impersonate a real browser call to avoid being flagged
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sq-AL,sq;q=0.9,en;q=0.8",
}

#Function to send a fetch request to the URL using Httpx to follow redirects and use custom headers
def fetch(url: str) -> httpx.Response:
    with httpx.Client(headers=HEADERS, follow_redirects = True, timeout=10) as client:
        return client.get(url)

