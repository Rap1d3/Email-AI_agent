



import imaplib, os 
from dotenv import load_dotenv
import email
from email.header import decode_header
from bs4 import BeautifulSoup
from email.utils import parseaddr, parsedate_to_datetime
import re
import uuid
from pathlib import Path

# Load environment variables from .env (email credentials, API keys, etc.)
load_dotenv()

# Gmail credentials used to log in via IMAP.
# Must be an App Password (not the regular account password) — Gmail blocks
# plain-password IMAP logins for security reasons.
email_address = os.getenv("EMAIL_ADDRESS")
email_password = os.getenv("EMAIL_PASSWORD")
if email_address is None or email_password is None:
    raise ValueError("Email address or email password is None")

#Connects to Gmail via IMAP over SSL, logs in, and selects the INBOX folder.
#Returns the connection object on success, or None if connection/login fails.
def connect_to_mailbox():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_address, email_password)
        mail.select("inbox")
        return mail
    except imaplib.IMAP4.error as e:
        # Wrong email/App Password, or Gmail rejected the login attempt.
        print(f"❌ Authentication failed: incorrect email or app password. "
              f"Check your .env file and make sure you're using a Gmail App Password, "
              f"not your regular account password.\nDetails: {e}")
        return None
    except OSError as e:
        # No internet connection, DNS failure, or the server is unreachable.
        print(f"❌ Connection failed: could not reach imap.gmail.com. "
              f"Check your internet connection or firewall settings.\n"
              f"Details: {e}")
        return None


#Searches the mailbox for unread emails and returns their UIDs(stable unique identifiers, unlike sequence numbers, which can be reused/shifted when the mailbox state changes).
#Returns an empty list if nothing is found or if the search fails.
def fetch_emails(mail):
    try:
        status, data = mail.uid('search', None, "UNSEEN SINCE 15-Jul-2026")
    except imaplib.IMAP4.error as e:
        # The search command itself was malformed (bad IMAP syntax).
        print(f"❌ Search command error: invalid search syntax.\nDetails: {e}")
        return []
    except OSError as e:
        # Network dropped mid-search.
        print(f"❌ Connection error during search.\nDetails: {e}")
        return []

    if status != "OK":
        # Server understood the command but reported a non-OK status.
        print(f"❌ Search failed with status: {status}")
        return []

    email_ids = data[0].split()
    return email_ids

"""
Fetches the full raw content of a single email by UID and parses it into
an email.message.Message object.

Uses BODY.PEEK[] instead of RFC822 so that fetching an email does NOT
mark it as read on the server — the mailbox should stay untouched by
this script beyond what it explicitly does (saving invoices, logging).

Returns None if the fetch failed or the server returned no data
(e.g. the email was deleted between the search and this fetch call).
"""
def get_email_content(mail, email_id):
    status, msg_data = mail.uid('fetch',email_id , "(BODY.PEEK[])")
    #status, msg_data = mail.fetch(email_id , "RFC822")
    if status != "OK" or msg_data[0] is None:
        return None
    raw_email_bytes = msg_data[0][1]
    msg = email.message_from_bytes(raw_email_bytes)
    return msg

"""
Extracts and decodes the subject and body text from an email message.

Subject: may be split into multiple RFC 2047 encoded-word fragments
(e.g. long or non-ASCII subjects), each with its own encoding — so we
decode and concatenate ALL fragments, not just the first one.

Body: prefers plain text; if only an HTML part is available, strips
the HTML tags down to readable text as a fallback. Skips attachment
parts entirely (they're handled separately by get_attachments).
"""
def mail_decoding(msg):
    subject_raw = msg["Subject"]
    if subject_raw is None:
        # Some emails (spam, automated notifications) have no Subject header at all.
        subject = "(no subject)"
    else:
        decoded_parts = decode_header(subject_raw)
        subject = ""
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                subject += part.decode(encoding or "utf-8" , errors="replace")
            else: subject += part

    plain_body = None
    html_body = None

    if msg.is_multipart():
        # Multipart emails contain several parts (plain text, HTML, attachments, etc.).
        # Walk through all of them and pick out the plain text and HTML bodies.
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = part.get("Content-Disposition")

            # Skip attachment parts — we only want the readable message body here.
            if content_disposition and "attachment" in content_disposition:
                continue

            if content_type == "text/plain" and plain_body is None:
                plain_body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
            elif content_type == "text/html" and html_body is None:
                html_body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        # Simple, single-part email — no need to walk anything.
        content_type = msg.get_content_type()
        raw = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
        if content_type == "text/plain":
            plain_body = raw
        elif content_type == "text/html":
            html_body = raw

    # Prefer plain text; fall back to HTML stripped of tags; fall back to empty string.
    if plain_body:
        body = plain_body
    elif html_body:
        soup = BeautifulSoup(html_body, "html.parser")
        body = soup.get_text(separator="\n", strip=True)
    else:
        body = ""

    return [subject, body]

