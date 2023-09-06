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

    def process_message(self,num,cleo):
        """
        Process a single message, including addition to cleo and removal from inbox
        """
        typ, msg_data = self.mailbox.fetch(num, '(RFC822)')
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
                    cleo.reopen_issue(issue_id,note,attachments)
                    self._cleanup_message(num,attachments)
                else:
                    issue_id = cleo.new_issue(sender,cc,subject,description,attachments)
                    if issue_id > 0:
                        self._cleanup_message(num,attachments)
                    else:
                        print(f" ERROR - issue_id is not well defined: {issue_id}")
        return

    def process_messages(self,cleo):
        """
        Select all messages in the mailbx and process them.
        """
        self.mailbox.select()
        typ, data = self.mailbox.search(None, 'ALL')
        print(" mailbox.process_messages: %d"%(len(data[0].split())))
        for num in data[0].split():
            self.process_message(num,cleo)
#        self.mailbox.expunge()
        self.mailbox.close()
        self.mailbox.logout()
        return
            
    def _cleanup_message(self,num,attachments):
        self.mailbox.store(num,'+FLAGS', '\\Deleted')
        for a in attachments:              # remove temporary attachment copies
            os.system(f"rm /tmp/{a['filename']}")
        return
        
    def _get_charsets(self,msg):
        charsets = set({})
        for c in msg.get_charsets():
            if c is not None:
                charsets.update([c])
        return charsets
    
    def _find_issue_id(self,description):
        """
        Select all messages in the mailbx and process them.
        """
        issue_id = 0
        index = description.find('ISSUE_ID:')
        if index > 0:
            issue_id = int(description[index+9:].split()[0])
        print(f" ISSUE_ID: {issue_id}")
        return issue_id
    
    def _get_attachments(self,msg):
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
                file_path = os.path.join('/tmp/',file_name)
                if not os.path.isfile(file_path):
                    with open(file_path,'wb') as f:
                        f.write(part.get_payload(decode = True))
                    #print(f" INFO - append: {file_path}")
                    attachments.append({'path': file_path, 'filename': file_name})
                else:
                    print(" ERROR - could not download attachment (file exists).")
                    return []

        return attachments

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
                        if subpart.get_content_type() == 'text/plain' and body == None:
                            # get the subpart payload (i.e the message body)
                            body = subpart.get_payload(decode=True) 
                        elif subpart.get_content_type() == 'html' and body_html == None:
                            body_html = subpart.get_payload(decode=True)
    
                # part isn't multipart so get the email body
                elif part.get_content_type() == 'text/plain' and body == None:
                    body = part.get_payload(decode = True)
    
        # if this is not a multi-part message then get the payload (i.e the message body)
        elif msg.get_content_type() == 'text/plain':
            body = msg.get_payload(decode = True) 
    
       # no checking done to match the charset with the correct part. 
        for charset in self._get_charsets(msg):
            try:
                body = body.decode(charset)
            except UnicodeDecodeError:
                self._handle_error("UnicodeDecodeError: encountered.",msg,charset)
            except AttributeError:
                self._handle_error("AttributeError: encountered" ,msg,charset)
    
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
