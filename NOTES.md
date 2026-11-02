## Notes for now before i generate a real notes file
The structure is simple each page to be scraped must contain a 
playwright and request folder in its own directory so we can fix issues for each one seperately and easily debug or scale.

Always run tests when anything changes in the website or library that you are using no exceptions.

There will be an Engine.py and Output.py that will set the example and be used as the mass scraping orchestrator and output file generator.

Each script is called by the orchestrator in production always and the only exemption is Test Files ofcourse.

## UI
There is no UI yet until we have a stable demo for scraping the main focus should be getting scrapers to consistently pull what we want from each site.

## Some valuable info
Make sure to use headers and rotate them if possible.
Use randomised rests and patterns to avoid detection.
Add rate limiting to requests when sending them out.
Urls can be crafter to act as the filtering the user/client will use in order to scrape based on what the client wants.
