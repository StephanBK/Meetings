#!/usr/bin/env python3
"""Check file hosts for pilot_44 bodies, obeying robots.txt."""

import csv
import re
import sys
import time
from collections import defaultdict
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import config


# Blocked hosts that we never fetch from
BLOCKED_HOSTS = {
    "go.boarddocs.com",
    "files.smartsites.parentsquare.com",
}


class HostChecker:
    """Fetches pages and extracts PDF link hosts, respecting robots.txt."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._robots_cache: dict[str, RobotFileParser | None] = {}
        self._crawl_delay: dict[str, float] = {}
        self._last_request_time: dict[str, float] = {}

    def _parse_crawl_delay(self, robots_text: str) -> float | None:
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

            if line.lower().startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip().lower()
                in_our_section = "meetingsbot" in agent.lower()
                in_wildcard_section = agent == "*"
                continue

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

    def _get_robots_parser(self, url: str) -> RobotFileParser | None:
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
                print(f"  robots.txt 5xx for {host}, skipping site")
                self._robots_cache[host] = None
                return None

            if resp.status_code >= 400:
                # No robots.txt - allow all
                self._robots_cache[host] = RobotFileParser()
                self._robots_cache[host].parse([])
                return self._robots_cache[host]

            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())
            self._robots_cache[host] = rp

            crawl_delay = self._parse_crawl_delay(resp.text)
            if crawl_delay is not None:
                self._crawl_delay[host] = crawl_delay
                print(f"  Crawl-delay for {host}: {crawl_delay}s")

            return rp

        except requests.RequestException as e:
            print(f"  Could not fetch robots.txt for {host}: {e}")
            self._robots_cache[host] = RobotFileParser()
            self._robots_cache[host].parse([])
            return self._robots_cache[host]

    def _can_fetch(self, url: str) -> bool:
        """Check if we're allowed to fetch this URL per robots.txt."""
        rp = self._get_robots_parser(url)
        if rp is None:
            return False
        return rp.can_fetch(config.USER_AGENT, url)

    def _rate_limit(self, host: str):
        """Enforce rate limiting per host, honoring Crawl-delay."""
        now = time.time()
        last = self._last_request_time.get(host, 0)
        elapsed = now - last

        delay = self._crawl_delay.get(host, config.REQUEST_DELAY_SECONDS)

        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_time[host] = time.time()

    def get_pdf_hosts(self, page_url: str) -> dict[str, int]:
        """
        Fetch a page and extract PDF link hosts.

        Returns dict of {host: count}.
        """
        if not page_url:
            return {}

        parsed = urlparse(page_url)
        host = parsed.netloc

        # Check if this host is blocked
        if host in BLOCKED_HOSTS:
            print(f"  BLOCKED: {host}")
            return {}

        # Check robots.txt
        if not self._can_fetch(page_url):
            print(f"  DISALLOWED by robots.txt: {page_url}")
            return {}

        # Rate limit and fetch
        self._rate_limit(host)

        try:
            resp = self.session.get(page_url, timeout=30)
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {page_url}")
                return {}

            html = resp.text

        except requests.RequestException as e:
            print(f"  ERROR: {e}")
            return {}

        # Extract PDF links
        # Look for href="...pdf" or href='...pdf'
        pdf_pattern = re.compile(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', re.IGNORECASE)
        matches = pdf_pattern.findall(html)

        hosts: dict[str, int] = defaultdict(int)
        for link in matches:
            # Resolve relative URLs
            full_url = urljoin(page_url, link)
            link_parsed = urlparse(full_url)
            link_host = link_parsed.netloc
            if link_host:
                hosts[link_host] += 1

        return dict(hosts)


def main():
    print("=== File Host Check for pilot_44 ===\n")

    # Read pilot_44.csv
    pilot_path = config.DATA_DIR / "pilot_44.csv"
    bodies = []
    with open(pilot_path, newline="") as f:
        reader = csv.DictReader(f)
        bodies = list(reader)

    print(f"Bodies to check: {len(bodies)}\n")

    checker = HostChecker()
    results = []  # (body_name, host, count)

    # Stats
    smartsites_bodies = set()
    blocked_bodies = set()
    allowed_bodies = set()

    for body in bodies:
        name = body["name"]
        url = body["board_page_url"] or body["website"]

        print(f"{name}:")
        if not url:
            print("  No URL")
            continue

        hosts = checker.get_pdf_hosts(url)

        if not hosts:
            print("  No PDF links found")
            continue

        for host, count in sorted(hosts.items()):
            results.append((name, host, count))
            print(f"  {host}: {count} links")

            if host == "files.smartsites.parentsquare.com":
                smartsites_bodies.add(name)
            elif host in BLOCKED_HOSTS:
                blocked_bodies.add(name)
            else:
                allowed_bodies.add(name)

    # Write output CSV
    output_path = config.DATA_DIR / "pilot_44_hosts.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["body", "host", "link_count"])
        for row in results:
            writer.writerow(row)

    print(f"\nWrote {len(results)} rows to {output_path}")

    # Report stats
    print("\n=== Summary ===")
    print(f"Bodies with files.smartsites.parentsquare.com: {len(smartsites_bodies)}")
    print(f"Bodies with other blocked hosts: {len(blocked_bodies - smartsites_bodies)}")
    print(f"Bodies with allowed hosts: {len(allowed_bodies)}")


if __name__ == "__main__":
    main()
