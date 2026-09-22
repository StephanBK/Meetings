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

# Full month names for regex
MONTH_FULL = r"January|February|March|April|May|June|July|August|September|October|November|December"
MONTH_ABBR = r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec"


def normalize_dashes(text: str) -> str:
    """Normalize en-dash, em-dash, and other dash variants to regular hyphen."""
    # Replace en-dash (U+2013), em-dash (U+2014), and other variants
    return re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]', '-', text)


def extract_year_from_url(url: str) -> Optional[int]:
    """Extract a 4-digit year from URL path or filename."""
    # Look for 4-digit year in URL
    match = re.search(r'[/_-]?(20\d{2}|19\d{2})[/_.-]', url)
    if match:
        return int(match.group(1))
    # Also check end of URL before .pdf
    match = re.search(r'(20\d{2}|19\d{2})\.pdf', url, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def parse_meeting_date(text: str, url: str = "", fallback_year: Optional[int] = None) -> Optional[date]:
    """
    Parse a meeting date from link text, with URL as fallback for year.

    Handles:
    - Full dates: "June 4, 2026", "September 8, 2026 - Building & Grounds"
    - Any dash type (hyphen, en-dash, em-dash)
    - Month + year only: "January 2009" -> January 1, 2009
    - Day + month only: "July 2 - Meeting" -> infer year from URL or fallback
    """
    # Normalize dashes
    text = normalize_dashes(text)
    url = normalize_dashes(url)

    # Pattern 1: Full month name + day + year
    # "June 4, 2026" or "September 8, 2026 - Meeting"
    match = re.search(
        rf'({MONTH_FULL})\s+(\d{{1,2}}),?\s*-?\s*(\d{{4}})',
        text, re.IGNORECASE
    )
    if match:
        month_str, day_str, year_str = match.groups()
        month = MONTH_MAP.get(month_str.lower())
        if month:
            try:
                return date(int(year_str), month, int(day_str))
            except ValueError:
                pass

    # Pattern 2: Abbreviated month + day + year
    # "Sept 3, 2026" or "Sep. 3, 2026"
    match = re.search(
        rf'({MONTH_ABBR})\.?\s+(\d{{1,2}}),?\s*-?\s*(\d{{4}})',
        text, re.IGNORECASE
    )
    if match:
        month_str, day_str, year_str = match.groups()
        month = MONTH_MAP.get(month_str.lower().rstrip("."))
        if month:
            try:
                return date(int(year_str), month, int(day_str))
            except ValueError:
                pass

    # Pattern 3: Month + year only (no day)
    # "January 2009 School Board Minutes" -> January 1, 2009
    match = re.search(
        rf'({MONTH_FULL})\s+(\d{{4}})\b',
        text, re.IGNORECASE
    )
    if match:
        month_str, year_str = match.groups()
        month = MONTH_MAP.get(month_str.lower())
        if month:
            try:
                return date(int(year_str), month, 1)
            except ValueError:
                pass

    # Pattern 4: Abbreviated month + year only
    match = re.search(
        rf'({MONTH_ABBR})\.?\s+(\d{{4}})\b',
        text, re.IGNORECASE
    )
    if match:
        month_str, year_str = match.groups()
        month = MONTH_MAP.get(month_str.lower().rstrip("."))
        if month:
            try:
                return date(int(year_str), month, 1)
            except ValueError:
                pass

    # Pattern 5: Month + day only (no year) - need to infer year
    # "July 2 - Annual Organizational Meeting"
    match = re.search(
        rf'({MONTH_FULL})\s+(\d{{1,2}})\b(?!\s*,?\s*\d{{4}})',
        text, re.IGNORECASE
    )
    if match:
        month_str, day_str = match.groups()
        month = MONTH_MAP.get(month_str.lower())
        if month:
            # Try to get year from URL first
            year = extract_year_from_url(url)
            if year is None:
                year = fallback_year
            if year:
                try:
                    return date(year, month, int(day_str))
                except ValueError:
                    pass

    # Pattern 6: Numeric M-D-YY or M/D/YY or M-D-YYYY
    match = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})', text)
    if match:
        a, b, c = int(match.group(1)), int(match.group(2)), int(match.group(3))
        # Assume M-D-Y format (US style)
        if c < 100:
            c += 2000 if c < 50 else 1900
        if 1 <= a <= 12 and 1 <= b <= 31:
            try:
                return date(c, a, b)
            except ValueError:
                pass

    # Pattern 7: YYYY-MM-DD
    match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            pass

    # Pattern 8: Try to find year in URL if we found month+day in text
    # but couldn't match above (handles edge cases)
    url_year = extract_year_from_url(url)
    if url_year:
        # Re-try month + day patterns with URL year
        match = re.search(rf'({MONTH_ABBR})\.?\s+(\d{{1,2}})\b', text, re.IGNORECASE)
        if match:
            month_str, day_str = match.groups()
            month = MONTH_MAP.get(month_str.lower().rstrip("."))
            if month:
                try:
                    return date(url_year, month, int(day_str))
                except ValueError:
                    pass

    return None


# Keep old function name for compatibility
def parse_date(text: str) -> Optional[date]:
    """Parse a date from link text. Returns None if no date found."""
    return parse_meeting_date(text, "", None)


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

                # Parse date with URL for year inference
                meeting_date = parse_meeting_date(link_text, url)
                if meeting_date is None:
                    # Try parsing from URL/filename as fallback
                    meeting_date = parse_meeting_date(url, url)

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
