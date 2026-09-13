"""
tests/test_data_loader.py

Unit tests for DataLoader, foreign key constraints, primary key uniqueness,
and null-amount auditing.
"""

import unittest
from pathlib import Path
import sys

# Add repo root to path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from code.data_loader import DataLoader


class TestDataLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = DataLoader()
        cls.dataset = cls.loader.load_and_validate()

    def test_validation_report_is_valid(self):
        """The data loader validation suite must pass with 0 errors."""
        self.assertTrue(self.dataset.validation_report.is_valid)

    def test_requests_shape_and_keys(self):
        """Ensure requests has exactly 250 evaluation rows and unique request_ids."""
        self.assertEqual(len(self.dataset.requests), 250)
        self.assertEqual(self.dataset.requests["request_id"].nunique(), 250)
        self.assertEqual(self.dataset.requests["request_id"].iloc[0], "request_26")
        self.assertEqual(self.dataset.requests["request_id"].iloc[-1], "request_275")

    def test_sample_requests_shape(self):
        """Ensure sample_requests has exactly 25 solved rows."""
        self.assertEqual(len(self.dataset.sample_requests), 25)
        self.assertEqual(self.dataset.sample_requests["request_id"].nunique(), 25)
        self.assertEqual(self.dataset.sample_requests["request_id"].iloc[0], "request_01")
        self.assertEqual(self.dataset.sample_requests["request_id"].iloc[-1], "request_25")

    def test_financial_profiles_coverage(self):
        """Ensure 275 user profiles covering all requests."""
        self.assertEqual(len(self.dataset.financial_profiles), 275)
        self.assertEqual(self.dataset.financial_profiles["user_id"].nunique(), 275)

    def test_null_amounts_in_financial_events(self):
        """Verify exactly 16 null amounts and all match images.csv related_event_id."""
        null_events = self.dataset.financial_events[self.dataset.financial_events["amount"].isna()]
        self.assertEqual(len(null_events), 16)
        image_events = set(self.dataset.images["related_event_id"])
        self.assertEqual(set(null_events["event_id"]), image_events)

    def test_foreign_keys_zero_orphans(self):
        """Verify all 11 FK checks report 0 orphans."""
        for fk_name, stats in self.dataset.validation_report.foreign_key_checks.items():
            self.assertEqual(stats["orphan_count"], 0, f"Orphans found in {fk_name}")

    def test_source_data_immutability(self):
        """Ensure amounts in financial_events are still null and not filled with 0."""
        null_events = self.dataset.financial_events[self.dataset.financial_events["amount"].isna()]
        self.assertEqual(len(null_events), 16)


if __name__ == "__main__":
    unittest.main()
