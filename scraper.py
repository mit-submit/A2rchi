from bs4 import BeautifulSoup
import requests
import re

#clears the ssl certificates to allow web scraping
import ssl
ssl._create_default_https_context = ssl._create_unverified_context


#urls to query from
urls =  {"home":"https://submit.mit.edu",
            "about":"https://submit.mit.edu/?page_id=6",
            "contact":"https://submit.mit.edu/?page_id=7",
            "news":"https://submit.mit.edu/?page_id=8",
            "people":"https://submit.mit.edu/?page_id=73"}

for web_title in urls.keys():

    # request web page
    resp = requests.get(urls[web_title], verify=False)

    # get the response text. in this case it is HTML
    html = resp.text

    #write the html output to a file
    with open('data/submit_website/'+web_title+".html", 'w') as file:
        file.write(html)

def scrape_rst_files(url):
    response = requests.get(url)
    if response.status_code == 200:
        #file_links = re.findall(r'<a[^>]*href="([^"]+\.rst)"[^>]*>', response.text)    
        file_links = re.findall(r'/[^"]+\.rst', response.text)
        print("response is: ", response.text)
        print("file links are: ", file_links)
        for file_url in file_links:
            file_url = ("https://raw.githubusercontent.com/mit-submit/submit-users-guide/main/source" + file_url).replace('/blob', '')
            print("file url is ", file_url)
            file_response = requests.get(file_url)
            if file_response.status_code == 200:
                file_content = file_response.text
                file_name = file_url.rsplit("/", 1)[-1]
                
                #write the file:
                with open('data/github/'+file_name[:-4]+".txt", 'w') as file:
                    file.write(file_content)
            else:
                print(f"Error downloading {file_url}: {file_response.status_code}")
    else:
        print(f"Error: {response.status_code}")

# Example usage
github_url = 'https://github.com/mit-submit/submit-users-guide/tree/main/source'
scrape_rst_files(github_url)
