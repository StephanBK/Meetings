"""Polite document fetcher with robots.txt compliance and rate limiting."""

import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from . import config


class PoliteFetcher:
    """Downloads documents politely, respecting robots.txt and rate limits."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._robots_cache: dict[str, Optional[RobotFileParser]] = {}
        self._crawl_delay: dict[str, float] = {}
        self._last_request_time: dict[str, float] = {}

    def _parse_crawl_delay(self, robots_text: str) -> Optional[float]:
        """Extract Crawl-delay for our user agent from robots.txt."""
        lines = robots_text.splitlines()
        in_our_section = False
        in_wildcard_section = False
        our_delay = None
        wildcard_delay = None

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Check for User-agent directive
            if line.lower().startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip().lower()
                in_our_section = "meetingsbot" in agent.lower()
                in_wildcard_section = agent == "*"
                continue

            # Check for Crawl-delay directive
            if line.lower().startswith("crawl-delay:"):
                try:
                    delay = float(line.split(":", 1)[1].strip())
                    if in_our_section:
                        our_delay = delay
                    elif in_wildcard_section and our_delay is None:
                        wildcard_delay = delay
                except ValueError:
                    pass

        return our_delay if our_delay is not None else wildcard_delay

    def _get_robots_parser(self, url: str) -> Optional[RobotFileParser]:
        """Get robots.txt parser for the given URL's host."""
        parsed = urlparse(url)
        host = parsed.netloc
        robots_url = f"{parsed.scheme}://{host}/robots.txt"

        if host in self._robots_cache:
            return self._robots_cache[host]

        try:
            self._rate_limit(host)
            resp = self.session.get(robots_url, timeout=10)

            if resp.status_code >= 500:
                # 5xx on robots.txt means stay out entirely
                print(f"  WARNING: robots.txt returned {resp.status_code} for {host}, will not fetch")
                self._robots_cache[host] = None
                return None

            if resp.status_code >= 400:
                # 4xx means no rules (can fetch anything)
                self._robots_cache[host] = RobotFileParser()
                self._robots_cache[host].parse([])
                return self._robots_cache[host]

            # Parse robots.txt
            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())
            self._robots_cache[host] = rp

            # Extract Crawl-delay
            crawl_delay = self._parse_crawl_delay(resp.text)
            if crawl_delay is not None:
                self._crawl_delay[host] = crawl_delay
                print(f"  Crawl-delay for {host}: {crawl_delay}s")

            return rp

        except requests.RequestException as e:
            print(f"  WARNING: Could not fetch robots.txt for {host}: {e}")
            # Treat connection errors as "no rules"
            self._robots_cache[host] = RobotFileParser()
            self._robots_cache[host].parse([])
            return self._robots_cache[host]

    def _can_fetch(self, url: str) -> bool:
        """Check if we're allowed to fetch this URL per robots.txt."""
        rp = self._get_robots_parser(url)
        if rp is None:
            # 5xx on robots.txt means stay out
            return False
        return rp.can_fetch(config.USER_AGENT, url)

    def _rate_limit(self, host: str):
        """Enforce rate limiting per host, honoring Crawl-delay if specified."""
        now = time.time()
        last = self._last_request_time.get(host, 0)
        elapsed = now - last

        # Use Crawl-delay if specified, otherwise default
        delay = self._crawl_delay.get(host, config.REQUEST_DELAY_SECONDS)

        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_time[host] = time.time()

    def fetch_document(self, document_id: int, source_url: str, body_id: str) -> bool:
        """
        Fetch a document and save to disk.

        Returns True if successful, False otherwise.
        """
        from . import db  # Import here to avoid holding connection

        parsed = urlparse(source_url)
        host = parsed.netloc

        # Check robots.txt
        if not self._can_fetch(source_url):
            print(f"  BLOCKED by robots.txt: {source_url}")
            db.execute(
                "UPDATE documents SET status = 'error', error = %s WHERE document_id = %s",
                ("Blocked by robots.txt", document_id),
                commit=True
            )
            return False

        # Rate limit
        self._rate_limit(host)

        try:
            resp = self.session.get(source_url, timeout=60)
            resp.raise_for_status()

            # Check for 403 or other blocks
            if resp.status_code == 403:
                print(f"  BLOCKED (403): {source_url}")
                db.execute(
                    "UPDATE documents SET status = 'error', error = %s WHERE document_id = %s",
                    ("HTTP 403 Forbidden", document_id),
                    commit=True
                )
                return False

            # Read content and calculate hash
            content = resp.content
            sha256 = hashlib.sha256(content).hexdigest()
            file_bytes = len(content)

            # Check if we already have this file (same SHA256)
            existing = db.fetch_one(
                "SELECT file_path FROM documents WHERE sha256 = %s AND file_path IS NOT NULL LIMIT 1",
                (sha256,)
            )

            if existing and existing["file_path"]:
                # Same file already exists, just link to it
                file_path = existing["file_path"]
                print(f"  DUPLICATE (sha256 match): {source_url}")
            else:
                # Save to disk
                body_dir = config.DOWNLOADS_DIR / body_id
                body_dir.mkdir(parents=True, exist_ok=True)

                # Use SHA256 prefix + original filename for uniqueness
                original_name = parsed.path.split("/")[-1]
                if not original_name.endswith(".pdf"):
                    original_name += ".pdf"
                filename = f"{sha256[:8]}_{original_name}"
                file_path = str(body_dir / filename)

                with open(file_path, "wb") as f:
                    f.write(content)

            # Update database
            db.execute(
                """UPDATE documents
                   SET file_path = %s, sha256 = %s, bytes = %s,
                       fetched_at = %s, status = 'fetched'
                   WHERE document_id = %s""",
                (file_path, sha256, file_bytes, datetime.now(), document_id),
                commit=True
            )

            return True

        except requests.RequestException as e:
            print(f"  ERROR fetching {source_url}: {e}")
            db.execute(
                "UPDATE documents SET status = 'error', error = %s WHERE document_id = %s",
                (str(e)[:500], document_id),
                commit=True
            )
            return False


def fetch_new_documents() -> tuple[int, int]:
    """
    Fetch all documents with status 'new'.

    Returns (success_count, error_count).
    """
    from . import db  # Import here to avoid holding connection

    fetcher = PoliteFetcher()

    # Get all new documents - single query, release connection immediately
    docs = list(db.fetch_all(
        """SELECT document_id, source_url, body_id
           FROM documents
           WHERE status = 'new'
           ORDER BY body_id, document_id"""
    ))

    if not docs:
        print("No new documents to fetch.")
        return 0, 0

    print(f"Fetching {len(docs)} documents...")

    success = 0
    errors = 0
    current_body = None

    for doc in docs:
        if doc["body_id"] != current_body:
            current_body = doc["body_id"]
            print(f"\n  {current_body}:")

        if fetcher.fetch_document(doc["document_id"], doc["source_url"], doc["body_id"]):
            success += 1
            print(f"    OK: {doc['source_url'].split('/')[-1]}")
        else:
            errors += 1

    return success, errors
