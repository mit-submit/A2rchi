from git import Repo
import os
import yaml
import re
import hashlib
import shutil

from a2rchi.utils.scraper import Scraper
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

class GitScraper(Scraper):
    """Generic base class for a Git-based scraper."""

    def __init__(self) -> None:
        super().__init__()

        # create sub-directory for git repositories if it doesn't exist
        self.git_dir = os.path.join(self.data_path, "git")
        os.makedirs(self.git_dir, exist_ok=True)

        try:
            self.git_username = read_secret("GIT_USERNAME")
            self.git_token = read_secret("GIT_TOKEN")
        except FileNotFoundError:
            raise FileNotFoundError("Git Personal Access Token (PAT) not found. Please set it up in your environment.")
        
        

    def _parse_url(self, url) -> dict:

        branch_name = None

        regex_repo_name = r'(?:github|gitlab)\.[\w.]+\/[^\/]+\/(\w+)(?:\.git|\/|$)'
        if match := re.search(regex_repo_name,url,re.IGNORECASE):
            repo_name = match.group(1)
        else:
            raise ValueError(f"The git url {url} does not match the expected format.")
        

        if 'gitlab' in url:
            clone_from_url = url.replace('gitlab',f'{self.git_username}:{self.git_token}@gitlab')
        elif 'github' in url:
            clone_from_url = url.replace('github',f'{self.git_username}:{self.git_token}@github')

        if '/tree/' in clone_from_url:
            # A specific branch was cloned
            branch_name = clone_from_url.split('/tree/')[1]
            clone_from_url = clone_from_url.split('/tree/')[0]

        return {'original_url':url,'clone_url':clone_from_url,'repo_name':repo_name,'branch':branch_name}
    
    def _clone_repo(self,url_dict):

        original_url = url_dict['original_url']
        clone_url = url_dict['clone_url']
        branch = url_dict['branch']
        repo_name = url_dict['repo_name']

        logger.info(f"Cloning repository {repo_name}...")
        
        repo_path = os.path.join(self.git_dir,repo_name)
        
        try:
            # Clone https git url into folder
            if branch is None:
                Repo.clone_from(clone_url, repo_path)
            else:
                Repo.clone_from(clone_url, repo_path,branch)
        except Exception as e:
            raise Exception(f'Repo could not be cloned as per error {str(e)}')
        
        # Should only work for MKDocs based Git repos
        if not os.path.exists(repo_path+'/mkdocs.yml') :
            logger.info(f"Skipping url {original_url}...\nThis repository does not contain mkdocs.yml")
            # Cleaning after skipping
            shutil.rmtree(repo_path,ignore_errors=True)
            raise ValueError(f"Repository is not MKDocs-based")
            
        
        return repo_path

    def hard_scrape(self, verbose=False):
        """
        Fills the data folder from scratch 
        
        """
        # clear website data if specified
        if self.config["reset_data"] :
            for file in os.listdir(self.git_dir):
                if os.path.isfile(file):
                    os.remove(os.path.join(self.git_dir, file))
                else:
                    shutil.rmtree(os.path.join(self.git_dir, file),ignore_errors=True)

        git_urls = super().collect_urls_from_lists()
        git_urls = [re.split("git-", git_url)[1] for git_url in git_urls if git_url.startswith('git-')]
        
        sources_path=os.path.join(self.data_path, 'sources.yml')

        # Scrape urls
        self.scrape_urls(urls=git_urls,upload_dir=self.git_dir,sources_path=sources_path)


        if verbose:
            logger.info("Git scraping was completed successfully")


    def scrape_urls(self,urls,upload_dir,sources_path):
        logger.debug(f"SOURCE: {sources_path}")
        try:
            # load existing sources or initialize as empty dictionary
            with open(sources_path, 'r') as file:
                sources = yaml.safe_load(file) or {}
        except FileNotFoundError:
            sources = {}

        for url in urls:

            # Dictionary with clone_url, repo_name, branch_name (if any)
            url_dict = self._parse_url(url) 

            
            try:
                repo_path = self._clone_repo(url_dict=url_dict)
                logger.info(f"Succesfully cloned repo to path: {repo_path}")
            except ValueError as e:
                continue

            # Obtains site url from the mkdocs yaml configuration
            with open(repo_path+'/mkdocs.yml', 'r') as file:
                data = yaml.safe_load(file)
                base_site_url = data['site_url']
            logger.info(f"Site base url: {base_site_url}")

            for root, _, files in os.walk(repo_path+'/docs'):
                for file in files:
                    # Only crawls .md files
                    if '.md' in file:
                        temp_file_path = root+'/'+file #File path within repo folder
                        logger.info(f"Writing MD doc associated with page: {base_site_url+temp_file_path.split('/docs/')[-1].replace('.md','')}")
                        file_name, current_url, content = self.write_page_data(temp_file_path,base_site_url,upload_dir)
                        sources[file_name] = current_url
                        logger.debug(f"TEXT({current_url})\n{content}\n")

            shutil.rmtree(repo_path,ignore_errors=True)

        # store list of files with urls to file 
        with open(sources_path, 'w') as file:
            yaml.dump(sources, file)

    def write_page_data(self, file_path, base_url, upload_dir):
        current_url = base_url+file_path.split('/docs/')[-1].replace('.md','')

        # Read the page and store the text only to file
        identifier = hashlib.md5()
        identifier.update(current_url.encode('utf-8'))
        file_name = str(int(identifier.hexdigest(),16))[0:12]
        logger.info(f"Store: {upload_dir}/{file_name}.html : {current_url}")


        #title = file_path.split('/')[-1].replace('.md','')

        with open(file_path, 'r', encoding='utf-8') as file:
            text_content = file.read()

        with open(f"{upload_dir}/{file_name}.txt", 'w') as file:
            file.write(text_content)

        return file_name, current_url, text_content