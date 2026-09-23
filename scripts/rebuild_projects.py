#!/usr/bin/env python3
"""Rebuild projects table with scope similarity grouping.

Projects are grouped by:
- body_id
- normalized building
- primary trade
- scope similarity (token overlap >= 0.4 OR same building + same trade + 3+ content words)
"""

import sys
import re
from collections import defaultdict

sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import db


STAGE_ORDER = ['1_needs', '1_problem', '2_study', '2_planning', '3_funding', '4_design', '5_bid', '6_award', '7_construction', '8_closeout']

# Standard English stop words
STOP_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of',
    'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'this', 'that', 'these',
    'those', 'it', 'its', 'as', 'if', 'not', 'all', 'any', 'each', 'every',
    'some', 'one', 'two', 'three', 'new', 'old', 'first', 'last', 'such'
}

# Domain-specific stop words (appear in nearly every construction signal)
DOMAIN_STOP_WORDS = {
    'construction', 'work', 'project', 'contract', 'services', 'performed',
    'change', 'order', 'credit', 'bond', 'issue', 'approved', 'completed',
    'awarded', 'vendor', 'settlement', 'payment', 'amount', 'total',
    'district', 'school', 'board', 'meeting', 'motion', 'resolution',
    'progress', 'status', 'update', 'report', 'item', 'bid', 'proposal'
}


def stage_rank(stage):
    """Return numeric rank for stage ordering."""
    if not stage:
        return 0
    try:
        return STAGE_ORDER.index(stage) + 1
    except ValueError:
        return 0


def normalize_building(b):
    """Normalize building name for comparison."""
    if not b:
        return ''
    b = b.lower()
    b = re.sub(r'[^a-z0-9\s]', '', b)
    b = ' '.join(b.split())
    return b


def primary_trade(trades):
    """Get primary (first) trade."""
    if not trades:
        return ''
    return trades[0] if trades else ''


def content_words(text):
    """Extract content words (excluding stop words and domain stop words)."""
    if not text:
        return set()
    words = re.findall(r'[a-z]+', text.lower())
    all_stops = STOP_WORDS | DOMAIN_STOP_WORDS
    return set(w for w in words if len(w) > 2 and w not in all_stops)


def token_overlap(text1, text2):
    """Calculate Jaccard-like token overlap."""
    words1 = content_words(text1)
    words2 = content_words(text2)
    if not words1 or not words2:
        return 0.0
    intersection = len(words1 & words2)
    union = len(words1 | words2)
    return intersection / union if union > 0 else 0.0


def should_merge(s1, s2):
    """Check if two signals should be in the same project.

    Merge requires:
    - Same body_id (always)
    - Same normalized building (must both have building and match)
    - Same primary trade (must both have trade and match)
    - AND scope similarity: token overlap >= 0.4 OR 3+ content words overlap
    """
    # Must be same body
    if s1['body_id'] != s2['body_id']:
        return False

    b1 = normalize_building(s1['building'])
    b2 = normalize_building(s2['building'])
    t1 = primary_trade(s1['trades'])
    t2 = primary_trade(s2['trades'])

    # Must have same building (both non-empty and equal)
    if not b1 or not b2 or b1 != b2:
        return False

    # Must have same primary trade (both non-empty and equal)
    if not t1 or not t2 or t1 != t2:
        return False

    # Now check scope similarity
    # Option 1: token overlap >= 0.4
    overlap = token_overlap(s1['scope_summary'], s2['scope_summary'])
    if overlap >= 0.4:
        return True

    # Option 2: 3+ content words overlap
    words1 = content_words(s1['scope_summary'])
    words2 = content_words(s2['scope_summary'])
    if len(words1 & words2) >= 3:
        return True

    return False


class UnionFind:
    """Union-Find data structure for clustering signals."""

    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        px, py = self.find(x), self.find(y)
        if px == py:
            return
        if self.rank[px] < self.rank[py]:
            px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]:
            self.rank[px] += 1


