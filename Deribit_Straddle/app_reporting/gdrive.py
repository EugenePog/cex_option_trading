"""Upload a CSV into Google Drive as a Sheet, replacing any matching name."""
import logging
import os

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .google_auth import get_credentials

log = logging.getLogger(__name__)
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def upload_csv_as_gsheet(csv_path: str, sheet_name: str | None = None,
                         folder_id: str | None = None) -> tuple[str, str]:
    """Upload-or-replace ``csv_path`` as a Google Sheet.

    Returns:
        ``(file_id, web_view_link)`` — the link is the user-facing URL.
    """
    service = build("drive", "v3", credentials=get_credentials())
    name = sheet_name or os.path.splitext(os.path.basename(csv_path))[0]

    query = f"name = '{name}' and mimeType = '{SHEET_MIME}' and trashed = false"
    if folder_id:
        query += f" and '{folder_id}' in parents"

    existing = service.files().list(
        q=query, fields="files(id)",
    ).execute().get("files", [])

    media = MediaFileUpload(csv_path, mimetype="text/csv", resumable=True)

    if existing:
        file_id = existing[0]["id"]
        f = service.files().update(
            fileId=file_id, media_body=media, fields="id, webViewLink",
        ).execute()
        log.info("Updated existing sheet: %s", f.get("webViewLink"))
    else:
        meta = {"name": name, "mimeType": SHEET_MIME}
        if folder_id:
            meta["parents"] = [folder_id]
        f = service.files().create(
            body=meta, media_body=media, fields="id, webViewLink",
        ).execute()
        log.info("Created new sheet: %s", f.get("webViewLink"))

    return f["id"], f.get("webViewLink", "")