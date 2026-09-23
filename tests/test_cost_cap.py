#!/usr/bin/env python3
"""Unit tests for LLM cost cap logic."""

import sys
import unittest
from datetime import date, datetime
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import config
from meetings.classify import check_daily_cap, get_today_llm_cost


class TestCostCap(unittest.TestCase):
    """Tests for the LLM daily cost cap feature."""

    @patch('meetings.classify.db')
    def test_get_today_llm_cost_no_runs(self, mock_db):
        """No LLM runs today returns zero cost."""
        mock_db.fetch_one.return_value = None

        cost = get_today_llm_cost()

        self.assertEqual(cost, 0.0)
        mock_db.fetch_one.assert_called_once()

    @patch('meetings.classify.db')
    def test_get_today_llm_cost_with_runs(self, mock_db):
        """LLM runs today are summed correctly."""
        # 100k input + 10k output with Haiku pricing ($1/M in, $5/M out)
        mock_db.fetch_one.return_value = {
            'input_tokens': 100000,
            'output_tokens': 10000,
            'model': 'claude-haiku-4-5-20251001'
        }

        cost = get_today_llm_cost()

        # 100k * $1/M = $0.10 input
        # 10k * $5/M = $0.05 output
        # Total = $0.15
        self.assertAlmostEqual(cost, 0.15, places=4)

    @patch('meetings.classify.get_today_llm_cost')
    def test_check_daily_cap_not_reached(self, mock_cost):
        """Cap not reached returns remaining budget."""
        mock_cost.return_value = 2.50
        cost_cap = 5.00

        cap_reached, current_cost, remaining = check_daily_cap(cost_cap)

        self.assertFalse(cap_reached)
        self.assertEqual(current_cost, 2.50)
        self.assertEqual(remaining, 2.50)

    @patch('meetings.classify.get_today_llm_cost')
    def test_check_daily_cap_exactly_reached(self, mock_cost):
        """Cap exactly reached is treated as reached."""
        mock_cost.return_value = 5.00
        cost_cap = 5.00

        cap_reached, current_cost, remaining = check_daily_cap(cost_cap)

        self.assertTrue(cap_reached)
        self.assertEqual(current_cost, 5.00)
        self.assertEqual(remaining, 0.0)

    @patch('meetings.classify.get_today_llm_cost')
    def test_check_daily_cap_exceeded(self, mock_cost):
        """Cap exceeded returns zero remaining."""
        mock_cost.return_value = 7.50
        cost_cap = 5.00

        cap_reached, current_cost, remaining = check_daily_cap(cost_cap)

        self.assertTrue(cap_reached)
        self.assertEqual(current_cost, 7.50)
        self.assertEqual(remaining, 0.0)

    def test_default_cap_from_config(self):
        """Default LLM_DAILY_CAP is $5."""
        # This tests the config module default
        self.assertEqual(config.LLM_DAILY_CAP, 5.0)


if __name__ == '__main__':
    unittest.main()
