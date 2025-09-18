import os

from a2rchi.utils.config_loader import load_global_config
from a2rchi.utils.jira import JiraClient
from a2rchi.utils.redmine_tickets import RedmineClient


class TicketManager():
    
    def __init__(self):
        self.global_config = load_global_config()
        self.data_path = self.global_config["DATA_PATH"]

        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True)

        # create sub-directory for tickets if it doesn't exist
        self.tickets_dir = os.path.join(self.data_path, "tickets")
        os.makedirs(self.tickets_dir, exist_ok=True)

        self.jira_client = JiraClient()
        self.redmine_client = RedmineClient()

    def run(self, redmine: bool, jira: bool):
        """
        Main function to run the TicketManager.
        """
        if jira: 
            self.jira_client.run(tickets_dir=self.tickets_dir)
        if redmine: 
            self.redmine_client.run(tickets_dir=self.tickets_dir)
