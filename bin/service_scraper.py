#!/bin/python
import os
from utils.scraper import Scraper
from utils.data_manager import DataManager

scraper=Scraper()
scraper.hard_scrape(verbose=True)

dm = DataManager()
dm.update_vectorstore()
