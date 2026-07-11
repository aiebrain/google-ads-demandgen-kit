#!/usr/bin/env python
"""One-time OAuth: turn an OAuth *client* JSON into an authorized_user credential file.

You download the OAuth client JSON from Google Cloud Console (Desktop app type). This
script opens Google's consent screen, you approve, and it writes google_ads_adc.json
containing the refresh_token the MCP server uses to call the Google Ads API.

Run it once on any machine with a browser:
    python oauth_setup.py
It looks for the client JSON at $GOOGLE_ADS_OAUTH_CLIENT_JSON, else
<config-dir>/google_ads_oauth_client.json, else prompts for the path.
Output: <config-dir>/google_ads_adc.json   (config dir = $GOOGLE_ADS_MCP_HOME or ~/.google-ads-mcp)
"""
import json
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# adwords: manage Google Ads. cloud-platform: lets the same creds be used as ADC if needed.
SCOPES = [
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/cloud-platform",
]

home = Path(os.environ.get("GOOGLE_ADS_MCP_HOME", str(Path.home() / ".google-ads-mcp")))
home.mkdir(parents=True, exist_ok=True)
out = home / "google_ads_adc.json"

client_path = os.environ.get("GOOGLE_ADS_OAUTH_CLIENT_JSON")
if not client_path:
    default_client = home / "google_ads_oauth_client.json"
    client_path = str(default_client) if default_client.exists() else input(
        "Path to OAuth client JSON downloaded from Google Cloud: "
    ).strip().strip('"')
client_path = Path(client_path)
if not client_path.exists():
    raise SystemExit(f"OAuth client JSON not found: {client_path}")

flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes=SCOPES)
# open_browser=False prints the URL so it also works over SSH; visit it in any browser.
creds = flow.run_local_server(port=0, prompt="consent", open_browser=False)

info = {
    "type": "authorized_user",
    "client_id": creds.client_id,
    "client_secret": creds.client_secret,
    "refresh_token": creds.refresh_token,
    "scopes": SCOPES,
}
out.write_text(json.dumps(info, indent=2), encoding="utf-8")
print(f"Credentials saved to: {out}")
print("This file (client_id/client_secret/refresh_token) is a SECRET. Never commit or share it.")
