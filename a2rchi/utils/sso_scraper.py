import os
import time
import re
import json
import urllib.parse
from abc import ABC, abstractmethod
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

class SSOScraper(ABC):
    """Generic base class for SSO-authenticated web scrapers."""
    
    def __init__(self, username=None, password=None, headless=True, site_type="generic", max_depth=50):
        """Initialize the SSO scraper with credentials and browser settings.
        
        Args:
            username (str, optional): SSO username. If None, will try to get from env vars.
            password (str, optional): SSO password. If None, will try to get from env vars.
            headless (bool): Whether to run the browser in headless mode.
            site_type (str): Type of site to scrape ('generic' or 'mkdocs')
            max_depth (int): Maximum number of pages to crawl per page.
        """
        self.username = username or self.get_username_from_env()
        self.password = password or self.get_password_from_env()
        self.headless = headless
        self.max_depth = max_depth
        self.site_type = site_type
        self.driver = None
        self.visited_urls = set()
        self.page_data = []
        
        if self.username:
            logger.info(f"Using username: {self.username}")
    
    @abstractmethod
    def get_username_from_env(self):
        """Get username from environment variables. Override in subclasses."""
        pass
    
    @abstractmethod
    def get_password_from_env(self):
        """Get password from environment variables. Override in subclasses."""
        pass
    
    @abstractmethod
    def login(self):
        """Login to SSO with the provided credentials. Override in subclasses."""
        pass
    
    def setup_driver(self):
        """Configure and initialize the Firefox WebDriver."""
        firefox_options = FirefoxOptions()
        if self.headless:
            firefox_options.add_argument("--headless")
        
        # Additional options for better performance in containers
        firefox_options.add_argument("--no-sandbox")
        firefox_options.add_argument("--disable-dev-shm-usage")
        firefox_options.add_argument("--disable-gpu")
        firefox_options.add_argument("--window-size=1920,1080")
        
        # Create Firefox profile with preferences
        firefox_profile = webdriver.FirefoxProfile()
        firefox_profile.set_preference("dom.disable_open_during_load", False)
        firefox_profile.set_preference("browser.download.folderList", 2)
        firefox_profile.set_preference("browser.download.manager.showWhenStarting", False)
        firefox_profile.set_preference("browser.helperApps.neverAsk.saveToDisk", "application/pdf")
        
        # Initialize the driver with options
        self.driver = webdriver.Firefox(options=firefox_options)
        self.driver.set_page_load_timeout(30)
        logger.info(f"Starting Firefox browser in {'headless' if self.headless else 'visible'} mode...")
        return self.driver
    
    def navigate_to(self, url, wait_time=1):
        """Navigate to specified URL and wait for page to load."""
        if not self.driver:
            raise RuntimeError("WebDriver not initialized. Call setup_driver() first.")
            
        self.driver.get(url)
        time.sleep(wait_time)  # Enable wait time for page loading
        logger.info(f"Navigated to {url}")
        logger.info(f"Page title: {self.driver.title}")
        return self.driver.title
    
    def get_page_content(self):
        """Get the current page source."""
        if not self.driver:
            raise RuntimeError("WebDriver not initialized. Call setup_driver() first.")
        return self.driver.page_source
    
    def extract_page_data(self, url):
        """Extract the title and raw HTML content from the current page."""
        title = self.driver.title
        
        # Get both text content for summary and full HTML
        text_content = self.driver.find_element(By.TAG_NAME, "body").text
        html_content = self.driver.page_source
        
        return {
            "url": url,
            "title": title,
            "content": text_content,
            "html": html_content
        }
    
    def extract_mkdocs_page_data(self, url):
        """Extract title and raw HTML from MkDocs page."""
        # Just get the title and full HTML without parsing
        title = self.driver.title
        html_content = self.driver.page_source
        text_content = self.driver.find_element(By.TAG_NAME, "body").text
        
        return {
            "url": url,
            "title": title,
            "content": text_content,
            "html": html_content,
            "type": "mkdocs"
        }
    
    def get_links_with_same_hostname(self, base_url):
        """Extract all links from the current page that have the same hostname as base_url."""
        base_hostname = urllib.parse.urlparse(base_url).netloc
        links = []
        
        # Find all anchor tags
        if self.site_type == "mkdocs":
            # For MkDocs, prioritize navigation links
            anchors = self.driver.find_elements(By.CSS_SELECTOR, ".md-nav__link, .md-content a")
        else:
            anchors = self.driver.find_elements(By.TAG_NAME, "a")
        
        for anchor in anchors:
            try:
                href = anchor.get_attribute("href")
                if href and href.strip():
                    parsed_url = urllib.parse.urlparse(href)
                    # Check if the link has the same hostname and is not a fragment
                    if parsed_url.netloc == base_hostname and parsed_url.scheme in ('http', 'https'):
                        # Normalize the URL to prevent duplicates
                        normalized_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
                        if parsed_url.query:
                            normalized_url += f"?{parsed_url.query}"
                        links.append(normalized_url)
            except Exception as e:
                logger.error(f"Error extracting link: {e}")
                
        return list(set(links))  # Remove duplicates
    
    def crawl(self, start_url):
        """Crawl pages starting from the given URL, storing title and content of each page.
        
        Args:
            start_url (str): The URL to start crawling from
            
        Returns:
            list: List of dictionaries containing page data (url, title, content)
        """
        max_depth = self.max_depth
        if not self.driver:
            self.setup_driver()
            
        # Reset crawling state
        self.visited_urls = set()
        self.page_data = []
        to_visit = [start_url]
        
        # First authenticate through the start URL
        self.authenticate_and_navigate(start_url)
        
        base_hostname = urllib.parse.urlparse(start_url).netloc
        logger.info(f"Base hostname for crawling: {base_hostname}")
        logger.info(f"Site type: {self.site_type}")
        
        pages_visited = 0
        
        while to_visit and pages_visited < max_depth:
            current_url = to_visit.pop(0)
            
            # Skip if we've already visited this URL
            if current_url in self.visited_urls:
                continue
                
            logger.info(f"Crawling page {pages_visited + 1}/{max_depth}: {current_url}")
            
            try:
                # Navigate to the page
                self.navigate_to(current_url, wait_time=2)
                
                # Mark as visited
                self.visited_urls.add(current_url)
                pages_visited += 1
                
                # Extract and store page data
                page_data = self.extract_page_data(current_url)
                self.page_data.append(page_data)
                logger.info(f"Extracted data from {current_url} ({len(page_data['content'])} chars)")
                
                # Get links to follow
                new_links = self.get_links_with_same_hostname(current_url)
                logger.info(f"Found {len(new_links)} links on the page")
                
                # Add new links to visit
                for link in new_links:
                    if link not in self.visited_urls and link not in to_visit:
                        to_visit.append(link)
                        
            except Exception as e:
                logger.info(f"Error crawling {current_url}: {e}")
                self.visited_urls.add(current_url)  # Mark as visited to avoid retrying
        
        logger.info(f"Crawling complete. Visited {pages_visited} pages.")
        return self.page_data
    
    def save_crawled_data(self, output_dir="crawled_data"):
        """Save the crawled data to files.
        
        Args:
            output_dir (str): Directory to save the crawled data
        """
        if not self.page_data:
            logger.info("No data to save")
            return
            
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Saving crawled data to {output_dir}...")
        
        # Save a summary file with all URLs and titles
        summary_path = os.path.join(output_dir, "summary.txt")
        with open(summary_path, "w", encoding="utf-8") as f:
            for i, page in enumerate(self.page_data):
                f.write(f"{i+1}. {page['title']}\n")
                f.write(f"   URL: {page['url']}\n")
                f.write(f"   Content length: {len(page.get('html', ''))} chars\n")
                f.write("\n")
        
        # Save each page's content to separate files - raw HTML and text
        for i, page in enumerate(self.page_data):
            # Create a safe filename from the URL
            safe_name = re.sub(r'[^\w\-_.]', '_', page['url'])
            safe_name = safe_name[-100:] if len(safe_name) > 100 else safe_name
            
            # Save the complete raw HTML
            html_file_path = os.path.join(output_dir, f"{i+1}_{safe_name}.txt")
            with open(html_file_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {page['url']}\n")
                f.write(f"Title: {page['title']}\n")
                f.write("="*80 + "\n\n")
                f.write(page.get('content', page.get('html', '')))
            
            # # Also save a text file for basic reading/searching
            # text_file_path = os.path.join(output_dir, f"{i+1}_{safe_name}.txt")
            # with open(text_file_path, "w", encoding="utf-8") as f:
            #     f.write(f"URL: {page['url']}\n")
            #     f.write(f"Title: {page['title']}\n")
            #     f.write("="*80 + "\n\n")
            #     f.write(page['content'])
        
        # Save a JSON index of all crawled pages
        json_path = os.path.join(output_dir, "crawled_index.json")
        with open(json_path, "w", encoding="utf-8") as f:
            # Create a simplified index without the large HTML content
            pages_data = []
            for i, page in enumerate(self.page_data):
                safe_name = re.sub(r'[^\w\-_.]', '_', page['url'])
                safe_name = safe_name[-100:] if len(safe_name) > 100 else safe_name
                filename = f"{i+1}_{safe_name}"
                pages_data.append({
                    "url": page["url"],
                    "title": page["title"],
                    "filename": filename
                })
            
            index = {
                "site_type": self.site_type,
                "base_url": next(iter(self.page_data), {}).get('url', ''),
                "pages": pages_data,
                "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_pages": len(self.page_data)
            }
            json.dump(index, f, indent=2, ensure_ascii=False)
                
        logger.info(f"Saved {len(self.page_data)} pages to {output_dir}")
        logger.info(f"- HTML files containing complete raw page content")
        logger.info(f"- Text files for basic reading")
        logger.info(f"- JSON index of all crawled pages")
    
    def close(self):
        """Close the browser and clean up resources."""
        if self.driver:
            logger.info("Closing browser...")
            self.driver.quit()
            self.driver = None
    
    def authenticate_and_navigate(self, url):
        """Complete authentication flow and navigate to target URL."""
        try:
            if not self.driver:
                self.setup_driver()
                
            # First navigate to trigger SSO
            self.driver.get(url)
            
            # Login
            if self.login():
                # Navigate back to target page
                title = self.navigate_to(url)
                return title
            else:
                return None
        except Exception as e:
            logger.warning(f"Error during authentication: {e}")
            return None
        
    def __enter__(self):
        """Context manager entry point."""
        self.setup_driver()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit point."""
        self.close()


class CERNSSOScraper(SSOScraper):
    """A scraper to handle CERN SSO authentication and page navigation."""
    
    def get_username_from_env(self):
        """Get CERN SSO username from environment variables."""
        return read_secret("SSO_USERNAME")

    def get_password_from_env(self):
        """Get CERN SSO password from environment variables."""
        return read_secret("SSO_PASSWORD")

    def login(self):
        """Login to CERN SSO with the provided credentials."""
        if not self.username or not self.password:
            raise ValueError("Missing credentials for CERN SSO")
            
        try:
            # Wait for login form to appear
            username_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "username"))
            )
            username_input.send_keys(self.username)
            # time.sleep(1)  # Optional sleep to ensure the input is registered
            
            password_input = self.driver.find_element(By.ID, "password")
            password_input.send_keys(self.password)
            # time.sleep(1)  # Optional sleep to ensure the input is registered
            
            sign_in = self.driver.find_element(By.ID, "kc-login")
            sign_in.click()
                
            logger.info("Login credentials submitted")
            return True
        except Exception as e:
            logger.error(f"Error during login: {e}")
            return False