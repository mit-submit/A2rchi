import requests
import hashlib
import re
import os
import sys
import yaml

#clears the ssl certificates to allow web scraping
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

class Scraper():
    
    def __init__(self):
        from config_loader import Config_Loader
        self.config = Config_Loader().config["utils"]["scraper"]
        self.global_config = Config_Loader().config["global"]

        # check if target folders exist 
        if not os.path.isdir(self.global_config["DATA_PATH"]):
                os.mkdir(self.global_config["DATA_PATH"])

        self.websites_dir = self.global_config["DATA_PATH"]+"websites/"
        if not os.path.isdir(self.websites_dir):
                os.mkdir(self.websites_dir)
        if not os.path.isfile(self.websites_dir+"info.txt"):
            with open(self.websites_dir+"info.txt", 'w') as file:
                file.write("This is the folder for uploading the information from websites")

        self.input_lists = Config_Loader().config["chains"]["input_lists"]
        print("input lists: ", self.input_lists)

    def hard_scrape(self,verbose=False):
        """
        Fills the data folder from scratch 
        
        """
        if self.config["reset_data"] :
            for file in os.listdir(self.websites_dir):
                if (file == "info.txt"): continue
                os.remove(self.websites_dir + file)
                
        Scraper.scrape_urls(urls = self.collect_urls_from_lists(),
                            upload_dir= self.websites_dir,
                            sources_path=self.global_config["DATA_PATH"]+'sources.yml',
                            verify_urls=self.config["verify_urls"],
                            enable_warnings=self.config["enable_warnings"])
        if verbose:
            print("Scraping was completed successfully")

    def collect_urls_from_lists(self):
        urls = []
        for list_name in self.input_lists:
            with open(f"config/{list_name}","r") as f:
                data = f.read()
            for line in data.split("\n"):
                if len(line)>0 and line[0] != '#':
                    urls.append(line)
        return urls
    
    @staticmethod
    def scrape_urls(urls, upload_dir, sources_path, verify_urls, enable_warnings):
        print(" SOURCE: ",sources_path)
        try:
            with open(sources_path, 'r') as file:
                sources = yaml.safe_load(file) or {}  # load existing sources or initialize as empty dictionary
        except FileNotFoundError:
            sources = {}
            
        for url in urls:
            # request web page
            if not enable_warnings:
                import urllib3
                urllib3.disable_warnings()
            resp = requests.get(url, verify = verify_urls)
            # write the html output to a file
            identifier = hashlib.md5()
            identifier.update(url.encode('utf-8'))
            file_name = str(int(identifier.hexdigest(),16))[0:12]

            if (url.split('.')[-1] == 'pdf'):
                with open(f"{upload_dir}/{file_name}.pdf : {url}", 'wb') as file:
                    file.write(resp.content)
            else:
                print(f" Store: {upload_dir}/{file_name}.html : {url}")
                with open(f"{upload_dir}/{file_name}.html", 'w') as file:
                    file.write(resp.text)
            
            sources[file_name] = url 

        # store list of files with urls to file 
        file_name = sources_path
        with open(file_name, 'w') as file:
            yaml.dump(sources, file)
