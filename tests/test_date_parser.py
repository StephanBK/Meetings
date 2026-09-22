"""Unit tests for meeting date parsing."""

import pytest
from datetime import date

from meetings.adapters.pdf_watcher import (
    parse_meeting_date,
    normalize_dashes,
    extract_year_from_url,
)


class TestNormalizeDashes:
    """Tests for dash normalization."""

    def test_en_dash(self):
        """En-dash (U+2013) should become hyphen."""
        assert normalize_dashes("June 4, 2026 – Regular Meeting") == "June 4, 2026 - Regular Meeting"

    def test_em_dash(self):
        """Em-dash (U+2014) should become hyphen."""
        assert normalize_dashes("June 4, 2026 — Regular Meeting") == "June 4, 2026 - Regular Meeting"

    def test_regular_hyphen(self):
        """Regular hyphen should remain unchanged."""
        assert normalize_dashes("June 4, 2026 - Regular Meeting") == "June 4, 2026 - Regular Meeting"

    def test_multiple_dashes(self):
        """Multiple different dash types should all normalize."""
        assert normalize_dashes("A–B—C-D") == "A-B-C-D"


class TestExtractYearFromUrl:
    """Tests for year extraction from URLs."""

    def test_year_in_path(self):
        """Year in URL path."""
        assert extract_year_from_url("https://example.com/2026/minutes.pdf") == 2026

    def test_year_before_pdf(self):
        """Year before .pdf extension."""
        assert extract_year_from_url("https://example.com/minutes-2025.pdf") == 2025

    def test_year_with_underscore(self):
        """Year with underscore separator."""
        assert extract_year_from_url("https://example.com/files_2024/doc.pdf") == 2024

    def test_no_year(self):
        """URL without year."""
        assert extract_year_from_url("https://example.com/minutes.pdf") is None

    def test_19xx_year(self):
        """Historic year (19xx)."""
        assert extract_year_from_url("https://example.com/archive/1999/doc.pdf") == 1999


class TestParseMeetingDate:
    """Tests for meeting date parsing - exact strings from user requirements."""

    def test_full_date_with_en_dash(self):
        """June 4, 2026 – Regular Meeting"""
        result = parse_meeting_date("June 4, 2026 – Regular Meeting")
        assert result == date(2026, 6, 4)

    def test_month_year_only(self):
        """January 2009 School Board Minutes -> January 1, 2009"""
        result = parse_meeting_date("January 2009 School Board Minutes")
        assert result == date(2009, 1, 1)

    def test_day_month_no_year_with_url(self):
        """July 2 - Annual Organizational Meeting with year from URL."""
        result = parse_meeting_date(
            "July 2 - Annual Organizational Meeting",
            url="https://example.com/2025/meetings.pdf"
        )
        assert result == date(2025, 7, 2)

    def test_full_date_with_hyphen(self):
        """May 7, 2026 - Regular Meeting"""
        result = parse_meeting_date("May 7, 2026 - Regular Meeting")
        assert result == date(2026, 5, 7)

    def test_full_date_with_en_dash_committee(self):
        """September 8, 2026 – Building & Grounds Committee"""
        result = parse_meeting_date("September 8, 2026 – Building & Grounds Committee")
        assert result == date(2026, 9, 8)


class TestParseMeetingDateAdditional:
    """Additional test cases for edge cases."""

    def test_abbreviated_month_full_date(self):
        """Sept 3, 2026 - Meeting"""
        result = parse_meeting_date("Sept 3, 2026 - Meeting")
        assert result == date(2026, 9, 3)

    def test_abbreviated_month_year_only(self):
        """Feb 2010 Minutes"""
        result = parse_meeting_date("Feb 2010 Minutes")
        assert result == date(2010, 2, 1)

    def test_numeric_date_mdy(self):
        """12/15/2025"""
        result = parse_meeting_date("12/15/2025")
        assert result == date(2025, 12, 15)

    def test_numeric_date_short_year(self):
        """12-15-25"""
        result = parse_meeting_date("12-15-25")
        assert result == date(2025, 12, 15)

    def test_iso_date(self):
        """2026-03-15"""
        result = parse_meeting_date("2026-03-15")
        assert result == date(2026, 3, 15)

    def test_day_month_with_fallback_year(self):
        """March 5 - Budget Hearing with fallback year."""
        result = parse_meeting_date(
            "March 5 - Budget Hearing",
            url="",
            fallback_year=2026
        )
        assert result == date(2026, 3, 5)

    def test_day_month_no_year_no_url(self):
        """July 2 - Meeting without URL or fallback returns None."""
        result = parse_meeting_date("July 2 - Annual Organizational Meeting")
        assert result is None

    def test_abbreviated_month_day_with_url_year(self):
        """Sep 15 - Meeting with URL year."""
        result = parse_meeting_date(
            "Sep 15 - Special Meeting",
            url="https://example.com/board/2024/agenda.pdf"
        )
        assert result == date(2024, 9, 15)

    def test_no_date_in_text(self):
        """Text without any date."""
        result = parse_meeting_date("Board Meeting Agenda")
        assert result is None
