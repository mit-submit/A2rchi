import os
import getpass, imaplib, email

from utils.config_loader import Config_Loader
config = Config_Loader().config["utils"]["mailbox"]

def get_charsets(msg):
    charsets = set({})
    for c in msg.get_charsets():
        if c is not None:
            charsets.update([c])
    return charsets

def handle_error(errmsg, emailmsg, cs):
    print()
    print(errmsg)
    print("This error occurred while decoding with ",cs," charset.")
    print("These charsets were found in this email.",get_charsets(emailmsg))
    print("This is the subject:",emailmsg['subject'])
    print("This is the sender:",emailmsg['From'])

def get_email_body(msg):
    # finding the body in an email message
    
    body = 'no_text'
    body_html = 'no_html'

    # Walk through the parts of the email to find the text body.    
    if msg.is_multipart():    
        for part in msg.walk():

            # If part is multipart, walk through the subparts.            
            if part.is_multipart(): 

                for subpart in part.walk():
                    if subpart.get_content_type() == 'text/plain':
                        # Get the subpart payload (i.e the message body)
                        body = subpart.get_payload(decode=True) 
                        #charset = subpart.get_charset()
                    elif subpart.get_content_type() == 'html':
                        body_html = subpart.get_payload(decode=True)
                        #body_html = subpart.get_payload(decode=True)

            # Part isn't multipart so get the email body
            elif part.get_content_type() == 'text/plain':
                body = part.get_payload(decode=True)
                #charset = part.get_charset()

    # If this is not a multi-part message then get the payload (i.e the message body)
    elif msg.get_content_type() == 'text/plain':
        body = msg.get_payload(decode=True) 

   # No checking done to match the charset with the correct part. 
    for charset in get_charsets(msg):
        try:
            body = body.decode(charset)
        except UnicodeDecodeError:
            handle_error("UnicodeDecodeError: encountered.",msg,charset)
        except AttributeError:
             handle_error("AttributeError: encountered" ,msg,charset)

    return body, body_html  


M = imaplib.IMAP4(host='ppc.mit.edu', port=config["IMAP4_PORT"], timeout=None)
#M.login(getpass.getuser(),getpass.getpass())
M.login('cmsprod',getpass.getpass())
M.select()

typ, data = M.search(None, 'ALL')
for num in data[0].split():
    typ, msg_data = M.fetch(num, '(RFC822)')
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            msg = email.message_from_string(response_part[1].decode())
            for header in [ 'subject', 'to', 'cc', 'bcc', 'from' ]:
                print('%-8s: %s'%(header.upper(),msg[header]))
            body, body_html = get_email_body(msg)
            print("BODY:")
            print(body)
            print(body_html)

M.close()
M.logout()
