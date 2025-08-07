from git import Repo
import os
import shutil
import urllib.parse
import json

git_url = 'https://gitlab.cern.ch/cmsdmops/Documentation.git'
site_url = 'https://cmsdmops.docs.cern.ch'

documentation_path = os.getcwd()+'/cmsdmops-doc'

def create_unified_doc_from_gitlab(git_url,site_url,documentation_path):
    urls = []
    if (not os.path.exists(documentation_path)) or (len(os.listdir(documentation_path))==0):
        Repo.clone_from(git_url,documentation_path)

    output_file = os.getcwd()+'/dmops.md'

    with open(output_file,'w',encoding='utf-8') as outfile:
        for root, _, files in os.walk(documentation_path):
            #print(root,a,files)
            for file in files:
                if file.endswith('.md'):
                    if not (file.startswith('README') or file.startswith('index')):
                        url = site_url+root.replace(documentation_path+'/docs','')+'/'+file.replace(' ','%20').replace('.md','')
                        urls.append(url)
                    full_path = root+'/'+file
                    with open(full_path, 'r', encoding='utf-8') as infile:
                        content = infile.read()
                        outfile.write(content)
                        outfile.write("\n\n---\n\n")

    shutil.rmtree(documentation_path)
    return urls

#doc_urls = create_unified_doc_from_gitlab(git_url,site_url,documentation_path)
#print(doc_urls)

import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC



username = ''
password = ''
url = 'https://cmspnr.docs.cern.ch'

driver = webdriver.Chrome()
driver.get(url)
print(f"Navigated to {url}")
print(f"Page title: {driver.title}")


username_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "username"))
            )
username_input.send_keys(username)

password_input = driver.find_element(By.ID, "password")
password_input.send_keys(password)

sign_in = driver.find_element(By.ID, "kc-login")
sign_in.click()

time.sleep(1)  # Optional sleep to ensure the input is registered
print(f"Login successfully")
print(f"Page title: {driver.title}")

anchors = driver.find_elements(By.CSS_SELECTOR, ".md-nav__link, .md-content a")

base_hostname = urllib.parse.urlparse(url).netloc
urls = []
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
                urls.append(normalized_url)
    except Exception as e:
        print(f"Error extracting link: {e}")

urls = list(set(urls))
pages_data = []

for current_url in urls:
    print(f"Crawling page {current_url}")
    driver.get(current_url)
    title = driver.title
    text_content = driver.find_element(By.TAG_NAME, "body").text
    html_content = driver.page_source
    page_data = {
            "url": current_url,
            "title": title,
            "content": text_content,
            #"html": html_content
        }
    print(f"Extracted data from {current_url} ({len(page_data['content'])} chars)")
    pages_data.append(page_data)

output_dir = os.getcwd()
json_path = os.path.join(output_dir, "crawled_index.json")
with open(json_path, "w", encoding="utf-8") as f:
    for page_data in pages_data:
        json.dump(page_data, f, indent=2, ensure_ascii=False)