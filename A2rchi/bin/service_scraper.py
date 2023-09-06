from A2rchi.utils.scraper import Scraper

import os

print("Starting Scraper Service")
scraper=Scraper()

while True:
    scraper.hard_scrape(verbose=True)
    os.system("sleep 7d")
