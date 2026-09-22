#!/usr/bin/env python3
"""Dedupe NYC charter schools by website host."""

import csv
import sys
from collections import defaultdict
from urllib.parse import urlparse

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import config


def normalize_host(url: str) -> str:
    """Extract and normalize host from URL."""
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc or parsed.path.split("/")[0]
    # Remove www. prefix for grouping
    if host.startswith("www."):
        host = host[4:]
    return host.lower()


def main():
    print("=== Charter School Dedupe ===\n")

    bodies_path = config.DATA_DIR / "bodies.csv"

    # Read all rows
    with open(bodies_path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        all_rows = list(reader)

    # Separate NYC charters from other rows
    nyc_charters = []
    other_rows = []

    for row in all_rows:
        if row.get("segment") == "NYC charter school boards":
            nyc_charters.append(row)
        else:
            other_rows.append(row)

    print(f"Total bodies: {len(all_rows)}")
    print(f"NYC charter schools: {len(nyc_charters)}")
    print(f"Other bodies: {len(other_rows)}")

    # Group NYC charters by website host
    by_host: dict[str, list[dict]] = defaultdict(list)

    for row in nyc_charters:
        host = normalize_host(row.get("website", ""))
        by_host[host].append(row)

    print(f"Distinct hosts: {len(by_host)}")

    # For each host group, keep one row and merge names
    deduped_charters = []

    for host, schools in sorted(by_host.items()):
        if len(schools) == 1:
            deduped_charters.append(schools[0])
            continue

        # Multiple schools share this host - merge
        # Keep the first one as the base (typically the oldest/main school)
        # Sort by body_id to get consistent ordering
        schools = sorted(schools, key=lambda r: r.get("body_id", ""))
        base = dict(schools[0])  # Copy the first row

        # Collect names of merged entries
        merged_names = [s.get("name", "") for s in schools[1:]]
        total_enrollment = sum(
            int(s.get("enrollment", 0) or 0) for s in schools
        )

        # Update base row
        base["enrollment"] = str(total_enrollment) if total_enrollment else ""

        # Add merged names to manual_notes
        existing_notes = base.get("manual_notes", "") or ""
        merge_note = f"Merged with: {'; '.join(merged_names)}"
        if existing_notes:
            base["manual_notes"] = f"{existing_notes}. {merge_note}"
        else:
            base["manual_notes"] = merge_note

        # Update name to indicate it's a network/board
        if len(schools) > 2:
            base["name"] = f"{base['name']} (and {len(schools)-1} others)"

        deduped_charters.append(base)
        print(f"  Merged {len(schools)} schools for {host}")

    print(f"\nAfter dedupe: {len(deduped_charters)} NYC charter boards")

    # Combine and write
    final_rows = other_rows + deduped_charters

    with open(bodies_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(final_rows)

    print(f"Wrote {len(final_rows)} rows to {bodies_path}")

    # Update universe_ledger.csv
    ledger_path = config.DATA_DIR / "universe_ledger.csv"

    with open(ledger_path, newline="") as f:
        reader = csv.DictReader(f)
        ledger_fields = reader.fieldnames
        ledger_rows = list(reader)

    for row in ledger_rows:
        if row.get("segment") == "NYC charter school boards":
            row["note"] = f"Deduped from 277 to {len(deduped_charters)} by website host. Networks share one board."
            row["universe_count"] = str(len(deduped_charters))

    with open(ledger_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ledger_fields)
        writer.writeheader()
        writer.writerows(ledger_rows)

    print(f"Updated universe_ledger.csv")

    return len(deduped_charters)


if __name__ == "__main__":
    new_count = main()
    print(f"\n=== Summary ===")
    print(f"New NYC charter board count: {new_count}")
