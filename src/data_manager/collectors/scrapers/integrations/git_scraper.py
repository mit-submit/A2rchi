import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple
from datetime import datetime

from mkdocs.utils.yaml import yaml_load
from git import Repo, Commit

from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from src.data_manager.collectors.scrapers.scraper_manager import \
        ScraperManager


class GitScraper:
    """Scraper integration that clones Git repositories and indexes MkDocs sites."""

    def __init__(self, manager: "ScraperManager") -> None:
        self.manager = manager
        self.config = manager.config
        self.data_path = manager.data_path
        self.git_dir = self.data_path / "git"
        self.git_dir.mkdir(parents=True, exist_ok=True)

        self.git_username = read_secret("GIT_USERNAME")
        self.git_token = read_secret("GIT_TOKEN")
        self._credentials_available = bool(self.git_username and self.git_token)
        if not self._credentials_available:
            logger.info("No git credentials supplied; git scraping will be skipped.")

    def collect(self, git_urls: List[str]) -> List[ScrapedResource]:
        if not self._credentials_available or not git_urls:
            return []

        harvested: List[ScrapedResource] = []

        for url in git_urls:
            try:
                repo_path, base_site_url = self._prepare_repository(url)
            except ValueError as exc:
                logger.info(f"{exc}")
                continue
            except Exception as exc:
                logger.error(f"Failed to clone {url}: {exc}")
                continue

            try:
                harvested.extend(
                    self._harvest_repository(repo_path, base_site_url)
                )
            finally:
                shutil.rmtree(repo_path, ignore_errors=True)

        if harvested:
            logger.info("Git scraping was completed successfully")

        return harvested

    def _prepare_repository(self, url: str) -> Tuple[Path, str]:
        url_dict = self._parse_url(url)
        repo_path = self._clone_repo(url_dict)
        try:
            with (repo_path / "mkdocs.yml").open("r") as file:
                data = yaml_load(file)
            base_site_url = data["site_url"] if data["site_url"][-1]=="/" else data["site_url"]+"/"
        except Exception:
            if "rucio" in url_dict["clone_url"]:
                base_site_url = "https://rucio.cern.ch/documentation/"
            elif "fts" in url_dict["clone_url"]:
                base_site_url = "https://fts3-docs.web.cern.ch/fts3-docs/"
        logger.info(f"Site base url: {base_site_url}")
        return repo_path, base_site_url

    def _harvest_repository(
        self, repo_path: Path, base_site_url: str
    ) -> List[ScrapedResource]:
        docs_dir = repo_path / "docs"
        if not docs_dir.exists():
            logger.info(f"Skipping repository {repo_path}; docs directory not found")
            return []

        resources: List[ScrapedResource] = []
        repo = Repo(repo_path)
        for markdown_path in docs_dir.rglob("*.md"):
            current_url = base_site_url + \
                markdown_path.relative_to(docs_dir).with_suffix("").as_posix()

            logger.info(f"Indexing Git doc: {current_url}")
            text_content = markdown_path.read_text(encoding="utf-8")
            created_at = self._get_creation_date(repo=repo, file_path=markdown_path)
            last_updated_at = self._get_last_updated_date(repo=repo, file_path=markdown_path)
            resource = ScrapedResource(
                url=current_url,
                content=text_content,
                suffix="txt",
                source_type="git",
                metadata={
                    "path": str(markdown_path.relative_to(repo_path)),
                    "title": str(markdown_path).split('/')[-1].replace('.md','').title(),
                    "created_at": created_at.strftime('%Y-%m-%dT%H:%M:%S.%f%z'),
                    "updated_at": last_updated_at.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
                },
            )
            if len(resource.content)>0:
                resources.append(resource)
            else:
                logger.info(f"Resource {current_url} is empty. Skipping...")

        return resources

    def _parse_url(self, url: str) -> dict:
        branch_name = None

        regex_repo_name = r"(?:github|gitlab)\.[\w.]+\/[^\/]+\/([\w.-]+)(?:\.git|\/)$"
        match = re.search(regex_repo_name, url, re.IGNORECASE)
        if not match:
            raise ValueError(f"The git url {url} does not match the expected format.")

        repo_name = match.group(1)

        if "gitlab" in url:
            clone_from_url = url.replace("gitlab", f"{self.git_username}:{self.git_token}@gitlab")
        elif "github" in url:
            clone_from_url = url#.replace("github", f"{self.git_username}:{self.git_token}@github")
        else:
            raise ValueError(f"Unsupported git host in url {url}")

        if "/tree/" in clone_from_url:
            branch_name = clone_from_url.split("/tree/")[1]
            clone_from_url = clone_from_url.split("/tree/")[0]

        return {
            "original_url": url,
            "clone_url": clone_from_url,
            "repo_name": repo_name,
            "branch": branch_name,
        }

    def _clone_repo(self, url_dict: dict) -> Path:
        original_url = url_dict["original_url"]
        clone_url = url_dict["clone_url"]
        branch = url_dict["branch"]
        repo_name = url_dict["repo_name"]

        logger.info(f"Cloning repository {repo_name}...")

        repo_path = self.git_dir / repo_name
        if branch is None:
            Repo.clone_from(clone_url, repo_path)
        else:
            Repo.clone_from(clone_url, repo_path, branch)

        return repo_path

    def _get_creation_date(self, repo: Repo, file_path: str) -> datetime:
        """Retrieves the commit date of the very first commit that introduced the file."""
        try:
            commits = list(repo.iter_commits(paths=file_path))
            
            if not commits:
                return None # File not found

            first_commit: Commit = commits[-1]
            
            if not first_commit.parents:
                return first_commit.committed_datetime

            parent_commit = first_commit.parents[0]
            diffs = first_commit.diff(parent_commit, paths=[file_path])
            
            if any(d.change_type == 'A' for d in diffs):
                return first_commit.committed_datetime
            
            return first_commit.committed_datetime

        except Exception as e:
            print(f"Error retrieving creation date for {file_path}: {e}")
            return None

    def _get_last_updated_date(self, repo: Repo, file_path: str) -> datetime:
        """Retrieves the commit date of the last commit that modified the specified file."""
        try:
            last_commit: Commit = next(repo.iter_commits(paths=file_path, max_count=1))
            return last_commit.committed_datetime

        except StopIteration:
            # File not found in history
            return None
        except Exception as e:
            print(f"Error retrieving last updated date for {file_path}: {e}")
            return None
