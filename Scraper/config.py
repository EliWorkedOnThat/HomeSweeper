#Here we will house all thats needed as configurations for each scraper 
#It will act as a global variable holder

#Time in seconds to rest between scraping listings so we don't pull them instantly and get flagged
Rest_Time = 3

#Boolean to determine if we are in a demo or production environment (Set to false to start production work)
Demo_Environment = True

#How many NEW listings each site scrapes per turn (one run), before the engine saves them and moves to the next site
#Every turn starts at page 1 (newest first) for fresh posts, then continues the older listings from where the last turn stopped
#Demo_Environment caps this at each scraper's DEMO_LISTING_LIMIT (5)
Listings_Per_Turn = 50

#Boolean to run the scraper browser invisibly in the background (Set to False to watch the browser window while debugging)
Headless = False

#Database name to use for all scrapers
Database_Name = "Homesweeper.db"
