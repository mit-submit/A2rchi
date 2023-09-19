#!/bin/python
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.env import read_secret

import email
import imaplib
import os

### DEFINITIONS
# this constant defines an offset into the message description
# which contains the Cleo issue id that a message refers to.
ISSUE_ID_OFFSET = 9


class Mailbox:
    'A class to describe the mailbox usage.'

    def __init__(self):
        """
        The mailbox (should be a singleton).
        """
        self.mailbox = None
        self.user = read_secret('IMAP_USER')
        self.password = read_secret('IMAP_PW')
        self.config = Config_Loader().config["utils"]["mailbox"]

        # make sure to open the mailbox
        if self._verify():
            self.mailbox = self._connect()


    def process_message(self, num, cleo):
        """
        Process a single message, including addition to cleo and removal from inbox
        """
        _, msg_data = self.mailbox.fetch(num, '(RFC822)')
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                # get the basic message parameters (description = body)
                msg = email.message_from_bytes(response_part[1])
                sender, cc, subject, description = self._get_fields(msg)

                # find the issue, if it exists already
                issue_id = self._find_issue_id(description)

                # make sure to deal with attachments correctly
                attachments = self._get_attachments(msg)

                if issue_id > 0:
                    note = f"ISSUE_ID:{issue_id} continued (leave for reference)\n\n"
                    note += f"{subject}: {description}"
                    cleo.reopen_issue(issue_id, note, attachments)
                    self._cleanup_message(num, attachments)
                else:
                    issue_id = cleo.new_issue(sender, cc, subject, description, attachments)
                    if issue_id > 0:
                        self._cleanup_message(num, attachments)
                    else:
                        print(f" ERROR - issue_id is not well defined: {issue_id}")

        return


    def process_messages(self, cleo):
        """
        Select all messages in the mailbx and process them.
        """
        self.mailbox.select()
        _, data = self.mailbox.search(None, 'ALL')
        print(f" mailbox.process_messages: {len(data[0].split())}")

        for num in data[0].split():
            self.process_message(num, cleo)

        self.mailbox.close()
        self.mailbox.logout()

        return


    def _cleanup_message(self, num, attachments):
        self.mailbox.store(num, '+FLAGS', '\\Deleted')

        # remove temporary attachment copies
        for a in attachments:
            os.system(f"rm /tmp/{a['filename']}")

        return


    def _get_charsets(self, msg):
        charsets = set({})
        for c in msg.get_charsets():
            if c is not None:
                charsets.update([c])

        return charsets


    def _find_issue_id(self, description):
        """
        Select all messages in the mailbx and process them.
        """
        issue_id = 0
        index = description.find('ISSUE_ID:')
        if index > 0:
            issue_id = int(description[index + ISSUE_ID_OFFSET:].split()[0])
        print(f" ISSUE_ID: {issue_id}")

        return issue_id


    def _get_attachments(self, msg):
        # finding all attachments in an email
        attachments = []
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            if part.get('Content-Disposition') is None:
                continue
            file_name = part.get_filename()

            if bool(file_name):
                print(f" INFO - found attachement: {file_name}")
                file_path = os.path.join('/tmp/', file_name)
                if not os.path.isfile(file_path):
                    with open(file_path, 'wb') as f:
                        f.write(part.get_payload(decode=True))

                    attachments.append({'path': file_path, 'filename': file_name})
                else:
                    print(" ERROR - could not download attachment (file exists).")
                    return []

        return attachments


    def _get_email_body(self, msg):
        # finding the body in an email message
        body, body_html = None, None

        # walk through the parts of the email to find the text body.    
        if msg.is_multipart():
            for part in msg.walk():

                # if part is multipart, walk through the subparts.            
                if part.is_multipart(): 

                    for subpart in part.walk():
                        if subpart.get_content_type() == 'text/plain' and body == None:
                            # get the subpart payload (i.e the message body)
                            body = subpart.get_payload(decode=True) 
                        elif subpart.get_content_type() == 'html' and body_html == None:
                            body_html = subpart.get_payload(decode=True)

                # part isn't multipart so get the email body
                elif part.get_content_type() == 'text/plain' and body == None:
                    body = part.get_payload(decode=True)

        # if this is not a multi-part message then get the payload (i.e the message body)
        elif msg.get_content_type() == 'text/plain':
            body = msg.get_payload(decode=True) 

       # no checking done to match the charset with the correct part. 
        for charset in self._get_charsets(msg):
            try:
                body = body.decode(charset)
            except UnicodeDecodeError:
                self._handle_error("UnicodeDecodeError: encountered.", msg, charset)
            except AttributeError:
                self._handle_error("AttributeError: encountered.", msg, charset)

        return body, body_html


    def _get_fields(self, msg):
        sender, cc, subject = msg['from'], msg['cc'], msg['subject']
        for header in ['subject', 'to', 'cc', 'bcc', 'from']:
            print(f"{header.upper():8s}: {msg[header]}")

        body, body_html = self._get_email_body(msg)
        description = body if body else body_html
        print("BODY:")
        print(description)

        return sender, cc, subject, description


    def _connect(self):
        """
        Open the mailbox
        """
        print(f" Open mailbox (U:{self.user} P:*********)")
        mailbox = imaplib.IMAP4(host='ppc.mit.edu', port=self.config["IMAP4_PORT"], timeout=None)
        mailbox.login(self.user, self.password)

        return mailbox


    def _handle_error(self, errmsg, emailmsg, cs):
        print()
        print(errmsg)
        print(f"This error occurred while decoding with {cs} charset.")
        print(f"These charsets were found in this email: {self._get_charsets(emailmsg)}")
        print(f"This is the subject: {emailmsg['subject']}")
        print(f"This is the sender: {emailmsg['From']}")

        return


    def _verify(self):
        """
        Make sure the environment is setup
        """
        if self.user == None or self.password == None:
            print(" Did not find all cleo configs: IMAP_USER, IMAP_PW (source ~/.imap).")
            return False

        return True
