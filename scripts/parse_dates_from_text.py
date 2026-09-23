#!/usr/bin/env python3
"""Parse meeting dates from document text for docs with NULL meeting_date.

Looks for date patterns in the first 3,000 characters of extracted text.
Updates meeting_date and in_window flag.
"""

import sys
import re
from datetime import date, datetime, timedelta

sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import db

# In-window is last 12 months
IN_WINDOW_DAYS = 365


def parse_date(text):
    """Parse a date from text, returning the first valid date found.

    Looks for patterns like:
    - Month DD, YYYY (e.g., "September 16, 2026")
    - MM/DD/YYYY or MM/DD/YY
    - YYYY-MM-DD
    - DD Month YYYY (e.g., "16 September 2026")
    """
    if not text:
        return None

    # Limit to first 3000 chars
    text = text[:3000]

    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }

    # Pattern 1: Month DD, YYYY (most common in meeting minutes)
    pattern1 = r'\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+(\d{1,2}),?\s+(\d{4})\b'
    match = re.search(pattern1, text, re.IGNORECASE)
    if match:
        month_name, day, year = match.groups()
        month = months.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day))
            except ValueError:
                pass

    # Pattern 2: DD Month YYYY
    pattern2 = r'\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})\b'
    match = re.search(pattern2, text, re.IGNORECASE)
    if match:
        day, month_name, year = match.groups()
        month = months.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day))
            except ValueError:
                pass

    # Pattern 3: MM/DD/YYYY
    pattern3 = r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b'
    match = re.search(pattern3, text)
    if match:
        month, day, year = match.groups()
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            pass

    # Pattern 4: YYYY-MM-DD (ISO format)
    pattern4 = r'\b(\d{4})-(\d{2})-(\d{2})\b'
    match = re.search(pattern4, text)
    if match:
        year, month, day = match.groups()
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            pass

    return None


def main():
    print("=== Parsing Dates from Document Text ===\n")

    today = date.today()
    window_start = today - timedelta(days=IN_WINDOW_DAYS)

    # Get docs with NULL meeting_date and text
    docs = list(db.fetch_all('''
        SELECT document_id, body_id, text, in_window
        FROM documents
        WHERE meeting_date IS NULL
          AND text IS NOT NULL
          AND LENGTH(text) > 0
    '''))

    print(f"Documents with NULL meeting_date and text: {len(docs)}")

    updated = 0
    still_null = 0
    turned_out_of_window = []

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            for doc in docs:
                parsed_date = parse_date(doc['text'])

                if parsed_date:
                    # Determine in_window
                    new_in_window = parsed_date >= window_start

                    cur.execute('''
                        UPDATE documents
                        SET meeting_date = %s, in_window = %s
                        WHERE document_id = %s
                    ''', (parsed_date, new_in_window, doc['document_id']))

                    updated += 1

                    # Check if this doc was classified and turned out_of_window
                    if not new_in_window and doc['in_window'] is True:
                        # Check if it has LLM runs (was classified)
                        llm_run = db.fetch_one('''
                            SELECT input_tokens, output_tokens
                            FROM llm_runs
                            WHERE document_id = %s
                        ''', (doc['document_id'],))
                        if llm_run:
                            turned_out_of_window.append({
                                'document_id': doc['document_id'],
                                'date': parsed_date,
                                'input_tokens': llm_run['input_tokens'],
                                'output_tokens': llm_run['output_tokens']
                            })
                else:
                    still_null += 1

            conn.commit()

    print(f"\n=== Results ===")
    print(f"Documents now with date: {updated}")
    print(f"Documents still without date: {still_null}")

    if turned_out_of_window:
        total_input = sum(d['input_tokens'] for d in turned_out_of_window)
        total_output = sum(d['output_tokens'] for d in turned_out_of_window)
        # Haiku pricing: $1/M input, $5/M output
        cost = (total_input * 1.0 / 1_000_000) + (total_output * 5.0 / 1_000_000)
        print(f"\nClassified docs that turned out_of_window: {len(turned_out_of_window)}")
        print(f"  Total tokens: {total_input:,} input, {total_output:,} output")
        print(f"  Token cost: ${cost:.2f}")
        for d in turned_out_of_window[:5]:
            print(f"    Doc {d['document_id']}: {d['date']}")
    else:
        print(f"\nNo classified docs turned out_of_window")

    return 0


if __name__ == "__main__":
    sys.exit(main())
