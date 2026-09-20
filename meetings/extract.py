"""PDF text extraction with OCR fallback for scanned documents."""

from datetime import datetime
from pathlib import Path

import pdfplumber
from pdf2image import convert_from_path
import pytesseract

from . import db

# Threshold: if average characters per page is below this, use OCR
CHARS_PER_PAGE_THRESHOLD = 100

# DPI for rendering scanned pages (about 130 as specified)
OCR_DPI = 130


def extract_native_text(pdf_path: str) -> tuple[str, int]:
    """
    Extract text from PDF using pdfplumber (native extraction).

    Returns (text, page_count).
    """
    text_parts = []
    page_count = 0

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)

    return "\n\n".join(text_parts), page_count


def extract_ocr_text(pdf_path: str) -> tuple[str, int]:
    """
    Extract text from PDF using OCR (for scanned documents).

    Returns (text, page_count).
    """
    # Convert PDF pages to images
    images = convert_from_path(pdf_path, dpi=OCR_DPI)
    page_count = len(images)

    text_parts = []
    for i, image in enumerate(images):
        # OCR the image
        page_text = pytesseract.image_to_string(image)
        text_parts.append(page_text)

    return "\n\n".join(text_parts), page_count


def extract_document(document_id: int, file_path: str) -> tuple[bool, str]:
    """
    Extract text from a document.

    Returns (success, text_method).
    """
    if not file_path or not Path(file_path).exists():
        db.execute(
            "UPDATE documents SET status = 'error', error = %s WHERE document_id = %s",
            ("File not found", document_id),
            commit=True
        )
        return False, ""

    try:
        # First try native extraction
        text, page_count = extract_native_text(file_path)

        # Check if we got enough text (100 chars per page average)
        chars_per_page = len(text) / page_count if page_count > 0 else 0

        if chars_per_page < CHARS_PER_PAGE_THRESHOLD:
            # Likely a scanned document, use OCR
            print(f"    Low text density ({chars_per_page:.0f} chars/page), using OCR...")
            text, page_count = extract_ocr_text(file_path)
            text_method = "ocr"
        else:
            text_method = "native"

        # Update database
        db.execute(
            """UPDATE documents
               SET text = %s, text_method = %s, pages = %s, status = 'extracted'
               WHERE document_id = %s""",
            (text, text_method, page_count, document_id),
            commit=True
        )

        return True, text_method

    except Exception as e:
        db.execute(
            "UPDATE documents SET status = 'error', error = %s WHERE document_id = %s",
            (str(e)[:500], document_id),
            commit=True
        )
        return False, ""


def extract_fetched_documents() -> tuple[int, int, int]:
    """
    Extract text from all documents with status 'fetched'.

    Returns (native_count, ocr_count, error_count).
    """
    # Get all fetched documents
    docs = list(db.fetch_all(
        """SELECT document_id, file_path, body_id
           FROM documents
           WHERE status = 'fetched'
           ORDER BY body_id, document_id"""
    ))

    if not docs:
        print("No fetched documents to extract.")
        return 0, 0, 0

    print(f"Extracting text from {len(docs)} documents...")

    native_count = 0
    ocr_count = 0
    error_count = 0
    current_body = None

    for doc in docs:
        if doc["body_id"] != current_body:
            current_body = doc["body_id"]
            print(f"\n  {current_body}:")

        filename = Path(doc["file_path"]).name if doc["file_path"] else "unknown"
        success, text_method = extract_document(doc["document_id"], doc["file_path"])

        if success:
            if text_method == "native":
                native_count += 1
                print(f"    OK (native): {filename}")
            else:
                ocr_count += 1
                print(f"    OK (ocr): {filename}")
        else:
            error_count += 1
            print(f"    ERROR: {filename}")

    return native_count, ocr_count, error_count
