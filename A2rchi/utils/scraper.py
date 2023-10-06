import hashlib
import os
import re
import requests
import ssl
import yaml

#clears the ssl certificates to allow web scraping
ssl._create_default_https_context = ssl._create_unverified_context


class Scraper():
    
    def __init__(self):
        # fetch configs
        from A2rchi.utils.config_loader import Config_Loader
        self.config = Config_Loader().config["utils"]["scraper"]
        self.global_config = Config_Loader().config["global"]
        self.data_path = self.global_config["DATA_PATH"]

        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True)

        # create sub-directory for websites if it doesn't exist
        self.websites_dir = os.path.join(self.data_path, "websites")
        os.makedirs(self.websites_dir, exist_ok=True)

        self.input_lists = Config_Loader().config["chains"]["input_lists"]
        print(f"input lists: {self.input_lists}")


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
            print("Scraping was completed successfully")


    def collect_urls_from_lists(self):
        urls = []
        for list_name in self.input_lists:
            with open(f"config/{list_name}", "r") as f:
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
