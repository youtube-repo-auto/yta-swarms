import os
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

load_dotenv()

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    },
    scopes=[
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ],
)

creds = flow.run_local_server(port=8080)

print("\n✅ Kopieer naar .env:\n")
print(f"YOUTUBE_ACCESS_TOKEN={creds.token}")
print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
