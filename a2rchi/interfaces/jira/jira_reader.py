import jira
import logging
import os
from typing import Iterator, Optional

from a2rchi.utils.config_loader import Config_Loader
from a2rchi.utils.env import read_secret
from a2rchi.utils.anonymizer import Anonymizer


class JiraReader():
    def __init__(self) -> None:

        # TODO: Move to this to the main application later
        logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(message)s')

        try:
            print(Config_Loader().config["interfaces"])
            self.jira_config = Config_Loader().config["interfaces"]["jira"]
            self.jira_url = self.jira_config["JIRA_URL"]
            self.jira_projects = self.jira_config["JIRA_PROJECTS"]

            if not self.jira_url or not self.jira_projects:
                logging.info("JIRA configs couldn't be found. A2rchi will skip data fetching from JIRA")
                return
        except KeyError as e:
            raise KeyError(f"JIRA configs couldn't be found. A2rchi will skip data fetching from JIRA: {str(e)}")
        
        try:
            self.pat = read_secret("JIRA_PAT")
        except FileNotFoundError:
            raise FileNotFoundError("JIRA Personal Access Token (PAT) not found. Please set it up in your environment.")

        try:
            self.anonymize_data = self.jira_config.get("ANONYMIZE_DATA", True)

            self.client = self.log_in(self.pat)
            self.anonymizer = Anonymizer()

            if not self.client:
                raise Exception("No JIRA Connection!")
        except Exception as error:
            raise Exception(f"Error initializing JiraReader\n{str(error)}")

    def read_pat(self, pat_path: str) -> str:
        with open(pat_path, 'r') as f:
            pat = f.read().strip()
        return pat

    def log_in(self, pat: str) -> Optional[jira.JIRA]:
        try:
            return jira.JIRA(self.jira_url, token_auth=pat, timeout=30)
        except Exception as e:
            print("Failed to log in to JIRA: ", str(e))

    def run(self, tickets_dir: str) -> None:   
        """
        Main function to run the JIRA reader.
        """
        if self.jira_url and self.jira_projects:
            jira_data = self.fetch_jira_data()
            self.write_jira_data(tickets_dir, jira_data)
        
    def get_all_issues(self) -> Iterator[jira.Issue]:
        """
        Function to fetch all issues from the specified JIRA projects.
        """
        max_results = 100  # You can adjust this up to 1000 for JIRA Cloud
        for project in self.jira_projects:
            logging.debug(f"Fetching issues for project: {project}")
            query = f'project={project}'
            start_at = 0
            while True:
                batch = self.client.search_issues(
                    query,
                    startAt=start_at,
                    maxResults=max_results
                )
                if not batch:
                    break
                yield from (issue for issue in batch)
                if len(batch) < max_results:
                    break
                start_at += max_results

    def fetch_jira_data(self) -> Iterator[dict[str, str]]:
        """
        Fetches issues from JIRA and yields anonymized data dictionaries.
        """
        for issue in self.get_all_issues():
            issue_data = {
                'issue_id': str(issue),
                'created_at': getattr(issue.fields, "created", ""),
            }
            issue_text = f"Title: {issue}\nSummary: {getattr(issue.fields, 'summary', '')}\n"

            comments = self.client.comments(issue)
            for comment in comments:
                issue_text += f"Comment: {getattr(comment, 'body', '')}\n"

            if self.anonymize_data:
                issue_text = self.anonymizer.anonymize(issue_text)
            issue_data['issue_text'] = issue_text
            yield issue_data

            logging.debug(f"Issue data: {issue_data}")

    def write_jira_data(self,
                        tickets_dir: Optional[str],
                        jira_data: Iterator[dict[str, str]]) -> None:
        """
        Writes each JIRA ticket into separate text files in the ticket tickets_dir
        """
        print(f"Saving the ticket data into {tickets_dir}")
        for issue in jira_data:
            with open(os.path.join(tickets_dir, f"jira_{issue['issue_id']}.txt"), "w", encoding="utf-8") as f:
                f.write(issue['created_at'] + "\n")
                f.write(issue['issue_text'])
