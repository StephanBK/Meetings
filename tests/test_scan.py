"""Unit tests for the keyword scanning logic."""

import pytest
from meetings.scan import make_keyword_regex, scan_text, split_passages


class TestKeywordRegex:
    """Tests for keyword regex generation."""

    def test_basic_match(self):
        """Keyword matches exact word."""
        rx = make_keyword_regex("roof")
        assert rx.search("the roof is leaking")
        assert rx.search("Roof damage reported")  # case insensitive

    def test_no_partial_match(self):
        """Keyword does not match as substring of another word."""
        rx = make_keyword_regex("roof")
        assert not rx.search("rooftop unit installed")  # "roof" is part of "rooftop"
        assert not rx.search("bulletproof windows")  # "roof" is part of "bulletproof"

    def test_plural_s_suffix(self):
        """Keyword matches plural with -s ending."""
        rx = make_keyword_regex("window")
        assert rx.search("new windows installed")
        assert rx.search("the window replacement")

    def test_plural_es_suffix(self):
        """Keyword matches plural with -es ending."""
        rx = make_keyword_regex("bus")
        # Note: "buses" ends with -es
        rx_switch = make_keyword_regex("switch")
        assert rx_switch.search("electrical switches")

    def test_ing_suffix(self):
        """Keyword matches -ing ending."""
        rx = make_keyword_regex("roof")
        assert rx.search("roofing project approved")
        rx_heat = make_keyword_regex("heat")
        assert rx_heat.search("heating system replacement")

    def test_ed_suffix(self):
        """Keyword matches -ed ending."""
        rx = make_keyword_regex("leak")
        assert rx.search("the pipe leaked last winter")
        rx_fail = make_keyword_regex("fail")
        assert rx_fail.search("the unit failed inspection")

    def test_phrase_with_hyphen_or_space(self):
        """Keyword phrase matches with space or hyphen."""
        rx = make_keyword_regex("air handler")
        assert rx.search("the air handler was replaced")
        assert rx.search("air-handler replacement")

    def test_case_insensitive(self):
        """Matching is case insensitive."""
        rx = make_keyword_regex("HVAC")
        assert rx.search("hvac system")
        assert rx.search("HVAC upgrade")
        assert rx.search("Hvac maintenance")


class TestNegativePhrases:
    """Tests for negative phrase removal."""

    def test_window_of_opportunity_not_match(self):
        """'window of opportunity' should not trigger window trade."""
        hits = scan_text("This is a window of opportunity for the district.")
        # Should have no hits because "window" is in negative phrase
        assert len(hits) == 0

    def test_microsoft_windows_not_match(self):
        """'Microsoft Windows' should not trigger window trade."""
        hits = scan_text("All computers run Microsoft Windows 11 operating system.")
        assert len(hits) == 0

    def test_performance_bond_not_match(self):
        """'performance bond' should not trigger bond funding stage."""
        hits = scan_text("The contractor must provide a performance bond.")
        # Should have no hits because "bond" is in negative phrase "performance bond"
        assert len(hits) == 0

    def test_real_window_still_matches(self):
        """Actual window replacement should still match."""
        hits = scan_text("The building needs new window replacement throughout.")
        assert len(hits) == 1
        assert "envelope_windows" in hits[0]["trades"]


class TestSkipPatterns:
    """Tests for skip patterns (personnel lines, etc.)."""

    def test_salary_step_line_skipped(self):
        """Personnel line with Salary/Step is skipped."""
        # This passage would otherwise match "heating" but should be skipped
        text = """
        Appointment of John Doe, Heating Technician
        Salary/Step: Step 4, $65,000
        Effective Date: September 1, 2026
        """
        hits = scan_text(text)
        assert len(hits) == 0

    def test_position_line_skipped(self):
        """Personnel line with Position: is skipped."""
        text = """
        John Smith appointed.
        Position: Maintenance Supervisor, Buildings and Grounds
        """
        hits = scan_text(text)
        assert len(hits) == 0

    def test_leave_of_absence_skipped(self):
        """Personnel leave of absence is skipped."""
        text = "Jane Doe, Roof Inspector, granted leave of absence effective October 1."
        hits = scan_text(text)
        assert len(hits) == 0

    def test_replacing_retired_skipped(self):
        """Personnel replacement line is skipped."""
        text = "Appointed Bob Jones, HVAC Technician, replacing Thomas Smith who retired in June."
        hits = scan_text(text)
        assert len(hits) == 0

    def test_weed_control_skipped(self):
        """Weed control and pesticide lines are skipped."""
        text = "Approved weed control application for athletic fields."
        hits = scan_text(text)
        assert len(hits) == 0

    def test_real_construction_not_skipped(self):
        """Actual construction passages are not skipped."""
        text = "The HVAC system at the high school requires emergency replacement due to compressor failure."
        hits = scan_text(text)
        assert len(hits) == 1
        assert "hvac_mechanical" in hits[0]["trades"]


class TestSplitPassages:
    """Tests for passage splitting."""

    def test_split_by_blank_lines(self):
        """Text is split by blank lines."""
        text = """First passage with enough content to pass the threshold.

        Second passage also with enough content to be included.

        Third passage meeting the minimum length requirement."""
        passages = split_passages(text)
        assert len(passages) == 3

    def test_short_passages_excluded(self):
        """Passages with fewer than 25 characters are excluded."""
        text = """This is a real passage with enough content.

        Too short.

        Another passage with sufficient content to be included."""
        passages = split_passages(text)
        assert len(passages) == 2


class TestScanTextIntegration:
    """Integration tests for scan_text."""

    def test_trade_match_creates_hit(self):
        """A passage with a trade keyword creates a hit."""
        hits = scan_text("The district approved a roofing project for the elementary school.")
        assert len(hits) == 1
        assert "roofing" in hits[0]["trades"]

    def test_trigger_match_creates_hit(self):
        """A passage with a trigger keyword creates a hit even without trade."""
        hits = scan_text("The facilities committee met to discuss building improvements.")
        assert len(hits) == 1
        assert "facilities committee" in hits[0]["triggers"]

    def test_stage_captured_with_trade(self):
        """Stage keywords are captured when trade matches."""
        hits = scan_text("Emergency repair needed for the boiler system that failed inspection.")
        assert len(hits) == 1
        assert "hvac_mechanical" in hits[0]["trades"]
        assert "1_problem" in hits[0]["stages"]

    def test_multiple_trades_in_one_passage(self):
        """Multiple trades can match in the same passage."""
        hits = scan_text("The project includes HVAC replacement and new roofing at the middle school.")
        assert len(hits) == 1
        assert "hvac_mechanical" in hits[0]["trades"]
        assert "roofing" in hits[0]["trades"]
