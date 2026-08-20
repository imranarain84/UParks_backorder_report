"""Sends the daily report email via the Gmail API, authenticated with a
long-lived OAuth refresh token (no app password, no re-auth needed).
"""
import base64
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

GMAIL_TOKEN_URI = "https://oauth2.googleapis.com/token"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _build_service(client_id: str, client_secret: str, refresh_token: str):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GMAIL_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=GMAIL_SCOPES,
    )
    return build("gmail", "v1", credentials=creds)


def send_report_email(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    sender: str,
    recipients: list[str],
    subject: str,
    body_text: str,
    csv_path: Path,
) -> None:
    service = _build_service(client_id, client_secret, refresh_token)

    message = MIMEMultipart()
    message["to"] = ", ".join(recipients)
    message["from"] = sender
    message["subject"] = subject
    message.attach(MIMEText(body_text))

    with csv_path.open("rb") as f:
        attachment = MIMEApplication(f.read(), _subtype="csv")
    attachment.add_header(
        "Content-Disposition", "attachment", filename=csv_path.name
    )
    message.attach(attachment)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
