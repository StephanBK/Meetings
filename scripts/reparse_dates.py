#!/usr/bin/env python3
"""Re-parse meeting dates for existing documents."""

import sys
from datetime import date

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import db
from meetings.adapters.pdf_watcher import parse_meeting_date


# Backfill window: documents on or after this date are "in window"
BACKFILL_START = date(2025, 9, 20)


def add_columns_if_missing():
    """Add meeting_date and in_window columns if they don't exist."""
    # Check if columns exist
    cols = db.fetch_all("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'documents' AND column_name IN ('meeting_date', 'in_window')
    """)
    existing = {r['column_name'] for r in cols}

    if 'meeting_date' not in existing:
        print("Adding meeting_date column to documents...")
        db.execute("ALTER TABLE documents ADD COLUMN meeting_date DATE", commit=True)

    if 'in_window' not in existing:
        print("Adding in_window column to documents...")
        db.execute("ALTER TABLE documents ADD COLUMN in_window BOOLEAN", commit=True)


def reparse_all_documents():
    """Re-parse dates for all documents."""
    docs = list(db.fetch_all("""
        SELECT document_id, body_id, source_url, link_text
        FROM documents
        ORDER BY body_id, document_id
    """))

    print(f"\nRe-parsing dates for {len(docs)} documents...")

    stats = {
        'total': len(docs),
        'parsed': 0,
        'in_window': 0,
        'out_of_window': 0,
        'no_date': 0,
        'by_body': {}
    }

    for doc in docs:
        doc_id = doc['document_id']
        body_id = doc['body_id']
        link_text = doc['link_text'] or ''
        source_url = doc['source_url'] or ''

        # Parse date using link_text and URL
        meeting_date = parse_meeting_date(link_text, source_url)

        if meeting_date is None:
            # Try URL as text fallback
            meeting_date = parse_meeting_date(source_url, source_url)

        # Calculate in_window
        in_window = meeting_date >= BACKFILL_START if meeting_date else None

        # Update document
        db.execute("""
            UPDATE documents
            SET meeting_date = %s, in_window = %s
            WHERE document_id = %s
        """, (meeting_date, in_window, doc_id), commit=True)

        # Track stats
        if body_id not in stats['by_body']:
            stats['by_body'][body_id] = {'total': 0, 'parsed': 0, 'in_window': 0}

        stats['by_body'][body_id]['total'] += 1

        if meeting_date:
            stats['parsed'] += 1
            stats['by_body'][body_id]['parsed'] += 1
            if in_window:
                stats['in_window'] += 1
                stats['by_body'][body_id]['in_window'] += 1
            else:
                stats['out_of_window'] += 1
        else:
            stats['no_date'] += 1

    return stats


def link_meetings():
    """Link documents to meeting records where dates match."""
    # Get documents with parsed dates but no meeting_id
    docs = list(db.fetch_all("""
        SELECT d.document_id, d.body_id, d.meeting_date
        FROM documents d
        WHERE d.meeting_date IS NOT NULL AND d.meeting_id IS NULL
    """))

    linked = 0
    for doc in docs:
        # Find or create meeting
        meeting = db.fetch_one("""
            SELECT meeting_id FROM meetings
            WHERE body_id = %s AND meeting_date = %s
        """, (doc['body_id'], doc['meeting_date']))

        if meeting:
            meeting_id = meeting['meeting_id']
        else:
            # Create meeting
            db.execute("""
                INSERT INTO meetings (body_id, meeting_date, meeting_type)
                VALUES (%s, %s, 'regular')
                ON CONFLICT DO NOTHING
            """, (doc['body_id'], doc['meeting_date']), commit=True)

            meeting = db.fetch_one("""
                SELECT meeting_id FROM meetings
                WHERE body_id = %s AND meeting_date = %s
            """, (doc['body_id'], doc['meeting_date']))

            if meeting:
                meeting_id = meeting['meeting_id']
            else:
                continue

        # Link document
        db.execute("""
            UPDATE documents SET meeting_id = %s WHERE document_id = %s
        """, (meeting_id, doc['document_id']), commit=True)
        linked += 1

    return linked


def get_signal_stats():
    """Get signal statistics by in_window status."""
    result = db.fetch_all("""
        SELECT
            d.in_window,
            COUNT(DISTINCT s.signal_id) as signal_count,
            COUNT(DISTINCT d.document_id) as doc_count
        FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        WHERE s.is_signal = TRUE
        GROUP BY d.in_window
    """)
    return {r['in_window']: {'signals': r['signal_count'], 'docs': r['doc_count']} for r in result}


def main():
    print("=" * 60)
    print("Re-parsing meeting dates for existing documents")
    print(f"Backfill window: >= {BACKFILL_START}")
    print("=" * 60)

    # Add columns if needed
    add_columns_if_missing()

    # Re-parse all documents
    stats = reparse_all_documents()

    # Link documents to meetings
    linked = link_meetings()

    # Get signal stats
    signal_stats = get_signal_stats()

    # Print results
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)

    print(f"\nTotal documents: {stats['total']}")
    print(f"Dates parsed: {stats['parsed']} ({100*stats['parsed']/stats['total']:.1f}%)")
    print(f"No date found: {stats['no_date']}")
    print(f"In window (>= {BACKFILL_START}): {stats['in_window']}")
    print(f"Out of window: {stats['out_of_window']}")
    print(f"Documents linked to meetings: {linked}")

    print("\n--- Per Body ---")
    for body_id, body_stats in sorted(stats['by_body'].items()):
        total = body_stats['total']
        parsed = body_stats['parsed']
        in_win = body_stats['in_window']
        print(f"  {body_id}: {total} docs, {parsed} with date ({100*parsed/total:.0f}%), {in_win} in_window")

    print("\n--- Signals by in_window ---")
    for in_window, sig_stats in sorted(signal_stats.items(), key=lambda x: (x[0] is None, x[0])):
        label = "in_window" if in_window else "out_of_window" if in_window is False else "no_date"
        print(f"  {label}: {sig_stats['signals']} signals from {sig_stats['docs']} documents")

    # Remaining unparsed
    unparsed = list(db.fetch_all("""
        SELECT document_id, body_id, link_text, source_url
        FROM documents
        WHERE meeting_date IS NULL
        LIMIT 10
    """))

    if unparsed:
        print(f"\n--- Sample Unparsed Documents ({stats['no_date']} total) ---")
        for doc in unparsed:
            print(f"  [{doc['document_id']}] {doc['body_id']}: {doc['link_text'][:60] if doc['link_text'] else 'no link_text'}...")


if __name__ == "__main__":
    main()
