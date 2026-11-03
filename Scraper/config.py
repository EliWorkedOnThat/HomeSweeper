#Here we will house all thats needed as configurations for each scraper 
#It will act as a global variable holder

#Time in minutes to wait before rescraping
Rotation_Rescraping_Time = 5

#Time in seconds to rest between scraping listings so we don't pull them instantly and get flagged
Rest_Time = 3

#Boolean to determine if we are in a demo or production environment (Set to false to start production work)
Demo_Environment = True

#Database name to use for all scrapers
Database_Name = "Homesweeper.db"
