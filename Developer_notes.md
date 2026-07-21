# Je m'appelle Rapide et je suis la myrtille la plus rapide du monde…

## mail_client.py

This is the backbone of the project — all the mechanical, scripted work
lives here. I kept everything as predictable and linear as I could.

It starts by loading the `.env` file, which is where the sensitive stuff
(credentials) comes from. Right after that, I set up variables for the
email address and password, and immediately validate them just to be
safe, before anything else runs.

Then there's `connect_to_mailbox()` — it doesn't take any arguments and
just returns a `mail` object holding the active connection to the mailbox.

### `fetch_emails()`

Next up is `fetch_emails()`. It takes the `mail` object as its only
argument. I call `mail.uid()` on it — the first parameter is `'search'`,
since I need actual unique IDs (UIDs, not sequence numbers), the second
is `None`, and the third is the full search criteria string, which
combines "unread only" (`UNSEEN`) with a starting date (`SINCE ...`) in
one go.

This gives back `status` and `data`: `status` tells me whether the
search itself succeeded, and `data` holds the raw result — a single
byte string with all the UIDs packed together, separated by spaces
(not a ready-to-use list yet).

After that I check for errors — first any exception the call itself
might raise, then the `status` value — and finally split `data[0]` to
get the actual list of individual UIDs, which I return.

### `get_email_content()`

Next is `get_email_content()`, which takes `mail` and `email_id` (the ID
of a specific email). It fetches the raw content of that email from the
server and turns it into a proper, parseable email object.

I use `mail.uid()` again, but this time with `'fetch'` instead of
`'search'`, followed by the email's own ID, and then a third argument
that specifies exactly what to retrieve and how — in this case,
`"(BODY.PEEK[])"`, which fetches the whole message **without** marking
it as read on the server (unlike a plain fetch, which would mark it
read as a side effect).

The response is saved into the now-familiar `status` and a new variable,
`msg_data`, holding the raw data. I check both the status and whether
any data actually came back — returning `None` if either check fails.
Then I pull the raw bytes of the message out of the nested structure,
and finally use `email.message_from_bytes()` to turn those bytes into a
full, usable email object.

### `mail_decoding()`

This one takes the `msg` object and pulls out two things from it: the
subject and the body text, returned together as `[subject, body]`.

For the subject, I first check if the `Subject` header even exists —
some emails (spam, automated notifications) don't have one at all, so
in that case I just fall back to `"(no subject)"`. If it does exist, I
run it through `decode_header()`, which can split a subject into several
encoded fragments (this happens with long or non-ASCII subjects) — so I
loop through **all** of them and concatenate the result, instead of just
grabbing the first one.

For the body, emails can either be multipart (several sections: plain
text, HTML, attachments) or a single simple part. If it's multipart, I
walk through every part, skip anything marked as an attachment, and grab
the plain text and HTML versions if present. If it's not multipart, I
just read the one part directly. In the end, I prefer plain text if it's
there; if only HTML is available, I strip the tags out with
BeautifulSoup to get readable text; if neither is available, I return an
empty string — so `body` is never left undefined.

### `get_attachments()`

This function collects every attachment in the email and returns them as
a list of `(filename, raw_bytes)` tuples. The bytes are kept exactly as
they are — not decoded as text — since attachments here are basically
always binary (PDFs), and decoding them as text would corrupt the data.

It only makes sense to look for attachments if the email is multipart, so
I walk through its parts and check each one's `Content-Disposition`
header — if it starts with `"attachment"`, that part is treated as a
file to save. I grab its raw payload and its filename; in the rare case
a part has no filename at all, I fall back to a placeholder
(`"unnamed_attachment.pdf"`) so the rest of the pipeline never has to
deal with a missing filename later on.

### `get_sender_email()`

This function takes the `msg` object and returns just the sender's email
address — nothing else. It exists because the raw `"From"` header
usually looks like `"Name" <address@example.com>`, and I only ever need
the clean address part for naming saved files consistently, not the
display name.

First it checks whether the `"From"` header is even present at all —
some emails can technically be missing it — and if so, returns a safe
placeholder (`"unknown_sender"`) instead of letting the next line crash.
If the header exists, it's passed to `parseaddr()`, a standard library
function built exactly for this — it splits a "From"-style string into a
`(name, email_address)` tuple, handling all the quoting/spacing edge
cases so I don't have to write my own parsing logic. I only keep and
return the `email_address` part.

### `get_email_date()`

This one extracts the email's `"Date"` header and reformats it into a
plain `YYYY-MM-DD` string, so it can be dropped straight into a saved
attachment's filename.

