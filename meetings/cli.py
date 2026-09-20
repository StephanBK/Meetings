"""Command-line interface for the meetings pipeline."""

import argparse
import csv
import sys
from pathlib import Path
from datetime import datetime

from . import db, config


def cmd_initdb(args):
    """Initialize database schema."""
    print("Initializing database schema...")
    db.init_schema()
    print("Schema created successfully.")


def cmd_load_bodies(args):
    """Load bodies from CSV file."""
    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"Error: File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading bodies from {csv_path}...")

    # Read CSV
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Prepare insert SQL with ON CONFLICT for idempotency
    insert_sql = """
        INSERT INTO bodies (
            body_id, name, segment, county, website, board_page_url,
            platform, coverage_level, blocker_code, enrollment,
            expected_meetings_per_year, own_site_doc_links,
            last_checked, last_success, manual_notes
        ) VALUES (
            %(body_id)s, %(name)s, %(segment)s, %(county)s, %(website)s, %(board_page_url)s,
            %(platform)s, %(coverage_level)s, %(blocker_code)s, %(enrollment)s,
            %(expected_meetings_per_year)s, %(own_site_doc_links)s,
            %(last_checked)s, %(last_success)s, %(manual_notes)s
        )
        ON CONFLICT (body_id) DO UPDATE SET
            name = EXCLUDED.name,
            segment = EXCLUDED.segment,
            county = EXCLUDED.county,
            website = EXCLUDED.website,
            board_page_url = EXCLUDED.board_page_url,
            platform = EXCLUDED.platform,
            coverage_level = EXCLUDED.coverage_level,
            blocker_code = EXCLUDED.blocker_code,
            enrollment = EXCLUDED.enrollment,
            expected_meetings_per_year = EXCLUDED.expected_meetings_per_year,
            own_site_doc_links = EXCLUDED.own_site_doc_links,
            last_checked = EXCLUDED.last_checked,
            last_success = EXCLUDED.last_success,
            manual_notes = EXCLUDED.manual_notes
    """

    def parse_int(val):
        """Parse integer, return None for empty strings."""
        if val is None or val.strip() == "":
            return None
        try:
            return int(val)
        except ValueError:
            return None

    def parse_timestamp(val):
        """Parse timestamp, return None for empty strings."""
        if val is None or val.strip() == "":
            return None
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            # Try date-only format
            try:
                return datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                return None

    def empty_to_none(val):
        """Convert empty string to None."""
        if val is None or val.strip() == "":
            return None
        return val

    # Transform rows to params
    params_list = []
    for row in rows:
        params = {
            "body_id": row["body_id"],
            "name": row["name"],
            "segment": empty_to_none(row.get("segment")),
            "county": empty_to_none(row.get("county")),
            "website": empty_to_none(row.get("website")),
            "board_page_url": empty_to_none(row.get("board_page_url")),
            "platform": empty_to_none(row.get("platform")),
            "coverage_level": parse_int(row.get("coverage_level")) or 0,
            "blocker_code": empty_to_none(row.get("blocker_code")),
            "enrollment": parse_int(row.get("enrollment")),
            "expected_meetings_per_year": parse_int(row.get("expected_meetings_per_year")),
            "own_site_doc_links": parse_int(row.get("own_site_doc_links")),
            "last_checked": parse_timestamp(row.get("last_checked")),
            "last_success": parse_timestamp(row.get("last_success")),
            "manual_notes": empty_to_none(row.get("manual_notes")),
        }
        params_list.append(params)

    # Insert all rows
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            for params in params_list:
                cur.execute(insert_sql, params)
        conn.commit()

    print(f"Loaded {len(params_list)} bodies.")


def main():
    parser = argparse.ArgumentParser(
        prog="meetings",
        description="Meetings pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # initdb command
    subparsers.add_parser("initdb", help="Initialize database schema")

    # load-bodies command
    load_parser = subparsers.add_parser("load-bodies", help="Load bodies from CSV")
    load_parser.add_argument("csv_file", help="Path to bodies CSV file")

    args = parser.parse_args()

    if args.command == "initdb":
        cmd_initdb(args)
    elif args.command == "load-bodies":
        cmd_load_bodies(args)


if __name__ == "__main__":
    main()
