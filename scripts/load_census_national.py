#!/usr/bin/env python3
"""Load 2022 Census of Governments units as level 0 bodies.

Loads all sheets from the Census file:
- General Purpose (counties, municipalities, townships)
- Special District (fire, water, library, etc.)
- School District (independent school districts)
- DEP School Dist (dependent school districts - under another government)
"""

import sys
import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import config


def load_sheet(xlsx, sheet_name: str) -> pd.DataFrame:
    """Load a sheet and normalize columns."""
    df = pd.read_excel(xlsx, sheet_name=sheet_name)

    # Filter to active only if column exists
    if 'IS_ACTIVE' in df.columns:
        df = df[df['IS_ACTIVE'] == 'Y'].copy()

    # Normalize unit type based on sheet
    if sheet_name == 'General Purpose':
        # UNIT_TYPE like "1 - COUNTY", "2 - MUNICIPAL", "3 - TOWNSHIP"
        df['unit_type_clean'] = df['UNIT_TYPE'].apply(
            lambda x: x.split(' - ')[1].title() if pd.notna(x) and ' - ' in str(x) else str(x)
        )
    elif sheet_name == 'Special District':
        # FUNCTION_NAME like "Fire Protection", "Library", etc.
        df['unit_type_clean'] = 'Special District - ' + df['FUNCTION_NAME'].fillna('Other')
    elif sheet_name == 'School District':
        df['unit_type_clean'] = 'School District'
    elif sheet_name == 'DEP School Dist':
        df['unit_type_clean'] = 'Dependent School District'
    else:
        df['unit_type_clean'] = sheet_name

    return df


def main():
    print("=== Loading 2022 Census of Governments (All Sheets) ===\n")

    xlsx_path = "/tmp/Govt_Units_2022_Final.xlsx"
    print(f"Loading {xlsx_path}...")
    xlsx = pd.ExcelFile(xlsx_path)

    print(f"Sheets: {xlsx.sheet_names}\n")

    all_bodies = []
    sheet_stats = []

    for sheet_name in xlsx.sheet_names:
        df = load_sheet(xlsx, sheet_name)

        # Count with websites
        if 'WEB_ADDRESS' in df.columns:
            has_website = df['WEB_ADDRESS'].notna() & (df['WEB_ADDRESS'] != '')
            web_count = int(has_website.sum())
            websites = df['WEB_ADDRESS'].fillna('')
        else:
            web_count = 0
            websites = pd.Series([''] * len(df))
            has_website = pd.Series([False] * len(df))

        print(f"{sheet_name}: {len(df)} active, {web_count} with website")
        sheet_stats.append({
            'sheet': sheet_name,
            'count': len(df),
            'with_website': web_count
        })

        # Create segment by state and unit type
        def make_segment(row):
            state = row.get('STATE', 'XX')
            unit_type = row.get('unit_type_clean', 'Unknown')
            return f"{state} {unit_type}"

        segments = df.apply(make_segment, axis=1)

        # Get population if available
        if 'POPULATION' in df.columns:
            populations = df['POPULATION']
        else:
            populations = pd.Series([None] * len(df))

        # Build manual_notes
        def make_notes(row):
            unit_type = row.get('unit_type_clean', 'Unknown')
            pop = row.get('POPULATION')
            if pd.notna(pop):
                return f"Census 2022 {unit_type}, pop {int(pop)}"
            else:
                return f"Census 2022 {unit_type}"

        notes = df.apply(make_notes, axis=1)

        # Get county if available
        if 'COUNTY_AREA_NAME' in df.columns:
            counties = df['COUNTY_AREA_NAME'].fillna('')
        else:
            counties = pd.Series([''] * len(df))

        # Create bodies dataframe
        bodies = pd.DataFrame({
            'body_id': 'COG-' + df['CENSUS_ID_PID6'].astype(str),
            'name': df['UNIT_NAME'],
            'segment': segments,
            'county': counties,
            'schools': '',
            'enrollment': '',
            'website': websites,
            'coverage_level': 0,
            'blocker_code': '',
            'platform': '',
            'board_page_url': '',
            'own_site_doc_links': '',
            'expected_meetings_per_year': '',
            'docs_count': '',
            'last_checked': '',
            'last_success': '',
            'manual_notes': notes.values
        })

        all_bodies.append(bodies)

    # Combine all sheets
    combined = pd.concat(all_bodies, ignore_index=True)

    # Check for duplicates
    dupes = combined['body_id'].duplicated().sum()
    if dupes > 0:
        print(f"\nWARNING: {dupes} duplicate body_ids found")
        combined = combined.drop_duplicates(subset=['body_id'], keep='first')
        print(f"After dedup: {len(combined)} bodies")

    # Count totals
    total_with_website = (combined['website'] != '').sum()

    # Write output
    output_path = config.DATA_DIR / "census_national_bodies.csv"
    combined.to_csv(output_path, index=False)
    print(f"\nWrote {len(combined)} bodies to {output_path}")

    # Show segment distribution
    print("\nSegments (top 25):")
    segment_counts = combined['segment'].value_counts().head(25)
    for seg, count in segment_counts.items():
        print(f"  {seg}: {count}")

    print("\n=== Summary ===")
    print(f"Total active governments: {len(combined)}")
    print(f"With website: {total_with_website}")

    # Show sheet breakdown
    print("\nBy sheet:")
    for stat in sheet_stats:
        print(f"  {stat['sheet']}: {stat['count']} ({stat['with_website']} with website)")

    expected = 90837
    diff = len(combined) - expected
    if diff != 0:
        print(f"\nExpected {expected}, got {len(combined)} (diff: {diff:+d})")
        print("Note: DEP School Dist (1,313) are dependent on other governments and")
        print("may be counted within their parent in official totals.")

    return len(combined), int(total_with_website)


if __name__ == "__main__":
    count, with_web = main()