Same defensive check as above: if the `"Date"` header is missing, it
returns a placeholder (`"unknown_send_date"`) rather than crashing. This
placeholder is safe here specifically because the date is only ever used
as part of a filename or a log entry — it's never parsed back into an
actual date object anywhere else in the pipeline, so a plain string in
its place can't break anything downstream.

If the header is present, `parsedate_to_datetime()` converts the raw
header string (which comes in a verbose email-standard format, e.g.
`"Wed, 15 Jul 2026 14:34:44 GMT"`) into an actual Python `datetime`
object, and `.strftime("%Y-%m-%d")` formats that down to the simple date
string I actually want.

### `sanitize_filename_part()`

A small helper used whenever a piece of text (an email address, a
filename, etc.) needs to become part of a saved file's name. Filenames
have rules — certain characters aren't safe across every OS — so this
function replaces anything that isn't a letter, digit, underscore, dash,
`@`, or `.` with an underscore.

`@` and `.` are explicitly allowed (rather than also being replaced)
because they show up naturally in email addresses and file extensions,
and keeping them makes the resulting filename much more readable than if
they were also turned into underscores.

### `save_attachment()`

This function takes an attachment's `filename`, its raw `file_data`
bytes, and the `msg` it came from, and writes that attachment to disk
inside a local `invoices/` folder — returning the path it was saved to.

It builds the new filename piece by piece:
- `date_str` and `sender` come from the two functions above, giving the
  file a date and sender right in its name — so just by looking at the
  filename, you can tell which email it came from without opening
  anything.
- `suffix` is a short random string (`uuid.uuid4().hex[:6]`), added so
  that if the same sender ever sends an attachment with the same
  filename more than once on the same day, the files don't overwrite
  each other.
- The original `filename` has its extension stripped (`os.path.splitext`)
  and is run through `sanitize_filename_part()`, since it comes from the
  email itself and can't be trusted to already be filesystem-safe.

All four pieces are joined into `new_filename`, always ending in `.pdf`.

