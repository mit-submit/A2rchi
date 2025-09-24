import os

from src.data_manager.collectors.tickets.integrations.jira import JiraClient
from src.data_manager.collectors.tickets.integrations.redmine_tickets import \
    RedmineClient
from src.utils.config_loader import load_global_config

global_config = load_global_config()

class TicketManager():
    
    def __init__(self):
        self.data_path = global_config["DATA_PATH"]

        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True)

        # create sub-directory for tickets if it doesn't exist
        self.tickets_dir = os.path.join(self.data_path, "tickets")
        os.makedirs(self.tickets_dir, exist_ok=True)

        self.jira_client = JiraClient()
        self.redmine_client = RedmineClient()

    def run(self):
        """
        Main function to run the TicketManager.
        """
        self.jira_client.run(tickets_dir=self.tickets_dir)
        self.redmine_client.run(tickets_dir=self.tickets_dir)