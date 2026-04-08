"""Google Drive API wrapper using service account auth."""

import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
    """Build Drive v3 service from service account credentials in env var."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON environment variable is required. "
            "Set it to the JSON content of your GCP service account key."
        )
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)
