import os

from a2rchi.utils.config_loader import Config_Loader
from a2rchi.interfaces.jira.jira_reader import JiraReader


class TicketManager():
    
    def __init__(self):
        self.data_path = Config_Loader().config["global"]["DATA_PATH"]

        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True)

        # create sub-directory for tickets if it doesn't exist
        self.tickets_dir = os.path.join(self.data_path, "tickets")
        os.makedirs(self.tickets_dir, exist_ok=True)

        self.jira_reader = JiraReader()

    def run(self):
        """
        Main function to run the TicketManager.
        """
        self.jira_reader.run(tickets_dir=self.tickets_dir)