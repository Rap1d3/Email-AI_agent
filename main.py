



from mail_client import connect_to_mailbox, fetch_emails, get_email_content, mail_decoding, get_attachments, save_attachment, get_email_date, get_sender_email
from agent import classify_email
import csv, os

# Entry point of the pipeline: connects to the mailbox, fetches unread
# emails, classifies each with the AI agent, saves invoice attachments,
# and logs the result of every processed email to log.csv.
mail = connect_to_mailbox()
if mail is None:
    print("❌ Could not connect to mailbox. Exiting.")
    exit()

email_ids = fetch_emails(mail)
if not email_ids:
    print("📭 No new emails to process.")
    exit()

# Track which email UIDs have already been logged, to avoid reprocessing
# the same email on repeated runs.
file_exists = os.path.exists("log.csv") and os.path.getsize("log.csv") > 0
processed_uids = set()

if file_exists:
    try:
        with open("log.csv" , "r" , newline="" , encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader) # skip header row
            for row in reader:
                if row: processed_uids.add(row[0])
    except Exception as e:
        print(f"⚠️ Could not read existing log.csv, starting with empty history: {e}")
        processed_uids = set()

for email_id in email_ids:
    if email_id.decode() in processed_uids:
        print(f"⏭️ Skipping already processed email (UID: {email_id.decode()})")
        continue
    # Isolate each email's processing: a failure on one email should not
    # stop the rest of the batch from being processed.
    try:
        msg = get_email_content(mail , email_id)
        if msg is None:
            print(f"⚠️ Could not fetch email (UID: {email_id.decode()}), skipping.")
            continue
        subject , body = mail_decoding(msg)
        attachments = get_attachments(msg)
    
        result = classify_email(subject , body , attachments)
        print(subject , "->" , result)
    
        # Save every attachment only when the email was classified as an invoice.
        saved_paths = []

        if result["is_invoice"]:
            for filename, file_data in attachments:
                path = save_attachment(filename , file_data, msg)
                saved_paths.append(str(path))
                print(f"💾 Saved: {path}")
        saved_paths_str = "; ".join(saved_paths)

        with open("log.csv" , "a" , newline= "" , encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["uid", "date", "sender", "subject", "is_invoice", "confidence", "reason", "saved_file"])
                file_exists = True
            writer.writerow([email_id.decode(), get_email_date(msg) , get_sender_email(msg) , subject , result["is_invoice"] , result["confidence"] ,result["reason"] ,saved_paths_str])
    
    except Exception as e:
        print(f"❌ Error processing email (UID: {email_id.decode()}): {e}")
        continue
    
try:
    mail.logout()
except Exception:
    pass
print("✅ Done. Mailbox connection closed.")


