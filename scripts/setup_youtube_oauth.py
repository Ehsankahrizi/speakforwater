#!/usr/bin/env python3
"""
SpeakForWater — setup_youtube_oauth.py

ONE-TIME local setup script to obtain a YouTube refresh token.

Prerequisites:
  1. Go to https://console.cloud.google.com/
  2. Create a project (e.g., "SpeakForWater")
  3. Enable "YouTube Data API v3"
  4. Create OAuth credentials (type: Desktop app)
  5. Download client_secret.json

Then run:
  pip install google-auth-oauthlib
  python scripts/setup_youtube_oauth.py path/to/client_secret.json

A browser tab will open. Approve the YouTube upload permission with
the Google account that owns the channel.

The script will print 3 values to add as GitHub secrets:
  YT_CLIENT_ID
  YT_CLIENT_SECRET
  YT_REFRESH_TOKEN
"""

import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("ERROR: install google-auth-oauthlib first:\n")
    print("  pip install google-auth-oauthlib\n")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    secrets_path = Path(sys.argv[1]).expanduser().resolve()
    if not secrets_path.exists():
        print(f"ERROR: file not found: {secrets_path}")
        sys.exit(1)

    print(f"Using client secrets: {secrets_path}")
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",  # force refresh_token to be returned
        access_type="offline",
    )

    if not creds.refresh_token:
        print("\nERROR: No refresh_token returned. This usually means you've")
        print("already authorized this app. Revoke access at:")
        print("  https://myaccount.google.com/permissions")
        print("then run this script again.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  ADD THESE THREE VALUES AS GITHUB SECRETS")
    print("  Settings → Secrets and variables → Actions → New secret")
    print("=" * 60)
    print(f"\nName:   YT_CLIENT_ID")
    print(f"Value:  {creds.client_id}\n")
    print(f"Name:   YT_CLIENT_SECRET")
    print(f"Value:  {creds.client_secret}\n")
    print(f"Name:   YT_REFRESH_TOKEN")
    print(f"Value:  {creds.refresh_token}\n")
    print("=" * 60)


if __name__ == "__main__":
    main()
