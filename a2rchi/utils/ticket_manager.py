import os

from a2rchi.utils.config_loader import load_config
from a2rchi.utils.jira import JiraClient


class TicketManager():
    
    def __init__(self):
        self.data_path = load_config()["global"]["DATA_PATH"]

        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True)

        # create sub-directory for tickets if it doesn't exist
        self.tickets_dir = os.path.join(self.data_path, "tickets")
        os.makedirs(self.tickets_dir, exist_ok=True)

        self.jira_client = JiraClient()

    def run(self):
        """
        Main function to run the TicketManager.
        """
        self.jira_client.run(tickets_dir=self.tickets_dir)