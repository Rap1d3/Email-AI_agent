# Email Invoice AI Agent

An AI-powered agent that scans a Gmail inbox, identifies incoming invoices using an LLM with tool calling, extracts and verifies their content, and automatically saves them to a local folder with a detailed processing log.

## Features

- Connects to a Gmail inbox via IMAP (App Password authentication)
- Uses Claude (Anthropic API) with tool calling to classify emails as invoices or not
- Verifies the actual content of PDF attachments rather than relying on filenames alone
- Automatically saves invoice attachments to a structured local folder
- Logs every processed email (classification result, reasoning, saved file path) to a CSV file
- Skips emails that have already been processed, based on their unique IMAP UID
- Handles errors at every stage (network issues, malformed emails, API failures) without crashing the whole run

## Architecture / Pipeline

The agent processes each unread email through four stages:

1. **Connect to mailbox** — authenticate with Gmail over IMAP.
2. **Fetch & parse emails** — retrieve unread emails, decode subject/body, extract attachments.
3. **AI classification** — an agent (Claude, with tool calling) decides whether the email is an invoice. If the subject/body text isn't conclusive, it calls a tool to read the actual PDF content before deciding.
4. **Save & log** — if classified as an invoice, save its PDF attachment(s) to the `invoices/` folder and record the result in `log.csv`.

### Project structure

| File | Responsibility |
|---|---|
| `mail_client.py` | IMAP connection, fetching emails, parsing content/attachments, saving files |
| `agent.py` | AI classification logic — system prompt, tool definitions, tool-calling loop |
| `main.py` | Orchestrates the full pipeline and writes the CSV log |

## Requirements

- Python 3.10+
- A Gmail account with 2-Step Verification enabled (required to generate an App Password)
- An Anthropic API key


## Setup

1. **Clone the repository**

2. **Install dependencies**

3. **Create a `.env` file** in the project root:
   ```
   EMAIL_ADDRESS=your_email@gmail.com
   EMAIL_PASSWORD=your_gmail_app_password
   ANTHROPIC_API_KEY=your_anthropic_api_key
   ```

   - **Gmail App Password**: requires 2-Step Verification to be enabled on the account. Generate one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
   - **Anthropic API key**: create one at [console.anthropic.com](https://console.anthropic.com/).

## Usage

Run the pipeline:

```bash
python main.py
```

On each run, the agent will:
- Connect to the mailbox and fetch unread emails
- Skip any email already recorded in `log.csv`
- Classify each new email and print the result to the console
- Save any invoice attachments to `invoices/`
- Append a row to `log.csv` for every processed email

## Configuration notes

- **`BODY.PEEK[]` vs `RFC822`** (in `get_email_content`, `mail_client.py`): the pipeline uses `BODY.PEEK[]` when fetching emails, which reads the message **without** marking it as read on the server. Switching to `RFC822` would fetch the same content but mark the email as read as a side effect — useful if you'd rather rely on the mailbox's read/unread state instead of the CSV log to track what's been processed.
- **Search window** (in `fetch_emails`, `mail_client.py`): the IMAP search currently uses `"UNSEEN SINCE 15-Jul-2026"`, limiting the search to unread emails from that date onward. This avoids scanning the entire mailbox history on every run. Adjust or remove the `SINCE` date as needed — since duplicate processing is already prevented via the CSV log (see below), this filter is purely an optimization, not a correctness requirement.

## Known limitations

- Gmail/IMAP only — not tested against other providers (Outlook, Yahoo, etc.), which would require different server settings.
- Classification relies on an LLM and is not guaranteed to be 100% accurate; `confidence` and `reason` fields are provided in the log for manual review.
- If an email has multiple PDF attachments, all are saved, but the CSV log stores their paths as a single semicolon-separated string.
- Duplicate-processing protection is based on IMAP UID, which is stable within a mailbox but is not guaranteed unique across a full mailbox reset (`UIDVALIDITY` change) — an extremely rare event in normal use.