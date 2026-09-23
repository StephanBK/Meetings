#!/usr/bin/env python3
"""Load 2022 Census of Governments units as level 0 bodies."""

import sys
import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import config


def main():
    print("=== Loading 2022 Census of Governments ===\n")

    # Load the Excel file
    xlsx_path = "/tmp/Govt_Units_2022_Final.xlsx"
    print(f"Loading {xlsx_path}...")
    df = pd.read_excel(xlsx_path)

    print(f"Total records: {len(df)}")

    # Filter to active governments only
    active = df[df["IS_ACTIVE"] == "Y"].copy()
    print(f"Active governments: {len(active)}")

    # Count with websites
    has_website = active["WEB_ADDRESS"].notna() & (active["WEB_ADDRESS"] != "")
    print(f"With website: {has_website.sum()}")
    print(f"Without website: {(~has_website).sum()}")

    # Create segment by combining state and unit type
    def make_segment(row):
        state = row["STATE"]
        unit_type = row["UNIT_TYPE"]
        # Clean up unit type (e.g., "1 - COUNTY" -> "County")
        if " - " in str(unit_type):
            unit_type = unit_type.split(" - ")[1].title()
        return f"{state} {unit_type}"

    active["segment"] = active.apply(make_segment, axis=1)

    # Show segment distribution
    print("\nSegments (top 20):")
    segment_counts = active["segment"].value_counts().head(20)
    for seg, count in segment_counts.items():
        print(f"  {seg}: {count}")

    # Prepare for bodies.csv format
    output_path = config.DATA_DIR / "census_national_bodies.csv"

    # Create output dataframe
    bodies = pd.DataFrame({
        "body_id": "COG-" + active["CENSUS_ID_PID6"].astype(str),
        "name": active["UNIT_NAME"],
        "segment": active["segment"],
        "county": active["COUNTY_AREA_NAME"],
        "schools": "",
        "enrollment": "",
        "website": active["WEB_ADDRESS"].fillna(""),
        "coverage_level": 0,
        "blocker_code": "",
        "platform": "",
        "board_page_url": "",
        "own_site_doc_links": "",
        "expected_meetings_per_year": "",
        "docs_count": "",
        "last_checked": "",
        "last_success": "",
        "manual_notes": f"Census 2022 {active['UNIT_TYPE']} population {active['POPULATION'].fillna('')}"
    })

    # Reset manual_notes properly
    bodies["manual_notes"] = active.apply(
        lambda r: f"Census 2022 {r['UNIT_TYPE']}, pop {r['POPULATION']}" if pd.notna(r['POPULATION']) else f"Census 2022 {r['UNIT_TYPE']}",
        axis=1
    ).values

    # Write to separate file first (to not disturb main bodies.csv)
    bodies.to_csv(output_path, index=False)
    print(f"\nWrote {len(bodies)} bodies to {output_path}")

    print("\n=== Summary ===")
    print(f"Total active governments: {len(active)}")
    print(f"With website: {has_website.sum()}")
    print(f"Unit types: {active['UNIT_TYPE'].nunique()}")

    return len(active), int(has_website.sum())


if __name__ == "__main__":
    count, with_web = main()
