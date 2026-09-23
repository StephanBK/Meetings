"""Google Drive adapter for document discovery using Drive API v3.

This adapter handles bodies that store meeting documents in public Google Drive folders.
Requires a Google API key set in the GOOGLE_API_KEY environment variable.

Usage:
    from meetings.adapters.google_drive import GoogleDriveAdapter
    adapter = GoogleDriveAdapter()
    docs = adapter.list_documents(body_id, [drive_folder_url])
"""

import os
import re
from datetime import date
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests

from .base import Adapter, DocRef
from .pdf_watcher import parse_meeting_date, classify_doc_type


# Google Drive API v3 endpoint
DRIVE_API_FILES = "https://www.googleapis.com/drive/v3/files"


def extract_folder_id(url: str) -> Optional[str]:
    """Extract Google Drive folder ID from various URL formats.

    Supports:
    - https://drive.google.com/drive/folders/{folder_id}
    - https://drive.google.com/drive/folders/{folder_id}?usp=sharing
    - https://drive.google.com/drive/u/0/folders/{folder_id}
    """
    parsed = urlparse(url)

    if "drive.google.com" not in parsed.netloc:
        return None

    # Match /folders/{id} pattern
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', parsed.path)
    if match:
        return match.group(1)

    # Check for id= query parameter (less common)
    params = parse_qs(parsed.query)
    if "id" in params:
        return params["id"][0]

    return None


def extract_file_id(url: str) -> Optional[str]:
    """Extract Google Drive file ID from various URL formats.

    Supports:
    - https://drive.google.com/file/d/{file_id}/view
    - https://drive.google.com/open?id={file_id}
    """
    parsed = urlparse(url)

    if "drive.google.com" not in parsed.netloc:
        return None

    # Match /file/d/{id} pattern
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', parsed.path)
    if match:
        return match.group(1)

    # Check for id= query parameter
    params = parse_qs(parsed.query)
    if "id" in params:
        return params["id"][0]

    return None


class GoogleDriveAdapter(Adapter):
    """Adapter for discovering documents in Google Drive folders."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the adapter.

        Args:
            api_key: Google API key. If not provided, reads from GOOGLE_API_KEY env var.
        """
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Google API key required. Set GOOGLE_API_KEY environment variable.")

        # Backfill window (12 months back)
        today = date.today()
        self.backfill_start = today.replace(year=today.year - 1)

    def list_documents(self, body_id: str, pages: list[str]) -> list[DocRef]:
        """List PDF documents from Google Drive folder URLs.

        Args:
            body_id: The body identifier
            pages: List of Google Drive folder URLs

        Returns:
            List of DocRef objects for PDF files in the folders
        """
        docs = []
        seen_ids = set()

        for page_url in pages:
            folder_id = extract_folder_id(page_url)
            if not folder_id:
                print(f"    WARNING: Could not extract folder ID from {page_url}")
                continue

            try:
                folder_docs = self._list_folder_pdfs(folder_id)
                print(f"    Found {len(folder_docs)} PDFs in folder")

                for doc in folder_docs:
                    if doc.source_url in seen_ids:
                        continue
                    seen_ids.add(doc.source_url)

                    # Filter by backfill window if date is known
                    if doc.meeting_date and doc.meeting_date < self.backfill_start:
                        continue

                    docs.append(doc)

            except Exception as e:
                print(f"    ERROR listing folder {folder_id}: {e}")

        return docs

    def _list_folder_pdfs(self, folder_id: str) -> list[DocRef]:
        """List PDF files in a Google Drive folder using the API.

        Args:
            folder_id: The Google Drive folder ID

        Returns:
            List of DocRef objects for PDF files
        """
        docs = []
        page_token = None

        while True:
            params = {
                "key": self.api_key,
                "q": f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
                "fields": "nextPageToken,files(id,name,webViewLink,createdTime,modifiedTime)",
                "pageSize": 100,
            }

            if page_token:
                params["pageToken"] = page_token

            resp = requests.get(DRIVE_API_FILES, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for file in data.get("files", []):
                file_id = file["id"]
                name = file["name"]
                web_link = file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")

                # Direct download link for PDFs
                download_url = f"https://drive.google.com/uc?export=download&id={file_id}"

                # Parse date from filename
                meeting_date = parse_meeting_date(name, web_link)

                # Classify document type
                doc_type, meeting_type = classify_doc_type(name, web_link)

                if meeting_date is None:
                    print(f"    WARNING: Could not parse date from '{name}'")

                docs.append(DocRef(
                    source_url=download_url,
                    link_text=name,
                    doc_type=doc_type,
                    meeting_date=meeting_date,
                    meeting_type=meeting_type,
                ))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return docs
