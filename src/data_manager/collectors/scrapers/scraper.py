import requests
import re

from typing import List
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.utils.logging import get_logger

logger = get_logger(__name__)


class WebScraper:
    """Simple HTTP scraper that fetches raw content from public URLs."""

    def __init__(self, verify_urls: bool = True, enable_warnings: bool = True) -> None:
        self.verify_urls = verify_urls
        self.enable_warnings = enable_warnings

    def scrape(self, url: str) -> List[ScrapedResource]:
        """Fetch the given URL and return the retrieved payload.

        The caller is responsible for deciding how to persist the payload.
        """
        if not self.enable_warnings:
            import urllib3  # imported lazily to avoid the dependency when unused

            urllib3.disable_warnings()

        logger.info(f"Fetching URL {url}")
        response = requests.get(url, verify=self.verify_urls)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type")
        if url.lower().endswith(".pdf"):
            resource = ScrapedResource(
                url=url,
                content=response.content,
                suffix="pdf",
                source_type="links",
                metadata={"content_type": content_type},
            )
        else:
            title = re.findall(r'<title>(.*)<\/title>',response.text)
            resource = ScrapedResource(
                url=url,
                content=response.text,
                suffix="html",
                source_type="links",
                metadata={
                    "content_type": content_type,
                    "encoding": response.encoding,
                    "title": title[0] if len(title)>0 else url
                },
            )

        return [resource]


    def crawl(self, start_url, max_depth=1):
        """crawl pages from a given starting url up to a given depth """

        if not self.enable_warnings:
            import urllib3
            urllib3.disable_warnings()
            
        depth = 0 

        self.visited_urls = set()
        self.page_data = []
        to_visit = [start_url]
        level_links = []

        base_hostname = urlparse(start_url).netloc
        logger.info(f"Base hostname for crawling: {base_hostname}")

        # History record   
        pages_visited = 0
        self.visited_urls = set()


        while to_visit and depth < max_depth:
            current_url = to_visit.pop(0)
            
            # Skip if we've already visited this URL
            if current_url in self.visited_urls:
                continue
                
            logger.info(f"Crawling page {depth + 1}/{max_depth}: {current_url}")

            try:
                # grab the page content 
                response = requests.get(current_url, verify = self.verify_urls)
                response.raise_for_status()
                            
                
                # Mark as visited
                self.visited_urls.add(current_url)
                pages_visited += 1

                content_type = response.headers.get("Content-type")
                if current_url.lower().endswith(".pdf"):
                    resource = ScrapedResource(
                        url=current_url,
                        content=response.content,
                        suffix="pdf",
                        source_type="links",
                        metadata={"content_type": content_type},
                    )
                else:
                    resource = ScrapedResource(
                        url=current_url,
                        content=response.text,
                        suffix="html",
                        source_type="links",
                        metadata={
                            "content_type": content_type,
                            "encoding": response.encoding,
                        },
                    )

                # Extract and store page data
                self.page_data.append(resource)
                # logger.info(f"Extracted data from {current_url} ({len(self.page_data['content'])} chars)")
                
                # Get links to follow
                new_links = self.get_links_with_same_hostname(current_url, self.page_data[-1])
                # logger.info(f"Found {len(new_links)} links on the page (nv: {pages_visited})")

                # Add new links to visit
                for link in new_links:
                    if link not in self.visited_urls and link not in to_visit and link not in level_links:
                        logger.info(f"Found new link: {link} (nv: {pages_visited})")
                        level_links.append(link)

                # Scan next level if to_visit is empty
                if not to_visit:
                    to_visit.extend(level_links)
                    level_links = []
                    depth += 1
                        
            except Exception as e:
                logger.info(f"Error crawling {current_url}: {e}")
                self.visited_urls.add(current_url)  # Mark as visited to avoid retrying           
            
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
