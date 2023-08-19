import os
from A2rchi.utils.scraper import Scraper

scraper=Scraper()

while True:
    scraper.hard_scrape(verbose=True)
    os.system("sleep 7d")
