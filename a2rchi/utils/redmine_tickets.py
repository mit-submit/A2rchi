from a2rchi.utils.anonymizer import Anonymizer
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import get_logger

import os
from redminelib import Redmine
from typing import Any, Dict, Iterator

logger = get_logger(__name__)

# use this to grab the answer for a given ticket, then remove it from answer text
ANSWER_TAG = load_config()["utils"]["redmine"]["answer_tag"]

class RedmineClient():
    def __init__(self) -> None:
            self.redmine_url = read_secret("CLEO_URL")
            self.redmine_user = read_secret("CLEO_USER")
            self.redmine_pw = read_secret("CLEO_PW")
            self.redmine_project = read_secret("CLEO_PROJECT")

            if self._verify():
                self._connect()
                self._load()

            self.anonymizer = Anonymizer()

    def _verify(self, verbose: bool = True) -> bool:
        """
        Check if necessary secrets are provided to access Redmine
        """
        try:
            if not all([self.redmine_url, self.redmine_user, self.redmine_pw, self.redmine_project]):
                if verbose:
                    logger.info("Redmine secrets couldn't be found. A2rchi will skip data fetching from Redmine")
                return
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Redmine secrets couldn't be found. A2rchi will skip data fetching from Redmine: {str(e)}")
        
        return True

    def _connect(self) -> None:
        """
        Open the redmine web site called cleo
        """
        logger.info(f"Open redmine (URL:{self.redmine_url} U:{self.redmine_user} P:*********)")
        self.redmine = Redmine(self.redmine_url, username=self.redmine_user, password=self.redmine_pw)
        return

    def _load(self) -> None:
        """
        Load the project that is responsible to deal with email tickets.
        """
        self.project = self.redmine.project.get(self.redmine_project)
        return

    def run(self, tickets_dir: str) -> None:
        """
        Main function to run Redmine ticket reader.
        """
        if self._verify(verbose=False):
            ticket_data_generator = self.prepare_ticket_info()
            self.write_ticket_files(tickets_dir, ticket_data_generator)
        

    def write_ticket_files(self, tickets_dir: str, ticket_data_generator: Iterator[Dict[str, str]]) -> None:
        """
        Write each ticket to a separate file, processing one ticket at a time.
        """
        logger.debug(f"Preparing to write individual redmine ticket files to {tickets_dir}")
        
        tickets_written = 0
        
        for ticket_data in ticket_data_generator:
            filename = f"redmine_ticket_{ticket_data['issue_id']}.txt"
            filepath = os.path.join(tickets_dir, filename)
            
            with open(filepath, "w", encoding="utf-8") as file:
                file.write(f"Redmine issue ID/ticket number: {ticket_data['issue_id']}\n")
                file.write(f"Subject: {ticket_data['subject']}\n")
                file.write(f"Question: {ticket_data['question']}\n")
                file.write(f"Answer: {ticket_data['answer']}\n")
                file.write("\n\n\n")
            
            tickets_written += 1
            
            if tickets_written % 100 == 0:
                logger.debug(f"Written {tickets_written} individual ticket files...")
        
        logger.info(f"Written {tickets_written} individual redmine ticket files to {tickets_dir}")

    def prepare_ticket_info(self) -> Iterator[Dict[str, str]]:
        """
        Generator to yield one ticket dictionary at a time with keys: 'subject', 'question', 'answer', 'issue_id'
        """
        closed_issues = self.get_closed_issues()
        logger.info(f"Preparing {len(closed_issues)} redmine tickets' data")
        processed_count = 0
        for issue in closed_issues:
            try:
                # get subject, question, and answer from issue
                full_issue = self.redmine.issue.get(issue.id)
                
                subject = self.anonymizer.anonymize(full_issue.subject)
                
                description = full_issue.description or ""
                description = description.replace("\n", " ")
                question = self.anonymizer.anonymize(description)
                
                answer = self._extract_answer_from_journals(full_issue.journals)
                
                if answer and question != answer:
                    ticket_data = {
                        'issue_id': str(full_issue.id),
                        'subject': subject,
                        'question': question,
                        'answer': answer
                    }
                    
                    processed_count += 1
                    if processed_count % 100 == 0:
                        logger.debug(f"Processed {processed_count} tickets so far...")
                    
                    yield ticket_data
                    
            except Exception as e:
                logger.error(f"Error processing ticket {issue.id}: {str(e)}")
                continue
        
        logger.info(f"Successfully processed {processed_count} redmine tickets")

    def get_closed_issues(self) -> Any:

        closed_issues = self.redmine.issue.filter(
            project_id=self.project.id,
            status_id='closed',
        )
        return closed_issues
    
    def _extract_answer_from_journals(self, journals: Any) -> str:
        """
        Takes Redmine journal, returns formatted and anonymized answer (most recent one for now) if exists, otherwise empty string (checked later)
        """
        answers = []
        for record in journals[::-1]:
            note = record.notes
            if note and ANSWER_TAG in note:
                answer = note.replace(ANSWER_TAG, "")
                answer = "\n".join(line for line in answer.splitlines() if "ISSUE_ID" not in line)
                answer = answer.replace("\n", " ")
                answer = self.anonymizer.anonymize(answer)
                answers.append(answer)
        
        return answers[-1] if answers else ""