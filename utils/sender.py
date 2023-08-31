import os,sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

class Sender:
    'A class to send emails in an uncomplicated fashion.'

    def __init__(self):
        """
        Give it a name and generate a conncetion to the database (should be a singleton).
        """
        print(f" Open smtp (SERVER:{os.getenv('SENDER_SERVER')} PORT:{os.getenv('SENDER_PORT')} U:{os.getenv('SENDER_USER')} P:*********)")

        self.server_name = os.getenv('SENDER_SERVER')
        self.user = os.getenv('SENDER_USER')
        self.password = os.getenv('SENDER_PW')

    def send_message(self,to,cc,subject,body):

        # start and login to SMTP server 
        self.server = smtplib.SMTP(self.server_name,os.getenv('SENDER_PORT'))
        self.server.starttls()
        self.server.login(self.user,self.password)

        # generate the message
        msg = MIMEMultipart()
        msg['To'] = to
        msg['CC'] = cc
        msg['Subject'] = subject
        if os.getenv('SENDER_REPLYTO'):
            msg.add_header('reply-to',os.getenv('SENDER_REPLYTO'))

        # show what we are going to do
        print(f" Sending message - TO: {to}, CC: {cc}, REPLYTO: {os.getenv('SENDER_REPLYTO')}")
        print(f" ===============\n SUBJECT: {subject}")
        print(f" BODY:\n{body}")

        # add the message body
        msg.attach(MIMEText(body,'plain'))
        self.server.sendmail(self.user,"%s,%s"%(to,cc),msg.as_string())

        # finally, quit the server
        self.server.quit()

        return
