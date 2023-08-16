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
        self.config = Config_Loader().config["chains"]["chain"]
        self.global_config = Config_Loader().config["global"]

        # Check if target folders exist 
        if not os.path.isdir(self.global_config["DATA_PATH"]):
                os.mkdir(self.global_config["DATA_PATH"])

        self.websites_dir = self.global_config["DATA_PATH"]+"websites/"
        if not os.path.isdir(self.websites_dir):
                os.mkdir(self.websites_dir)
                with open(self.websites_dir+"info.txt", 'w') as file:
                    file.write("This is the folder for uploading the information from websites")

        ### Not needed anymore but could be used in the future
        # self.github_dir = self.global_config["DATA_PATH"]+"github/"
        # if not os.path.isdir(self.github_dir):
        #         os.mkdir(self.github_dir)
        #         with open(self.github_dir+"info.txt", 'w') as file:
        #             file.write("This is the folder for uploading the users guide from github")

        self.input_lists = Config_Loader().config["chains"]["input_lists"]
        print("input lists: ", self.input_lists)

    def hard_scrape(self,verbose=False):
        """
        (Re)fills the data folder from scratch 
        
        """
        self.scrape_urls()
        if verbose: print("Scraping was completed successfully")

    def collect_urls_from_lists(self):
        urls = []
        for list_name in self.input_lists:
            with open(f"config/{list_name}","r") as f:
                data = f.read()
            for line in data.split("\n"):
                if len(line)>0 and line[0] != '#':
                    urls.append(line)
        return urls
            
    def scrape_urls(self):
        urls = self.collect_urls_from_lists()
        sources = {}
        for url in urls:
            # request web page
            resp = requests.get(url, verify=False)
            # get the response text. in this case it is HTML
            html = resp.text
            # write the html output to a file
            identifier = hashlib.md5()
            identifier.update(url.encode('utf-8'))
            file_name = str(int(identifier.hexdigest(),16))[0:12]
            with open(f"{self.websites_dir}/{file_name}.html", 'w') as file:
                file.write(html)
            sources[file_name] = url 

        #store list of files with urls to file 
        file_name = self.global_config["DATA_PATH"]+'sources.yml'
        with open(file_name, 'w') as file:
            yaml.dump(sources, file)

    def scrape_rst_files(self,url,raw_url):
        """
        DEPRECATED
         
        Function suited for scraping github files. Will require generalization for other types of inputs

        """
        response = requests.get(url)
        if response.status_code == 200:
            #file_links = re.findall(r'<a[^>]*href="([^"]+\.rst)"[^>]*>', response.text)    
            file_links = re.findall(r'/[^"]+\.rst', response.text)
            # print("response is: ", response.text)
            print("file links are: ", file_links)
            for file_url in file_links:
                file_url = (raw_url + file_url).replace('/blob', '')
                print("file url is ", file_url)
                file_response = requests.get(file_url)
                if file_response.status_code == 200:
                    file_content = file_response.text
                    file_name = file_url.rsplit("/", 1)[-1]
                    
                    # write the file:
                    with open(self.github_dir+file_name[:-4]+".txt", 'w') as file:
                        file.write(file_content)
                else:
                    print(f"Error downloading {file_url}: {file_response.status_code}")
        else:
            print(f"Error: {response.status_code}")

# deprecated
    def scrape_submit_files(self):
        for web_title in self.urls.keys():
            # request web page
            resp = requests.get(self.urls[web_title], verify=False)
            # get the response text. in this case it is HTML
            html = resp.text
            #write the html output to a file
            with open(self.websites_dir+web_title+".html", 'w') as file:
                file.write(html)


# Example usage
# s = Scraper()
# url,raw_url = s.github_url,s.raw_url
# s.scrape_rst_files(url,raw_url)
# s.scrape_all()
