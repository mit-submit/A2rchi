import hashlib
import os
import re
import requests
import ssl
import yaml
from .sso_scraper import CERNSSOScraper
#clears the ssl certificates to allow web scraping
ssl._create_default_https_context = ssl._create_unverified_context


class Scraper():
    
    def __init__(self, piazza_email=None, piazza_password=None):
        # fetch configs
        from a2rchi.utils.config_loader import Config_Loader
        self.config = Config_Loader().config["utils"]["scraper"]
        self.global_config = Config_Loader().config["global"]
        self.piazza_config = Config_Loader().config["utils"].get("piazza", None)
        self.data_path = self.global_config["DATA_PATH"]

        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True)

        # create sub-directory for websites if it doesn't exist
        self.websites_dir = os.path.join(self.data_path, "websites")
        os.makedirs(self.websites_dir, exist_ok=True)

        self.input_lists = Config_Loader().config["chains"]["input_lists"]
        print(f"input lists: {self.input_lists}")

        # # log in to piazza
        # if self.piazza_config is not None:
        #     # create sub-directory for piazza if it doesn't exist
        #     self.piazza_dir = os.path.join(self.data_path, "piazza")
        #     os.makedirs(self.piazza_dir, exist_ok=True)

        #     self.piazza = Piazza()
        #     self.piazza.user_login(email=piazza_email, password=piazza_password)
        #     self.piazza_net = self.piazza.network(self.piazza_config["network_id"])


    def piazza_scrape(self, verbose=False):
        # clear piazza data if specified
        if self.config["reset_data"] :
            for file in os.listdir(self.piazza_dir):
                os.remove(os.path.join(self.piazza_dir, file))

        # iterate over resolved messages and structure them as:
        # [("User", Q), ("User", A), ("Expert", A), ("User", F), ("Expert", F), etc.]
        unresolved_posts = Scraper.scrape_piazza(
            upload_dir=self.piazza_dir,
            sources_path=os.path.join(self.data_path, 'sources.yml'),
        )

        if verbose:
            print("Piazza scraping was completed successfully")

        return unresolved_posts


    def hard_scrape(self, verbose=False):
        """
        Fills the data folder from scratch 
        
        """
        # clear website data if specified
        if self.config["reset_data"] :
            for file in os.listdir(self.websites_dir):
                os.remove(os.path.join(self.websites_dir, file))

        # scrape URLs
        Scraper.scrape_urls(
            urls=self.collect_urls_from_lists(),
            upload_dir=self.websites_dir,
            sources_path=os.path.join(self.data_path, 'sources.yml'),
            verify_urls=self.config["verify_urls"],
            enable_warnings=self.config["enable_warnings"],
        )

        if verbose:
            print("Web scraping was completed successfully")


    def collect_urls_from_lists(self):
        urls = []
        for list_name in self.input_lists:
            with open(os.path.join("weblists", os.path.basename(list_name)), "r") as f:
                data = f.read()

            for line in data.split("\n"):
                if len(line.lstrip())>0 and line.lstrip()[0:1] != "#":
                    urls.append(line)

        return urls
    
    @staticmethod
    def scrape_urls(urls, upload_dir, sources_path, verify_urls, enable_warnings):
        print(f" SOURCE: {sources_path}")
        try:
            # load existing sources or initialize as empty dictionary
            with open(sources_path, 'r') as file:
                sources = yaml.safe_load(file) or {}
        except FileNotFoundError:
            sources = {}

        for url in urls:
            # disable warnings if not specified
            if not enable_warnings:
                import urllib3
                urllib3.disable_warnings()

            if url.startswith("sso-"):
                # split to get the url 
                url = re.split("sso-", url)[1]
                try:
                    # Now using Firefox-based SSO scraper
                    with CERNSSOScraper(headless=True) as sso_scraper:
                        crawled_data = sso_scraper.crawl(url, max_pages=1000)
                        sso_scraper.save_crawled_data(upload_dir)
                        
                        for i, page in enumerate(crawled_data):
                            print(f"SSO Crawled {i+1}. {page['title']} - {page['url']}")
                            identifier = hashlib.md5()
                            identifier.update(page['url'].encode('utf-8'))
                            file_name = str(int(identifier.hexdigest(), 16))[0:12]
                            sources[file_name] = page['url'] 
                except Exception as e:
                    print(f"SSO scraping failed for {url}: {str(e)}")
                    print("Falling back to regular HTTP request...")
                    try:
                        # request web page without SSO
                        resp = requests.get(url, verify=verify_urls)
                    except Exception as e2:
                        print(f"Regular request also failed for {url}: {str(e2)}")
            else:

                # request web page
                resp = requests.get(url, verify=verify_urls)

                # write the html output to a file
                identifier = hashlib.md5()
                identifier.update(url.encode('utf-8'))
                file_name = str(int(identifier.hexdigest(), 16))[0:12]

                if (url.split('.')[-1] == 'pdf'):
                    print(f" Store: {upload_dir}/{file_name}.pdf : {url}")
                    with open(f"{upload_dir}/{file_name}.pdf", 'wb') as file:
                        file.write(resp.content)
                else:
                    print(f" Store: {upload_dir}/{file_name}.html : {url}")
                    with open(f"{upload_dir}/{file_name}.html", 'w') as file:
                        file.write(resp.text)

                sources[file_name] = url 

        # store list of files with urls to file 
        with open(sources_path, 'w') as file:
            yaml.dump(sources, file)


    @staticmethod
    def scrape_piazza(upload_dir, sources_path):
        print(f" SOURCE: {sources_path}")
        try:
            # load existing sources or initialize as empty dictionary
            with open(sources_path, 'r') as file:
                sources = yaml.safe_load(file) or {}
        except FileNotFoundError:
            sources = {}

        # get generator for all posts
        unresolved_posts = []
        posts = self.piazza_net.iter_all_posts(sleep=1.5)
        for post in posts:
            # add post to unresolved posts if it has no answer or an unresolved followup
            if post.get("no_answer", False) or post.get("no_answer_followup", False):
                unresolved_posts.append(int(post["nr"]))
                continue

            # otherwise 


        for url in urls:
            # disable warnings if not specified
            if not enable_warnings:
                import urllib3
                urllib3.disable_warnings()

            # request web page
            resp = requests.get(url, verify=verify_urls)

            # write the html output to a file
            identifier = hashlib.md5()
            identifier.update(url.encode('utf-8'))
            file_name = str(int(identifier.hexdigest(), 16))[0:12]

            if (url.split('.')[-1] == 'pdf'):
                print(f" Store: {upload_dir}/{file_name}.pdf : {url}")
                with open(f"{upload_dir}/{file_name}.pdf", 'wb') as file:
                    file.write(resp.content)
            else:
                print(f" Store: {upload_dir}/{file_name}.html : {url}")
                with open(f"{upload_dir}/{file_name}.html", 'w') as file:
                    file.write(resp.text)

            sources[file_name] = url 

        # store list of files with urls to file 
        with open(sources_path, 'w') as file:
            yaml.dump(sources, file)
