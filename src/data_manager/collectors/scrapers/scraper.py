import requests
import re

from typing import List
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from typing import Optional, Dict

from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.utils.logging import get_logger

logger = get_logger(__name__)

class LinkScraper:
    """
    Single scraper for all our link needs that handles Selenium and requests.
    This class explicitly handles requests, but if selenium scraping is enabled for a link
    everything is passed through to the driver including how the page data is collected and 
    how the next level of links are found. This class DOESNT own the selenium driver, that is 
    owned by the scraper manager class. 
    """

    def __init__(self, verify_urls: bool = True, enable_warnings: bool = True) -> None:
        self.verify_urls = verify_urls
        self.enable_warnings = enable_warnings

    def reap(self, response, current_url: str, selenium_scrape: bool = False, authenticator = None):
        """
        probably the most complicated method here and most volatile in terms of maybe later needing a rewrite

        this method is here to deal with any result that it gets back. for a selenium resource it expects results as a 
        BrowserIntermediaryResult, otherwhise it will handle it as a normal http response. it handles getting the next set 
        of links and updating the page data gathered

        Args: 
            response (BrowserIntermediaryResult | requests.response): whatever has been collected for the current_url by the scraper
            selenium_scrape (bool): whether or not selenium was used to scrape this content
            authenticator (SSOAuthenticator | None): client being used to crawl websites or just for auth 

        Return (list[str]): next links to crawl (depends on method used to get the previous results)
        """

        # mark as visited
        self.visited_urls.add(current_url)

        source_type = "links" if (authenticator is None) else "sso"
        
        if selenium_scrape: # deals with a selenium response (should work for both non authenitcated and authenticated sites in principle)
            assert(authenticator is not None) ## this shouldnt be tripped
            artifacts = response.artifacts
            links = response.links

            res = []

            for artifact, link in zip(artifacts, links):
                content = artifact.get("content")
                resource = ScrapedResource(
                        url = link,
                        content = content, 
                        suffix=response.get("suffix", "html"),
                        source_type = source_type,
                        metadata={ # later we might change this if we want more constructive metadata but for now this works 
                            "title": response.get("title"),
                            "content_type": "rendered_html",
                            "renderer": "selenium",
                            },
                        )
                res += authenticator.get_links_with_same_hostname(current_url)
                self.page_data.append(resource)
                
        else: # deals with http response
            content_type = response.headers.get("Content-type")

            if current_url.lower().endswith(".pdf"):
                resource = ScrapedResource(
                    url=current_url,
                    content=response.content,
                    suffix="pdf",
                    source_type=source_type,
                    metadata={"content_type": content_type},
                )
            else:
                resource = ScrapedResource(
                    url=current_url,
                    content=response.text,
                    suffix="html",
                    source_type=source_type,
                    metadata={
                        "content_type": content_type,
                        "encoding": response.encoding,
                    },
                )
            res = self.get_links_with_same_hostname(current_url, resource)
            self.page_data.append(resource)

        return res # either collected via http or via authenticators method


    def crawl(self, start_url: str, browserclient = None, max_depth: int = 1, selenium_scrape: bool = False):
        """
        crawl pages from a given starting url up to a given depth either using basic http or a provided browser client

        Args : 
            start_url (str): Url to start crawling from
            authenticator (SSOAuthenticator): class used for handling authenticatoin for web resources
            max_depth (int): max depth of links to descend from the start url
            selenium_scrape (bool): tracks whether or not the page should be scraped through selenium or not

        Returns: List[]

        """

        if not self.enable_warnings:
            import urllib3
            urllib3.disable_warnings()
            
        depth = 0 
        self.visited_urls = set()
        self.page_data = []
        to_visit = [start_url]
        level_links = []
        pages_visited = 0


        base_hostname = urlparse(start_url).netloc
        logger.info(f"Base hostname for crawling: {base_hostname}")


        # session either stays none or becomes a requests.Session object if not selenium scraping
        session = None

        if selenium_scrape: # scrape page with pure selenium
            if browserclient is None: 
                logger.error(f"Failed to crawl: {start_url}, auth is needed but no browser clilent was passed through")
                return [] 
            browserclient.authenticate_and_navigate(start_url)

        elif not selenium_scrape and browserclient is not None: # use browser client for auth but scrape with http request
            session = requests.Session()
            cookies = browserclient.authenticate(start_url)
            if cookies is not None:
                for cookie in cookies:
                    session.cookies.set_cookie(cookie['name'], cookie['value'])

        else: # pure html no browser client needed
            session = requests.Session()

        while to_visit and depth < max_depth:
            current_url = to_visit.pop(0)
            
            # Skip if we've already visited this URL
            if current_url in self.visited_urls:
                continue

            logger.info(f"Crawling page {depth + 1}/{max_depth}: {current_url}")

            try:

                # grab the page content 
                if not selenium_scrape: 
                    assert (session is not None) # REMOVELATER
                    response = session.get(current_url, verify = self.verify_urls)
                    response.raise_for_status()
                else: 
                    assert (browserclient is not None) # REMOVELATER
                    browserclient.navigate_to(current_url, wait_time = 2)
                    response = browserclient.extract_page_data(current_url) # see the BrowserIntermediaryResult class to see what comes back here
                            
                
                # Mark as visited and store content
                pages_visited += 1
                new_links = self.reap(response, current_url, selenium_scrape, browserclient)
                        
                for link in new_links:
                    if link not in self.visited_urls and link not in to_visit and link not in level_links:
                        logger.info(f"Found new link: {link} (nv: {pages_visited})")
                        level_links.append(link)

            except Exception as e:
                logger.info(f"Error crawling {current_url}: {e}")
                self.visited_urls.add(current_url)  # Mark as visited to avoid retrying           

            if not to_visit:
                to_visit.extend(level_links)
                level_links = []
                depth += 1
            
        logger.info(f"Crawling complete. Visited {pages_visited} pages.")
        return list(self.page_data)

    def get_links_with_same_hostname(self, url: str, page_data: ScrapedResource):
        """Return all links on the page that share the same hostname as `url`. For now does not support PDFs"""

        base = url
        links = set()
        a_tags = []
        
        if (page_data.suffix == "html"):
            soup = BeautifulSoup(page_data.content, "html.parser")
            a_tags = soup.find_all("a", href=True) 

        # how many  links found on the first level
        for tag in a_tags:
            full = urljoin(base, tag["href"])
            if urlparse(full).netloc == base:
                links.add(full)
        return list(links)