"""
Collects all attachments from an email message.
Returns a list of (filename, raw_bytes) tuples — the raw bytes are kept
as-is (not decoded as text) since attachments are typically binary (PDFs).
"""
def get_attachments(msg):
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            content_dispisition = part.get("Content-Disposition")
            if content_dispisition is not None:
                if content_dispisition.startswith("attachment"):
                    file_data = part.get_payload(decode=True)
                    file_name = part.get_filename()
                    if file_name is None: 
                        # Rare case: attachment has no filename parameter in its headers.
                        file_name = "unnamed_attachment.pdf"
                    attachments.append((file_name , file_data)) 
    
    return attachments

"""
Extracts just the email address from the "From" header
(which may look like "Name" <address@example.com>).
Used for naming saved attachment files consistently.
"""
def get_sender_email(msg):
    from_header = msg["From"]
    if from_header is None:
        # Header is missing entirely — fall back to a safe placeholder
        # instead of crashing parseaddr() on None.
        return "unknown_sender"
    name, email_address = parseaddr(from_header)
    return email_address

"""
Extracts and formats the email's Date header as YYYY-MM-DD,
for use in the saved attachment's filename.
"""
def get_email_date(msg):
    date_raw = msg["Date"]
    if date_raw is None:
        # Header is missing — fall back to a safe placeholder. This is only
        # used for filenames/logs, never parsed back into a date, so a
        # plain string here can't break anything downstream.
        return "unknown_send_date"
    date_obj = parsedate_to_datetime(date_raw)
    formatted_date = date_obj.strftime("%Y-%m-%d")
    return formatted_date

"""
Replaces any character that isn't a letter, digit, underscore, dash,
'@' or '.' with an underscore, so the result is always safe to use
as part of a filename on any OS.
"""
def sanitize_filename_part(text):
    return re.sub(r'[^\w\-@.]', '_' , text)

"""
Saves an attachment's raw bytes to disk inside the local "invoices" folder.

Filename format: {date}_{sender}_{original_name}_{random_suffix}.pdf
- date/sender make the file traceable to its source email at a glance
- random suffix avoids collisions if the same sender sends the same
filename on the same day more than once
"""
def save_attachment(filename , file_data , msg):
    date_str = get_email_date(msg)
    sender = sanitize_filename_part(get_sender_email(msg))
    suffix = uuid.uuid4().hex[:6]
    filename = sanitize_filename_part(os.path.splitext(filename)[0])
    new_filename = f'{date_str}_{sender}_{filename}_{suffix}.pdf'

    output_folder = Path("invoices")
    # create the folder if it doesn't exist yet
    output_folder.mkdir(exist_ok=True)

    file_path = output_folder / new_filename

    with open(file_path , "wb") as f:
        f.write(file_data)
    
    return file_path


#test for "get_attachment" function
"""mail = connect_to_mailbox()
email_ids = fetch_emails(mail)
if email_ids:
    #for i in range(len())
    msg = get_email_content(mail, email_ids[0])
    attachments = get_attachments(msg)
    for filename, data in attachments:
        print(f"Found attachment: {filename}, size: {len(data)} bytes")"""

#test for "get_email_content" function
"""mail = connect_to_mailbox()
email_ids = fetch_emails(mail)
if email_ids:
    """"""for i in range(len(email_ids)):
        msg = get_email_content(mail, email_ids[i])
        subject, body = mail_decoding(msg)
        print("Subject:", subject)
        print("Body:", body)""""""
    
    msg = get_email_content(mail, email_ids[0])
    subject, body = mail_decoding(msg)
    print("Subject:", subject)
    print("Body:", body)"""

#test for "save_attachment" function
"""mail = connect_to_mailbox()
email_ids = fetch_emails(mail)
if email_ids:
    msg = get_email_content(mail, email_ids[0])
    attachments = get_attachments(msg)
    if attachments:
        filename, file_data = attachments[0]
        saved_path = save_attachment(filename, file_data, msg)
        print(f"Saved to: {saved_path}")"""