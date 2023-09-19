from A2rchi.chains.chain import Chain
from A2rchi.utils import sender
from A2rchi.utils.env import read_secret

from redminelib import Redmine

import re
import datetime

# DEFINITIONS
A2RCHI_PATTERN = '-- A2rchi --'


class CleoAIWrapper:
    """
    Wrapper which holds functionality for the cleobot. Way of interaction
    between cleo and A2rchi core.
    """

    def __init__(self):
        self.chain = Chain()
        self.number_of_queries = 0 #TODO: finish installing this safegaurd.

    def __call__(self, history):
        
        reformatted_history = []
        for entry in history:
            if "ISSUE_ID:" in entry[1]:
                role = "Expert"
            else:
                role = "A2rchi"
            message = CleoAIWrapper.get_substring_between(entry[1],"\n\nRe:","\r\nOn ")
            reformatted_history.append((role,message))
        reformatted_history[0] = ("Expert", reformatted_history[0][1])
        reformatted_history[-1] = ("User", reformatted_history[-1][1])
        return self.chain(reformatted_history)["answer"]

    @staticmethod
    def get_substring_between(text, start_word, end_word):
        """
        Small helper function. Return everything (not including) between the 
        start_word and the end_word if start_word and end_word exist. Otherwise
        it does nothing
        """
        start_index = text.find(start_word)
        end_index = text.find(end_word)

        if start_index != -1 and end_index != -1 and start_index < end_index:
            return text[start_index + len(start_word):end_index].strip()
        else:
            return text


