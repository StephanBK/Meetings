"""Unit tests for the LLM classification logic."""

import pytest
from meetings.classify import validate_evidence_quote


class TestValidateEvidenceQuote:
    """Tests for evidence quote validation."""

    def test_empty_quote(self):
        """Empty quote always validates."""
        assert validate_evidence_quote("", "any text here")
        assert validate_evidence_quote(None, "any text here")

    def test_exact_match(self):
        """Exact substring match validates."""
        text = "The board approved the roofing project for the high school."
        quote = "approved the roofing project"
        assert validate_evidence_quote(quote, text)

    def test_case_insensitive(self):
        """Matching is case insensitive."""
        text = "The Board APPROVED the Roofing Project"
        quote = "board approved the roofing project"
        assert validate_evidence_quote(quote, text)

    def test_whitespace_collapsed(self):
        """Extra whitespace is collapsed before matching."""
        text = "The   board   approved   the   project"
        quote = "board approved the project"
        assert validate_evidence_quote(quote, text)

    def test_newlines_collapsed(self):
        """Newlines are treated as whitespace."""
        text = "The board\napproved\nthe project"
        quote = "board approved the project"
        assert validate_evidence_quote(quote, text)

    def test_80_percent_match(self):
        """80% word match in order validates."""
        text = "The board hereby approved the emergency roofing repair project"
        # Quote has 5 words, need 4 (80%) to match in order
        quote = "board approved roofing repair project"  # 5 of 5 words match
        assert validate_evidence_quote(quote, text)

    def test_partial_match_above_threshold(self):
        """Match above 80% threshold validates."""
        text = "The board approved the new roofing project for schools"
        # Quote has 4 words, need 3.2 (80%), so 4 must match
        quote = "board approved roofing project"  # 4 of 4 words match
        assert validate_evidence_quote(quote, text)

    def test_partial_match_below_threshold(self):
        """Match below 80% threshold fails."""
        text = "The board discussed other matters"
        quote = "board approved roofing project"  # Only 1 of 4 words match (25%)
        assert not validate_evidence_quote(quote, text)

    def test_words_must_be_in_order(self):
        """Words must appear in the same order."""
        text = "The project was approved by the board"
        quote = "board approved project"  # Out of order
        assert not validate_evidence_quote(quote, text)

    def test_quote_not_in_text(self):
        """Quote not in text fails."""
        text = "The district considered budget matters"
        quote = "roofing project approved"
        assert not validate_evidence_quote(quote, text)
