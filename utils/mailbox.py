#!/bin/python
import os,sys
import getpass, imaplib, email

from utils.config_loader import Config_Loader
config = Config_Loader().config["utils"]["mailbox"]

class Mailbox:
    'A class to describe the mailbox usage.'

    def __init__(self):
        """
        The mailbox (should be a singleton).
        """
        self.mailbox = None
        # make sure to open the mailbox
        if self._verify:
            self.mailbox = self._connect()

    def find_issue_id(self,description):
        """
        Select all messages in the mailbx and process them.
        """
        issue_id = 0
        index = description.find('ISSUE_ID:')
        if index > 0:
            issue_id = int(description[index+9:].split()[0])
        return issue_id
    
    def process_messages(self,cleo):
        """
        Select all messages in the mailbx and process them.
        """
        self.mailbox.select()
        typ, data = self.mailbox.search(None, 'ALL')
        print(" mailbox.process_messages: %d"%(len(data[0].split())))
        for num in data[0].split():
            processed = False
            typ, msg_data = self.mailbox.fetch(num, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_string(response_part[1].decode())
                    sender, cc, subject, description = self._get_fields(msg)
                    print(f" subject: {subject}")
                    print(f" description: {description}")
                    issue_id = self.find_issue_id(description)
                    print(f" ISSUE_ID: {issue_id}")

                    if issue_id > 0:
                        note = f"ISSUE_ID:{issue_id} continued (leave for reference)\n\n"
                        note += f"{subject}: {description}"
                        cleo.reopen_issue(issue_id,note)
                        self.mailbox.store(num,'+FLAGS', '\\Deleted')
                    else:
                        issue_id = cleo.new_issue(sender,cc,subject,description)
                        if issue_id > 0:
                            self.mailbox.store(num,'+FLAGS', '\\Deleted')
                        else:
                            print(f" ERROR - issue_id is not well defined: {issue_id}")
        self.mailbox.expunge()
        self.mailbox.close()
        self.mailbox.logout()
        return
            
    def _get_charsets(self,msg):
        charsets = set({})
        for c in msg.get_charsets():
            if c is not None:
                charsets.update([c])
        return charsets
    
    def _get_email_body(self,msg):
        # finding the body in an email message
        
        body = None
        body_html = None
    
        # walk through the parts of the email to find the text body.    
        if msg.is_multipart():    
            for part in msg.walk():
    
                # if part is multipart, walk through the subparts.            
                if part.is_multipart(): 
    
                    for subpart in part.walk():
                        if subpart.get_content_type() == 'text/plain':
                            # get the subpart payload (i.e the message body)
                            body = subpart.get_payload(decode=True) 
                            #charset = subpart.get_charset()
                        elif subpart.get_content_type() == 'html':
                            body_html = subpart.get_payload(decode=True)
                            #body_html = subpart.get_payload(decode=True)
    
                # part isn't multipart so get the email body
                elif part.get_content_type() == 'text/plain':
                    body = part.get_payload(decode=True)
                    #charset = part.get_charset()
    
        # if this is not a multi-part message then get the payload (i.e the message body)
        elif msg.get_content_type() == 'text/plain':
            body = msg.get_payload(decode=True) 
    
       # no checking done to match the charset with the correct part. 
        for charset in self._get_charsets(msg):
            try:
                body = body.decode(charset)
            except UnicodeDecodeError:
                handle_error("UnicodeDecodeError: encountered.",msg,charset)
            except AttributeError:
                handle_error("AttributeError: encountered" ,msg,charset)
    
        return body, body_html
    
    def _get_fields(self,msg):
        sender = msg['from']
        cc = msg['cc']
        subject = msg['subject']
        for header in [ 'subject', 'to', 'cc', 'bcc', 'from' ]:
            print('%-8s: %s'%(header.upper(),msg[header]))
        body, body_html = self._get_email_body(msg)
        print("BODY:")
        if body:
            description = body
            print(body)
        if body_html:
            description = body_html
            print(body_html)
        return sender, cc, subject, description

    def _connect(self):
        """
        Open the mailbox
        """
        print(f" Open mailbox (U:{os.getenv('IMAP_USER')} P:*********)")
        mailbox = imaplib.IMAP4(host='ppc.mit.edu', port=config["IMAP4_PORT"], timeout=None)
        mailbox.login(os.getenv('IMAP_USER'),os.getenv('IMAP_PW'))
        return mailbox
            
    def _handle_error(self,errmsg, emailmsg, cs):
        print()
        print(errmsg)
        print("This error occurred while decoding with ",cs," charset.")
        print("These charsets were found in this email.",self._get_charsets(emailmsg))
        print("This is the subject:",emailmsg['subject'])
        print("This is the sender:",emailmsg['From'])
        return
        
    def _verify(self):
        """
        Make sure the environment is setup
        """
        if os.getenv('IMAP_USER') == None or os.getenv('IMAP_PW') == None:
            print(" Did not find all cleo configs: IMAP_USER, IMAP_PW (source ~/.imap).")
            return False
        return True
