import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, getaddresses, parseaddr

from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)

class Sender:
    """A class to send emails in an uncomplicated fashion."""

    def __init__(self):
        """
        Give it a name and generate a conncetion to the database (should be a singleton).
        """
        self.server_name = read_secret('SENDER_SERVER')
        self.port = read_secret('SENDER_PORT')
        self.user = read_secret('SENDER_USER')
        self.password = read_secret('SENDER_PW')
        self.reply_to = read_secret('SENDER_REPLYTO')

        logger.info(f"Open smtp (SERVER:{self.server_name} PORT:{self.port} U:{self.user} P:*********)")


    def send_message(self, to, cc, subject, body):

        #start and login to SMTP server
        self.server = smtplib.SMTP(self.server_name, self.port)
        self.server.starttls()
        self.server.login(self.user, self.password)

        # generate the message
        msg = MIMEMultipart()
        msg['To'] = to
        msg['CC'] = cc
        msg['Subject'] = subject
        if self.reply_to:
            msg.add_header('reply-to', self.reply_to)

        # show what we are going to do
        logger.info(f"Sending message - TO: {to}, CC: {cc}, REPLYTO: {self.reply_to}")
        logger.info(f"SUBJECT: {subject}")
        logger.info(f"BODY: {body}")

        # Prepare the recipient list
        recipient_list = []
        if to:
            recipient_list.extend([
            formataddr(addr) for addr in getaddresses([to])
            ])

        if cc:
            recipient_list.extend([
            formataddr(addr) for addr in getaddresses([cc])
            ])

        logger.info(f"Recipient List: {recipient_list}")
        smtp_recipient_list = [addr[1] for addr in getaddresses([to, cc]) if addr[1]]
        logger.debug(f"SMTP Recipient List: {smtp_recipient_list}")

        # Send the email
        msg.attach(MIMEText(body, 'plain'))
        self.server.sendmail(self.user, recipient_list, msg.as_string())

        #finally, quit the server
        self.server.quit()

        return