class Cleo:
    'A class to describe the cleo redmine system.'

    def __init__(self, name):
        """
        Give it a name and generate a conncetion to the database (should be a singleton).
        """
        self.name = name             # to identify
        self.redmine = None
        self.smtp = sender.Sender()
        self.user = None
        self.project = None
        self.ai_wrapper = CleoAIWrapper()

        # read environment variables from secrets
        self.cleo_project = read_secret("CLEO_PROJECT")
        self.cleo_url = read_secret("CLEO_URL")
        self.cleo_user = read_secret("CLEO_USER")
        self.cleo_pw = read_secret("CLEO_PW")

        # make sure to open redmine access
        if self._verify():
            self.redmine = self._connect()
            self.user = self.redmine.user.get('current')
            self.load()

        # Load all the status, tracker, and priority ids
        statuses = self.redmine.issue_status.all()
        self.status_dict = dict()     # keys = status name, values = status_id
        for s in statuses:
            self.status_dict[s.name] = s.id

        trackers = self.redmine.tracker.all()
        self.tracker_dict = dict()
        for t in trackers:
            self.tracker_dict[t.name] = t.id

        priorities = self.redmine.enumeration.filter(resource="issue_priorities")
        self.priorities_dict = dict()
        for p in priorities:
            self.priorities_dict[p.name] = p.id


            
    def add_note_to_issue(self,issue_id,note):
        """
        Adding a note to an existing issue (and move to 'feedback' status)
        """
        self.redmine.issue.update(issue_id,status_id = self.status_dict['Feedback'],notes = note)
        return

    def reopen_issue(self, issue_id, note,attachments):
        """
        Move an issues status to `In Progress` and add a note
        """
        self.redmine.issue.update(issue_id,status_id = self.status_dict['In Progress'],
                                  notes = note,uploads = attachments)
        return
    
    def get_issue_history(self,issue_id):
        """
        Extract a tuple of author and notes for this ticket
        """
        issue = self.redmine.issue.get(issue_id)
        history = [("User:", "<b>" + issue.subject + "</b> \n" + issue.description )]
        for record in issue.journals:
            user = self.redmine.user.get(record.user.id)
            note = record.notes
            if note != '' and A2RCHI_PATTERN not in note:
                history.append((user.login,note))
        return history
    
    def load(self):
        """
        Load the project that is responsible to deal with email tickets.
        """
        self.project = self.redmine.project.get(self.cleo_project)
        return

    def new_issue(self,sender,cc,subject,description,attachments):
        """
        Create a brand new issue in the cleo system
        """
        if not subject.strip():
            subject = 'EMPTY subject'
        issue = self.redmine.issue.new()
        issue.project_id = self.project.id
        issue.subject = subject
        issue.description = description
        issue.tracker_id = self.tracker_dict["Support"]
        issue.status_id = self.status_dict['New']
        issue.priority_id = self.priorities_dict['Normal']
        issue.assigned_to_id = self.user.id
        issue.watcher_user_ids = []
        #issue.parent_issue_id =
        issue.start_date = datetime.date.today()
        issue.due_date = datetime.date.today()+datetime.timedelta(1)
        issue.estimated_hours = 1
        issue.done_ratio = 0
        issue.custom_fields = [{'id': 1, 'value': sender}, {'id': 2, 'value': cc}]
        #print(issue.custom_fields)
        #issue.custom_fields = []
        #issue.uploads = [{'path': '/abs/path/to/f1'}, {'path': '/abs/path/to/f2'}]
        #print(attachments)
        issue.uploads = attachments
        issue.save()
        return issue.id

    def process_new_issues(self):
        """
        Process all issues that are assigned to me and that are in 'New' or `In Progress` status.
        """
        issue_ids = []
        for issue in self.redmine.issue.filter(assigned_to_id=self.user.id,):
            if issue.status.id == self.status_dict['New'] or issue.status.id == self.status_dict['In Progress']:
                issue_ids.append(issue.id)
                subject = f"Re:{issue.subject}"
                history = self.remove_format(f"description: {issue.description}",'pre')
                for record in issue.journals:
                    if record.notes != "":
                        history += f"\n next entry: {record.notes}"                    
                print("History input: ",history)
                answer = self.ai_wrapper(self.get_issue_history(issue.id))
                self.add_note_to_issue(issue.id,answer)
                print("A2rchi's response:\n",answer)
                self.feedback_issue(issue.id)
        print(" cleo.process_new_issues: %d"%(len(issue_ids)))
        return issue_ids

    def process_resolved_issues(self):
        """
        Process all issues that are in resolved mode.
        """
        issue_ids = []
        for issue in self.project.issues:
            if issue.status.id == self.status_dict['Resolved']:
                print(f" process_resolved_issues: {issue.id}")
                issue_ids.append(issue.id)
                subject = f"Re:{issue.subject}"
                to = issue.custom_fields[0]['value']
                cc = issue.custom_fields[1]['value']
                note = ''
                for record in issue.journals:
                    if record.notes != '' and A2RCHI_PATTERN not in record.notes:
                        note = record.notes
                print(f"\n TO:{to}\n CC:{cc}\n SUBJECT:{subject}\nISSUE_ID:{issue.id} (leave for reference)\n\n{note}\n\n> {issue.description}")
                note = f"\nISSUE_ID:{issue.id} (leave for reference)\n\n{note}"
                addon = issue.description.replace("\n","\n > ")
                self.smtp.send_message(to,cc,subject,f"{note}\n\nInitial request:\n > {addon}")
                self.close_issue(issue.id,note)
        print(" cleo.process_resolved_issues: %d"%(len(issue_ids)))
        return issue_ids
        
    def remove_format(self,string,tag):
        pattern = r"<%s>.*?</%s>"%(tag,tag)
        return re.sub(pattern,"",string,flags=re.DOTALL)
    
    def close_issue(self,issue_id,answer):
        """
        Moving the issue in the 'closed' status
        """
        self.redmine.issue.update(issue_id,status_id=self.status_dict['Closed'],
                                  notes=f'{A2RCHI_PATTERN} Resolving email was sent:\n{answer}')
        return
    
    def feedback_issue(self,issue_id):
        """
        Moving the issue in the 'feedback' status
        """
        self.redmine.issue.update(issue_id,status_id=self.status_dict['Feedback'],
                                  notes=f'{A2RCHI_PATTERN} Moved into feedback.')
        return

    def show_issue(self,issue_id):
        """
        Show issue with given id as presently in the cleo system
        """
        issue = self.project.issues.get(issue_id)
        print(f" ==== id: {issue.id}")
        print(f" subject: {issue.subject}")
        print(f" description: {issue.description}")
        print(f" tracker: {issue.tracker} ({issue.tracker.id})")
        print(f" status: {issue.status} ({issue.status.id})")
        for record in issue.journals:
            print(dir(record))
            user = self.redmine.user.get(record.user.id)
            print(f" {record} ({user.login}):\n{record.notes}")
        return
            
    def show_issues(self):
        """
        Show all issues in the project
        """
        first = True
        for issue in self.project.issues:
            if first:
                first = False
                print("   Id status -- subject")
                print("========================")
            #print(" %04d %s -- %s"%(issue.id,issue.status,issue.subject))
            self.show_issue(issue.id)
        return

    def _connect(self):
        """
        Open the redmine web site called cleo
        """
        print(f" Open redmine (URL:{self.cleo_url} U:{self.cleo_user} P:*********)")
        rd = Redmine(self.cleo_url, username=self.cleo_user, password=self.cleo_pw)
        return rd
        
    def _verify(self):
        """
        Make sure the environment is setup
        """
        if self.cleo_url == None or self.cleo_user == None or self.cleo_pw == None:
            print(" Did not find all cleo configs: CLEO_URL, CLEO_USER, CLEO_PW (source ~/.cleo).")
            return False
        return True
