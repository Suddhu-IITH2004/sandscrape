# sandscrape
Turn any URL into a self-healing REST API in seconds.
Traditional web scrapers break the moment a website updates its HTML. hydra-scrape spins up parallel, isolated Daytona sandboxes to simultaneously race multiple extraction strategies (BeautifulSoup, Playwright, and XHR Network Sniffing), validates the extracted JSON, and automatically deploys the fastest working script as a live FastAPI endpoint. If the site ever changes, it re-runs the race and heals itself automatically.