The `invoices/` folder is created if it doesn't already exist
(`mkdir(exist_ok=True)` — the `exist_ok` part means this won't raise an
error if the folder is already there from a previous run). Then the file
is opened in binary write mode (`"wb"` — binary matters here, since a
PDF isn't text and would get corrupted if written as one) and the raw
bytes are written straight to disk.

## agent.py

### `api_key` / `client`

Right after loading `.env`, I grab the Anthropic API key the same way I
did with the email credentials — read it via `os.getenv()`, and raise a
`ValueError` immediately if it's missing, so the script fails fast with
a clear reason instead of crashing later with a confusing API error.
`client` is then the actual `Anthropic` object I use everywhere else in
this file to talk to Claude.

### `tools`

This defines the two tools the model is allowed to call. It's not
optional configuration — this list is what turns a plain prompt-in,
answer-out call into an actual tool-calling agent, since the model
decides on its own whether and when to use each one.

- `extract_pdf_text` lets the model request the real text content of a
  PDF attachment instead of guessing based on its filename.
- `submit_classification` is how the model delivers its final answer.
  Making this a tool call (instead of just asking the model to "respond
  in JSON") guarantees the response always comes back as valid,
  structured data I can rely on — `is_invoice`, `confidence`, and
  `reason` are all required fields in its schema.

### `system_prompt`

This is where the agent's actual behavior is defined — its role, and the
exact rules it has to follow before making a decision.

The most important rule here (points 2 and 3) is that a filename like
`"Rechnung.pdf"` is never enough evidence on its own. I added this after
testing showed the model would otherwise classify emails purely based on
the filename — both false positives (a random PDF just named
`"Rechnung.pdf"`) and false negatives (a real invoice with a generic
filename). The prompt forces it to actually call `extract_pdf_text` and
read the content whenever the subject/body text isn't already conclusive
on its own.

### `extract_pdf_text()`

This is the real implementation behind the `extract_pdf_text` tool — the
function that actually runs when the model decides to call it. It takes
a `filename` and the full `attachments` list, and returns the extracted
text as a plain string (or a descriptive error string if something goes
wrong — never an exception, since this result has to go straight back to
the model as a `tool_result`).

First it loops through `attachments` looking for a matching `filename`,
stopping at the first match with `break` so a duplicate filename can't
silently overwrite the one already found. If nothing matches, it returns
a "not found" message instead of trying to process `None` data.

If the file is found, its raw bytes are wrapped in `io.BytesIO`, which
lets `pdfplumber` read them as if they were a file on disk — without
ever actually writing anything to disk. It then loops through every page
and calls `.extract_text()` on each one, skipping pages that return
`None` (this happens with blank or scanned/image-only pages that have no
extractable text layer), and builds up the full text across all pages.

The whole read is wrapped in `try/except`, because a corrupted or
oddly-formatted PDF can make `pdfplumber` raise an error — and in that
case, the function still returns a normal string describing the failure
instead of crashing, so the model gets a `tool_result` either way and can
factor "couldn't read this file" into its confidence level.

### `classify_email()`

This is the main entry point of the whole agent — it runs the full
tool-calling conversation loop for one email and returns the final
classification as a dict.

It's given `subject`, `body`, and `attachments`, but only ever sends the
model the **filenames** of the attachments, not their raw bytes — the
model has to explicitly call `extract_pdf_text` if it actually wants to
see what's inside one. The conversation starts as a single user message
combining all three pieces of information.

`max_iterations` exists as a safety limit: since the model is supposed to
end every classification by calling `submit_classification`, this caps
how many back-and-forth turns are allowed, so a model that somehow never
calls it can't loop (and burn API tokens) forever.

Inside the loop, each turn sends the current `messages` history to
Claude. The API call itself is wrapped in `try/except` — a network
issue, rate limit, or API outage shouldn't crash the whole pipeline, just
this one email's classification, so a fallback result dict is returned
instead.

Then it scans the blocks in the response looking for a `tool_use` block:

- If the model called `submit_classification`, that call's `input` is
  already the exact result dict I want (`is_invoice`, `confidence`,
  `reason`) — so I return it directly, ending the loop.
- If it called `extract_pdf_text` instead, I run that function for real,
  then append **two** things to `messages`: the model's own turn (so it
  remembers it made this call), and a `tool_result` message with the
  extracted text. Then I `break` out of the block-scanning loop so the
  outer loop sends this updated conversation back to the model on the
  next iteration — now with the PDF's actual content available to it.

After the block loop, there's a check on `response.stop_reason`: if it's
anything other than `"tool_use"`, the model responded with plain text
instead of calling a tool at all. This shouldn't happen given how the
system prompt is written, but if it ever does, raising here is caught by
the `try/except` around `classify_email()` in `main.py`, so it still
won't take down the rest of the run.

Finally, if the loop finishes all `max_iterations` without ever hitting
`submit_classification`, a fallback result is returned instead of leaving
the function to return `None`.

## main.py

This is the entry point that actually runs the whole pipeline —
connecting to the mailbox, fetching unread emails, classifying each one,
saving invoices, and logging every result to `log.csv`.

### Connecting and fetching

It starts by calling `connect_to_mailbox()` and `fetch_emails()`
directly, checking each result before moving on: if the connection
fails, or there are simply no unread emails, the script prints a clear
message and exits right there instead of continuing with nothing to
work with.

### Building the "already processed" list

Before touching any email, the script reads the existing `log.csv` (if
one exists) and collects every UID already logged into a `processed_uids`
set. This is what prevents the same email from being reprocessed (and
re-classified, burning API tokens again) on every repeated run — since
this script has no other way of "remembering" what it already handled
besides this log file. Reading the file is wrapped in `try/except`: if
`log.csv` is somehow unreadable or corrupted, the script just falls back
to an empty history rather than crashing before it even starts.

### The main loop

For every fetched `email_id`, the first check is whether it's already in
`processed_uids` — if so, it's skipped immediately with a message, no
API calls wasted on it.

Everything else happens inside a `try/except` block, on purpose: this
isolates each individual email, so if something goes wrong on one of
them (a malformed email, a network blip, an unexpected API response), it
gets logged as an error and the loop moves on to the next email instead
of the whole run crashing over a single bad email.

Inside the block: the email is fetched and parsed, then passed to
`classify_email()`. If the result comes back marked as an invoice, every
attachment is saved to disk via `save_attachment()`, and all the
resulting file paths are joined into a single semicolon-separated string
(since one email can have more than one attachment, but each row in the
CSV needs exactly one value per column).

### Logging

Every processed email — invoice or not — gets one row appended to
`log.csv`, containing its UID, date, sender, subject, classification
result, and saved file path(s). The header row is only written once, the
first time the file is created, using `file_exists` as a flag to track
that.

### Wrapping up

Once the loop finishes, `mail.logout()` closes the IMAP connection
cleanly. It's wrapped in its own `try/except` too, since by this point
the connection could theoretically already be in a broken state from
something earlier in the run — and failing to log out gracefully
shouldn't prevent the script from reporting that it's done.
