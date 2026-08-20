"""Run this ONCE, locally, to get a Gmail OAuth refresh token.

This opens your browser for a one-time consent screen, then prints the
client_id, client_secret, and refresh_token you need to put into GitHub
Secrets. It never needs to run again unless you revoke access.

Usage:
    pip install google-auth-oauthlib
    python scripts/get_gmail_refresh_token.py --client-secret /path/to/client_secret.json
"""
import argparse
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client-secret",
        required=True,
        help="Path to the client_secret.json downloaded from Google Cloud Console "
        "(OAuth Client ID, type: Desktop app)",
    )
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(args.client_secret) as f:
        client_config = json.load(f)
    installed = client_config.get("installed") or client_config.get("web")

    print("\n--- Save these as GitHub Secrets ---")
    print(f"GMAIL_CLIENT_ID={installed['client_id']}")
    print(f"GMAIL_CLIENT_SECRET={installed['client_secret']}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("-------------------------------------\n")


if __name__ == "__main__":
    main()
