"""PDF watcher adapter: extracts PDF links from board pages."""

import re
import time
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

from .base import Adapter, DocRef
from .. import config


# Date patterns to try when parsing link text
DATE_PATTERNS = [
    # "September 3, 2026" or "September 03, 2026"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
    # "Sept 3, 2026" or "Sept. 3, 2026"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?\s+(\d{1,2}),?\s+(\d{4})",
    # "9-3-26" or "09-03-26" or "9/3/26"
    r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})",
    # "2026-09-03"
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
]

MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def parse_date(text: str) -> Optional[date]:
    """Parse a date from link text. Returns None if no date found."""
    text_lower = text.lower()

    # Try month name patterns first
    for pattern in DATE_PATTERNS[:2]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            month_str, day_str, year_str = match.groups()
            month = MONTH_MAP.get(month_str.lower().rstrip("."))
            if month:
                day = int(day_str)
                year = int(year_str)
                try:
                    return date(year, month, day)
                except ValueError:
                    continue

    # Try numeric patterns
    # M-D-YY or M/D/YY
    match = re.search(DATE_PATTERNS[2], text)
    if match:
        a, b, c = match.groups()
        a, b, c = int(a), int(b), int(c)
        # Assume M-D-Y format (US style)
        if c < 100:
            c += 2000 if c < 50 else 1900
        if 1 <= a <= 12 and 1 <= b <= 31:
            try:
                return date(c, a, b)
            except ValueError:
                pass

    # YYYY-MM-DD
    match = re.search(DATE_PATTERNS[3], text)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            pass

    return None


def classify_doc_type(link_text: str, url: str) -> tuple[str, Optional[str]]:
    """
    Classify document type from link text and URL.
    Returns (doc_type, meeting_type).
    """
    text_lower = link_text.lower()
    url_lower = url.lower()

    # Check for committee types first
    committee_keywords = [
        ("building", "Buildings and Grounds"),
        ("grounds", "Buildings and Grounds"),
        ("b&g", "Buildings and Grounds"),
        ("b_g", "Buildings and Grounds"),
        ("finance", "Finance"),
        ("education committee", "Education"),
        ("curriculum", "Curriculum"),
        ("policy", "Policy"),
    ]

    meeting_type = None
    for keyword, committee_name in committee_keywords:
        if keyword in text_lower or keyword in url_lower:
            meeting_type = committee_name
            break

    # Check for special meeting types
    if meeting_type is None:
        if "special" in text_lower:
            meeting_type = "special"
        elif "reorg" in text_lower:
            meeting_type = "reorganization"
        elif "work" in text_lower:
            meeting_type = "work session"
        elif "budget" in text_lower:
            meeting_type = "budget"
        else:
            meeting_type = "regular"

    # Determine doc_type
    if "minute" in text_lower or "minute" in url_lower:
        doc_type = "minutes"
    elif "agenda" in text_lower or "agenda" in url_lower:
        # Committee agendas get special type
        if meeting_type and meeting_type not in ("regular", "special", "reorganization", "work session", "budget"):
            doc_type = "committee_agenda"
        else:
            doc_type = "agenda"
    elif "packet" in text_lower:
        doc_type = "packet"
    else:
        # Default based on page context - will be refined by caller
        doc_type = "other"

    return doc_type, meeting_type


class PdfWatcherAdapter(Adapter):
    """Adapter that extracts PDF links from board pages."""

    def __init__(self, backfill_start: date):
        """
        Initialize the adapter.

        Args:
            backfill_start: Only include documents dated on or after this date
        """
        self.backfill_start = backfill_start
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._last_request_time: dict[str, float] = {}

    def _rate_limit(self, host: str):
        """Enforce rate limiting per host."""
        now = time.time()
        last = self._last_request_time.get(host, 0)
        elapsed = now - last
        if elapsed < config.REQUEST_DELAY_SECONDS:
            time.sleep(config.REQUEST_DELAY_SECONDS - elapsed)
        self._last_request_time[host] = time.time()

    def _fetch_page(self, url: str) -> Optional[str]:
        """Fetch a page, respecting rate limits."""
        parsed = urlparse(url)
        host = parsed.netloc

        self._rate_limit(host)

        try:
            resp = self.session.get(url, timeout=30, allow_redirects=True)
            # Check if we got redirected to BoardDocs
            if "boarddocs.com" in resp.url:
                print(f"  WARNING: {url} redirects to BoardDocs, skipping")
                return None
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"  ERROR fetching {url}: {e}")
            return None

    def _extract_pdf_links(self, html: str, base_url: str) -> list[tuple[str, str]]:
        """Extract all PDF links from HTML. Returns list of (url, link_text)."""
        soup = BeautifulSoup(html, "html.parser")
        links = []

        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Only include PDF links
            if not href.lower().endswith(".pdf"):
                continue

            # Make absolute URL
            full_url = urljoin(base_url, href)

            # Get link text
            link_text = a.get_text(strip=True)
            if not link_text:
                # Try to get from title or filename
                link_text = a.get("title", "") or href.split("/")[-1]

            links.append((full_url, link_text))

        return links

    def list_documents(self, body_id: str, pages: list[str]) -> list[DocRef]:
        """Discover PDF documents from the given pages."""
        docs = []
        seen_urls = set()

        for page_url in pages:
            print(f"  Scanning {page_url}")
            html = self._fetch_page(page_url)
            if not html:
                continue

            pdf_links = self._extract_pdf_links(html, page_url)
            print(f"    Found {len(pdf_links)} PDF links")

            for url, link_text in pdf_links:
                # Skip duplicates
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Parse date
                meeting_date = parse_date(link_text)
                if meeting_date is None:
                    # Try parsing from URL/filename
                    meeting_date = parse_date(url)

                # Filter by backfill window
                if meeting_date and meeting_date < self.backfill_start:
                    continue

                # Classify document type
                doc_type, meeting_type = classify_doc_type(link_text, url)

                # If no date could be parsed, still include with warning
                if meeting_date is None:
                    print(f"    WARNING: Could not parse date from '{link_text}'")

                docs.append(DocRef(
                    source_url=url,
                    link_text=link_text,
                    doc_type=doc_type,
                    meeting_date=meeting_date,
                    meeting_type=meeting_type,
                ))

        return docs
