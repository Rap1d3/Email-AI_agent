



import os
from dotenv import load_dotenv
from anthropic import Anthropic
from mail_client import connect_to_mailbox, fetch_emails, get_email_content, mail_decoding, get_attachments
import io
import pdfplumber

# This module handles AI-based email classification: deciding whether an
# email is an invoice (Rechnung/Invoice) using Claude with tool calling.
# It's the only part of the pipeline that spends API tokens.
load_dotenv()

# Anthropic API key, required to talk to Claude.
api_key = os.getenv("ANTHROPIC_API_KEY")
if api_key is None:
    raise ValueError("ANTHROPIC_API_KEY is None")

client = Anthropic(api_key=api_key)

# Tool definitions passed to the model. The model decides on its own when
# (and whether) to call each one — this is what makes this a tool-calling
# agent rather than a single fixed prompt->answer call.
tools = [
    {
        "name": "extract_pdf_text",
        # Lets the model inspect the real content of a PDF attachment
        # instead of guessing based on its filename alone.
        "description": "Extracts the text from a PDF attachment so its actual content can be checked",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the PDF file to read"
                }
            },
            "required": ["filename"]
        }
    },
    {
    "name": "submit_classification",
    # The model MUST call this to deliver its final answer — forcing
    # the response through a tool call (rather than free-form text)
    # guarantees we always get valid, structured JSON back.
    "description": "Submits the final classification result for the email",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_invoice": {"type": "boolean"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "reason": {"type": "string"}
            },
        "required": ["is_invoice", "confidence", "reason"]
        }
    }
]

# System prompt defining the agent's role and decision rules.
# Key rule (point 2/3): filenames like "Rechnung.pdf" are NOT trusted as
# evidence on their own — the model is required to actually read the PDF
# content via extract_pdf_text before deciding, whenever subject/body text
# alone isn't conclusive. This was added after testing showed the model
# would otherwise classify emails based on filename alone (both false
# positives and false negatives).
system_prompt = """You are an AI agent that analyzes incoming emails to determine 
whether they are invoices (Rechnung/Invoice) that require processing.

You will receive the email's subject line, body text, and a list of attachment filenames.

Your task:
1. Analyze the subject and body TEXT CONTENT for signs of an invoice — such as invoice 
   numbers, payment amounts, due dates, vendor/company names, words like "Rechnung", 
   "Invoice", "Zahlung", "Betrag fällig", "Rechnungsnummer", or similar billing-related 
   language actually written in the subject or body.
   
2. IMPORTANT: An attachment's filename (e.g. "Rechnung.pdf", "Invoice.pdf") is NEVER 
   sufficient evidence on its own — filenames can be arbitrary, misleading, or generic. 
   Do not base your decision on the filename alone in either direction.

3. If there is a PDF attachment AND the subject/body text does not contain clear, 
   explicit invoice indicators (numbers, amounts, dates, billing language) — you MUST 
   call extract_pdf_text to inspect the actual content before deciding. Do not guess 
   based on filenames or the absence of information.

4. Only skip calling extract_pdf_text if the subject or body text ALREADY contains 
   explicit, unambiguous invoice details (e.g. an actual invoice number, a specific 
   amount, or the word "Rechnung"/"Invoice" used in context describing a real charge).

5. IMPORTANT: A real invoice normally has MULTIPLE structural elements together — 
   at least two of: an invoice/reference number, a vendor or company name, an issue 
   or due date, an itemized charge or payment instructions. The mere presence of a 
   single billing-related word (e.g. "Rechnung", "Invoice") together with a bare 
   number or amount, with no other structure, is NOT sufficient for "high" confidence 
   — this pattern is typical of test files or placeholder documents, not real invoices.

6. Once you have enough information, you MUST call the submit_classification tool 
   with your final result. Do not respond with plain text — always finish by calling 
   submit_classification.

Guidelines for confidence levels:
- "high": multiple concrete invoice indicators present together in the actual text 
  content (e.g. invoice number + amount, or vendor name + due date) — not just filenames
- "medium": some indicators present but ambiguous, or only a single indicator without 
  supporting structure (e.g. just the word "Rechnung" plus a bare number, with nothing else)
- "low": very little information to base the decision on

Keep the "reason" field concise — one or two sentences explaining your decision."""

def extract_pdf_text(filename , attachments):
    file_data = None
    for att_name, att_data in attachments:
        if att_name == filename:
            file_data = att_data
            # stop at the first match, don't keep overwriting on duplicate names
            break
    
    if file_data is None:
        return f"Attachment '{filename}' not found" 

    try:
        pdf_file = io.BytesIO(file_data)
        full_text= ""

        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                # scanned/blank pages can return None
                if page_text is not None: 
                    full_text += page_text
        return full_text
    except Exception as e:
        # Corrupted PDF, unsupported format, etc. — don't crash, report it
        # back to the model as a tool_result instead.
        return f"Failed to extract PDF text: {e}"

"""
Runs the tool-calling conversation loop that classifies a single email
as an invoice or not.

The model is given the subject, body, and attachment filenames (not the
raw attachment bytes — it must explicitly call extract_pdf_text if it
wants to inspect a PDF's actual content). It either:
- calls submit_classification straight away (subject/body were enough), or
- calls extract_pdf_text first, receives the result, and then decides.

max_iterations caps how many times we'll go back and forth with the
model, so a model that never calls submit_classification can't loop
forever (burning tokens indefinitely).
"""
def classify_email(subject , body , attachments):
    max_iterations = 5
    attachment_names = [att_name for att_name, att_data in attachments ]
    user_message = f'subject: {subject} , body: {body} , attachments_names: {attachment_names}'
    messages = [
        {"role" : "user", "content" : user_message}
    ]

    for i in range(max_iterations):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system_prompt,
                tools=tools,
                messages=messages
        )
        except Exception as e:
            # Network issue, rate limit, API outage, etc. — fail gracefully
            # for this one email instead of crashing the whole pipeline.
            return {"is_invoice": None, "confidence": "low", "reason": f"API call failed: {e}"}
        
        for block in response.content:
            if block.type == "tool_use":
                if block.name == "submit_classification":
                    # Final answer reached — the model's tool input IS our result dict. 
                    return block.input 
                elif block.name == "extract_pdf_text":
                    filename = block.input["filename"]
                    result = extract_pdf_text(filename , attachments)

                    # Append the model's own turn (its tool_use call) and our
                    # tool_result to the conversation, then loop again so the
                    # model can see the PDF content and make its final call.
                    messages.append({"role": "assistant" , "content": response.content})
                    messages.append({
                        "role" : "user",
                        "content" : [
                            {
                                "type" : "tool_result",
                                "tool_use_id" : block.id,
                                "content" : result
                            }
                        ]
                    })
                    # stop scanning blocks, go fetch a new response with the updated messages
                    break
        if response.stop_reason != "tool_use":
            # The model responded with plain text instead of calling a tool —
            # shouldn't happen given the system prompt, but this is caught
            # by the try/except around classify_email() in main.py so it
            # doesn't take down the whole run.
            raise ValueError("Cycle was stopped not for tool's reason")
    # Ran out of iterations without ever reaching submit_classification.
    return {"is_invoice": None, "confidence": "low", "reason": "Model did not reach a final classification within the iteration limit"}





