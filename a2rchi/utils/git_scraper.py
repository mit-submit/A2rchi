from git import Repo
import os
import yaml
import re
import json
import time

from a2rchi.utils.env import read_secret

class GitScraper():
    """Generic base class for Git-based scrapers."""

    def __init__(self, git_url, authentication=True) -> None:
        self.git_url = git_url
        
        try:
            # Create cloned_repos folder; this will store the clone repositories
            script_dir = os.path.dirname(os.path.abspath(__file__))
            base_dir = os.path.join(script_dir, "cloned_repos")
            os.makedirs(base_dir, exist_ok=True)
            repo_name = self.git_url.split('/')[-2]+'-'+self.git_url.split('/')[-1].replace('.git', '')
            self.repo_path = os.path.join(base_dir, repo_name)
        except Exception as e:
            raise Exception(f"Error creating cloned_repos for {self.git_url} - {str(e)}")
        
        if authentication:
            try:
                self.git_username = read_secret("GIT_USERNAME")
                self.git_token = read_secret("GIT_TOKEN")
                if 'gitlab' in self.git_url:
                    self.git_url = self.git_url.replace('gitlab',f'{self.git_username}:{self.git_token}@gitlab')
                elif 'github' in self.git_url:
                    self.git_url = self.git_url.replace('github',f'{self.git_username}:{self.git_token}@github')
                else:
                    raise ValueError(f'The repository must be GitLab or GitHub based.')
            except FileNotFoundError:
                raise FileNotFoundError("Git username or password. not found Please set it up in your environment.")
            

    def clone_repo(self):
        try:
            # Clone https git url into folder
            Repo.clone_from(self.git_url,self.repo_path)
        except Exception as e:
            raise Exception(f'Repo could not be cloned as per error {str(e)}')
    
    def crawl(self):

        # Clones the repo if it has not done so already
        if not os.path.exists(self.repo_path):
            self.clone_repo()

        # Should only work for MKDocs based Git repos
        if not os.path.exists(self.repo_path+'/mkdocs.yml'):
            raise ValueError("This repository does not contain mkdocs.yml")

        # Reset crawling state
        self.page_data = []

        # Obtains site url from the mkdocs yaml configuration
        with open(self.repo_path+'/mkdocs.yml', 'r') as file:
            data = yaml.safe_load(file)
            self.site_url = data['site_url']
        print(f"Site url: {self.site_url}")

        if not os.path.exists(self.repo_path+'/docs'):
            raise ValueError("The repository does not contain a 'docs' folder.")
        
        for root, _, files in os.walk(self.repo_path+'/docs'):
            for file in files:
                # Only crawls .md files
                if '.md' in file:
                    file_path = root+'/'+file
                    print(f"Crawling MD doc associated with page: {self.site_url+file_path.split('/docs/')[-1].replace('.md','')}")
                    page_data = self.extract_page_data(file_path)
                    self.page_data.append(page_data)

        return self.page_data

    def extract_page_data(self, file_path):
        title = file_path.split('/')[-1].replace('.md','')
        with open(file_path, 'r', encoding='utf-8') as file:
            text_content = file.read()
        url = self.site_url+file_path.split('/docs/')[-1].replace('.md','')#DE DOCS EN ADELANTE

        return {
            "url": url,
            "title": title,
            "content": text_content
        }
                

    def save_crawled_data(self, output_dir="crawled_data"):
        if not self.page_data:
            print("No data to save")
            return
            
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        print(f"Saving crawled data to {output_dir}...")

        # Save a summary file with all URLs and titles
        summary_path = os.path.join(output_dir, "summary.txt")
        with open(summary_path, "w", encoding="utf-8") as f:
            for i, page in enumerate(self.page_data):
                f.write(f"{i+1}. {page['title']}\n")
                f.write(f"   URL: {page['url']}\n")
                f.write(f"   Content length: {len(page.get('content', ''))} chars\n")
                f.write("\n")

        # Save each page's content to separate files - raw MD content and text
        for i, page in enumerate(self.page_data):
            # Create a safe filename from the URL
            safe_name = re.sub(r'[^\w\-_.]', '_', page['url'])
            safe_name = safe_name[-100:] if len(safe_name) > 100 else safe_name
            
            # Save the complete raw MD content
            md_file_path = os.path.join(output_dir, f"{i+1}_{safe_name}.txt")
            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {page['url']}\n")
                f.write(f"Title: {page['title']}\n")
                f.write("="*80 + "\n\n")
                f.write(page.get('content', page.get('content', '')))

        # Save a JSON index of all crawled pages
        json_path = os.path.join(output_dir, "crawled_index.json")
        with open(json_path, "w", encoding="utf-8") as f:
            # Create a simplified index without the large MD content
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
                "base_url": next(iter(self.page_data), {}).get('url', ''),
                "pages": pages_data,
                "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_pages": len(self.page_data)
            }
            json.dump(index, f, indent=2, ensure_ascii=False)
                
        print(f"Saved {len(self.page_data)} pages to {output_dir}")
        print(f"- MD files containing complete raw page content")
        print(f"- Text files for basic reading")
        print(f"- JSON index of all crawled pages")