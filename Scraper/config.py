#Here we will house all thats needed as configurations for each scraper 
#It will act as a global variable holder

#Time in seconds to rest between scraping listings so we don't pull them instantly and get flagged
Rest_Time = 3

#Boolean to determine if we are in a demo or production environment (Set to false to start production work)
Demo_Environment = False

#How many NEW listings each site scrapes per turn (one run), before the engine saves them and moves to the next site
#Every turn starts at page 1 (newest first) for fresh posts, then continues the older listings from where the last turn stopped
#Demo_Environment caps this at each scraper's DEMO_LISTING_LIMIT (5)
Listings_Per_Turn = 50

#Boolean to run the scraper browser invisibly in the background (Set to False to watch the browser window while debugging)
Headless = False

#Where the scraper's browser window opens when Headless is False, as (x, y) in pixels from the top-left corner of the main screen.
#Negative numbers put it off screen: it stays a normal, windowed browser but doesn't get in the way. (On a PC with a second
#monitor to the left or above, -2000 may land on that monitor; use bigger numbers like (-10000, -10000) there.)
#Set it to None to open the window normally on screen, to watch the scraper while debugging.
Window_Position = None #Off screen: (-2000, -2000). On screen for now, to watch the scraper work.

#Size of that window in pixels, (width, height). None lets Chromium pick.
Window_Size = (1280, 720)

#True = scrape with the Google Chrome installed on this PC (sites see "Google Chrome", like a real user), falling back to
#Playwright's Chromium if Chrome isn't installed. False = always use Playwright's Chromium.
#Either way the scraper gets a fresh, empty profile: never the PC owner's bookmarks, logins or open windows.
Use_Installed_Chrome = True

#Database name to use for all scrapers
Database_Name = "Homesweeper.db"
