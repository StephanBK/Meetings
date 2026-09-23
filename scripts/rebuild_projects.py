#!/usr/bin/env python3
"""Rebuild projects table by grouping signals on (body_id, building).

Projects aggregate multiple signals about the same building/facility,
tracking stage progression over time.
"""

import sys
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import db


STAGE_ORDER = ['1_needs', '1_problem', '2_study', '2_planning', '3_funding', '4_design', '5_bid', '6_award', '7_construction', '8_closeout']


def stage_rank(stage):
    """Return numeric rank for stage ordering."""
    if not stage:
        return 0
    try:
        return STAGE_ORDER.index(stage) + 1
    except ValueError:
        return 0


def main():
    print("=== Rebuilding Projects Table ===\n")

    # Clear in correct order
    db.execute('UPDATE signals SET project_id = NULL', commit=True)
    db.execute('DELETE FROM projects', commit=True)

    # Get all signals
    signals = list(db.fetch_all('''
        SELECT s.signal_id, s.building, s.stage, s.scope_summary, s.trades,
               d.body_id, d.meeting_date
        FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        WHERE s.is_signal = true
        ORDER BY d.body_id, s.building, d.meeting_date
    '''))

    print(f"Processing {len(signals)} signals...")

    projects = {}  # (body_id, building) -> project data

    for s in signals:
        building = s['building'] or ''
        key = (s['body_id'], building)

        if key not in projects:
            projects[key] = {
                'body_id': s['body_id'],
                'building': building if building else None,
                'scope_summary': s['scope_summary'],
                'first_signal_date': s['meeting_date'],
                'last_signal_date': s['meeting_date'],
                'latest_stage': s['stage'],
                'signal_count': 0,
                'signals': [],
            }

        p = projects[key]
        p['signal_count'] += 1
        p['signals'].append(s['signal_id'])

        if s['meeting_date']:
            if p['first_signal_date'] is None or s['meeting_date'] < p['first_signal_date']:
                p['first_signal_date'] = s['meeting_date']
            if p['last_signal_date'] is None or s['meeting_date'] > p['last_signal_date']:
                p['last_signal_date'] = s['meeting_date']

        if stage_rank(s['stage']) > stage_rank(p['latest_stage']):
            p['latest_stage'] = s['stage']

    print(f"Created {len(projects)} projects")

    # Insert projects
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            for key, p in projects.items():
                cur.execute('''
                    INSERT INTO projects (body_id, building, scope_summary, first_signal_date, last_signal_date, latest_stage, signal_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING project_id
                ''', (p['body_id'], p['building'], p['scope_summary'], p['first_signal_date'], p['last_signal_date'], p['latest_stage'], p['signal_count']))

                project_id = cur.fetchone()[0]

                for signal_id in p['signals']:
                    cur.execute('UPDATE signals SET project_id = %s WHERE signal_id = %s', (project_id, signal_id))

            conn.commit()

    # Statistics
    stats = db.fetch_one('''
        SELECT COUNT(*) as cnt, AVG(signal_count) as mean, MAX(signal_count) as max
        FROM projects
    ''')

    print(f"\n=== Results ===")
    print(f"Signals: {len(signals)}")
    print(f"Projects: {stats['cnt']}")
    print(f"Signals per project: mean={stats['mean']:.1f}, max={stats['max']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