def main():
    print("=== Rebuilding Projects with Scope Similarity ===\n")

    # Clear existing
    db.execute('UPDATE signals SET project_id = NULL', commit=True)
    db.execute('DELETE FROM projects', commit=True)

    # Get all signals
    signals = list(db.fetch_all('''
        SELECT s.signal_id, s.building, s.stage, s.scope_summary, s.trades,
               d.body_id, d.meeting_date
        FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        WHERE s.is_signal = true
        ORDER BY d.body_id, d.meeting_date
    '''))

    print(f"Processing {len(signals)} signals...")

    # Group signals by body first (for efficiency)
    by_body = defaultdict(list)
    for i, s in enumerate(signals):
        by_body[s['body_id']].append((i, s))

    # Use Union-Find to cluster signals
    uf = UnionFind(len(signals))

    for body_id, body_signals in by_body.items():
        # Compare all pairs within body (O(n^2) per body, but bodies are small)
        for i in range(len(body_signals)):
            for j in range(i + 1, len(body_signals)):
                idx1, s1 = body_signals[i]
                idx2, s2 = body_signals[j]
                if should_merge(s1, s2):
                    uf.union(idx1, idx2)

    # Build clusters
    clusters = defaultdict(list)
    for i in range(len(signals)):
        root = uf.find(i)
        clusters[root].append(i)

    print(f"Created {len(clusters)} projects")

    # Insert projects
    project_data = []
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            for root, indices in clusters.items():
                cluster_signals = [signals[i] for i in indices]

                # Aggregate project data
                body_id = cluster_signals[0]['body_id']
                buildings = [s['building'] for s in cluster_signals if s['building']]
                building = buildings[0] if buildings else None

                # First scope_summary as representative
                scope_summary = cluster_signals[0]['scope_summary']

                # Dates
                dates = [s['meeting_date'] for s in cluster_signals if s['meeting_date']]
                first_date = min(dates) if dates else None
                last_date = max(dates) if dates else None

                # Latest stage
                latest_stage = max(cluster_signals, key=lambda s: stage_rank(s['stage']))['stage']

                signal_count = len(cluster_signals)

                cur.execute('''
                    INSERT INTO projects (body_id, building, scope_summary, first_signal_date, last_signal_date, latest_stage, signal_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING project_id
                ''', (body_id, building, scope_summary, first_date, last_date, latest_stage, signal_count))

                project_id = cur.fetchone()[0]
                project_data.append({
                    'project_id': project_id,
                    'body_id': body_id,
                    'building': building,
                    'signal_count': signal_count,
                    'latest_stage': latest_stage
                })

                for i in indices:
                    cur.execute('UPDATE signals SET project_id = %s WHERE signal_id = %s',
                                (project_id, signals[i]['signal_id']))

            conn.commit()

    # Statistics
    signal_counts = [p['signal_count'] for p in project_data]
    mean_signals = sum(signal_counts) / len(signal_counts)
    max_signals = max(signal_counts)

    print(f"\n=== Results ===")
    print(f"Signals: {len(signals)}")
    print(f"Projects: {len(project_data)}")
    print(f"Signals per project: mean={mean_signals:.1f}, max={max_signals}")

    # 10 largest projects
    print(f"\n=== 10 Largest Projects ===")
    largest = sorted(project_data, key=lambda p: p['signal_count'], reverse=True)[:10]

    for p in largest:
        body = db.fetch_one("SELECT name FROM bodies WHERE body_id = %s", (p['body_id'],))
        body_name = body['name'][:30] if body else p['body_id']
        building = (p['building'] or '-')[:20]
        print(f"  {p['signal_count']:3d} signals: {body_name} / {building} / {p['latest_stage']}")

    # Check if max exceeds 60
    if max_signals > 60:
        print(f"\nWARNING: Max signals ({max_signals}) exceeds 60!")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
