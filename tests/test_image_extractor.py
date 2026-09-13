"""
tests/test_image_extractor.py

Automated unit tests for Step 2: Image extraction and resolution layer.
Verifies all 10 invariants specified in Step 2F.
"""

import unittest
from pathlib import Path
import sys
import pandas as pd

# Add repo root to path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from code.data_loader import DataLoader
from code.image_extractor import (
    VERIFIED_IMAGE_RESOLUTIONS,
    EnrichedEventsManager,
    ImageResolutionRecord
)


class TestImageExtractor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = DataLoader()
        cls.dataset = cls.loader.load_and_validate()
        cls.raw_events = cls.dataset.financial_events
        cls.images = cls.dataset.images
        cls.media_dir = cls.loader.media_dir
        cls.enriched_events = EnrichedEventsManager.get_enriched_financial_events(
            cls.raw_events, cls.images
        )

    def test_1_exactly_16_missing_amounts_detected(self):
        """1. Exactly 16 missing amounts are detected in raw financial_events."""
        null_count = self.raw_events["amount"].isna().sum()
        self.assertEqual(null_count, 16)

    def test_2_exactly_16_corresponding_images_exist(self):
        """2. Exactly 16 corresponding physical images exist on disk."""
        self.assertEqual(len(self.images), 16)
        for img_id in self.images["image_id"]:
            img_file = self.media_dir / f"{img_id}.png"
            self.assertTrue(img_file.exists(), f"Missing physical image: {img_file}")

    def test_3_every_missing_event_maps_to_exactly_one_image(self):
        """3. Every missing event maps to exactly one image."""
        missing_event_ids = set(self.raw_events[self.raw_events["amount"].isna()]["event_id"])
        image_event_ids = set(self.images["related_event_id"])
        self.assertEqual(missing_event_ids, image_event_ids)
        self.assertEqual(len(missing_event_ids), 16)

    def test_4_every_image_maps_to_expected_event(self):
        """4. Every image in registry maps to the expected event in images.csv."""
        for _, row in self.images.iterrows():
            img_id = row["image_id"]
            expected_evt = row["related_event_id"]
            self.assertIn(img_id, VERIFIED_IMAGE_RESOLUTIONS)
            rec = VERIFIED_IMAGE_RESOLUTIONS[img_id]
            self.assertEqual(rec.event_id, expected_evt)

    def test_5_every_resolved_amount_is_numeric(self):
        """5. Every resolved amount is numeric (float or int)."""
        for rec in VERIFIED_IMAGE_RESOLUTIONS.values():
            self.assertIsInstance(rec.resolved_amount, (int, float))
            self.assertFalse(pd.isna(rec.resolved_amount))

    def test_6_every_resolved_amount_is_positive(self):
        """6. Every resolved amount is positive when representing positive monetary amount."""
        for rec in VERIFIED_IMAGE_RESOLUTIONS.values():
            self.assertGreater(rec.resolved_amount, 0.0)

    def test_7_currency_is_preserved(self):
        """7. Currency matches the event currency and is preserved."""
        for rec in VERIFIED_IMAGE_RESOLUTIONS.values():
            raw_row = self.raw_events[self.raw_events["event_id"] == rec.event_id].iloc[0]
            self.assertEqual(rec.currency, raw_row["currency"])

    def test_8_original_csv_remains_unchanged(self):
        """8. Original CSV remains unchanged on disk (still has 16 null amounts)."""
        disk_csv_path = self.loader.dataset_dir / "financial_events.csv"
        disk_df = pd.read_csv(disk_csv_path)
        self.assertEqual(disk_df["amount"].isna().sum(), 16)
        self.assertEqual(self.raw_events["amount"].isna().sum(), 16)

    def test_9_no_unrelated_event_amounts_are_modified(self):
        """9. No unrelated event amounts are modified in enriched DataFrame."""
        # Check all rows except the 16 resolved ones
        unrelated_mask = ~self.enriched_events["event_id"].isin(
            [r.event_id for r in VERIFIED_IMAGE_RESOLUTIONS.values()]
        )
        raw_unrelated = self.raw_events[unrelated_mask]["amount"]
        enriched_unrelated = self.enriched_events[unrelated_mask]["amount"]
        pd.testing.assert_series_equal(raw_unrelated, enriched_unrelated)

    def test_10_no_unresolved_amount_silently_converted_to_zero(self):
        """10. In enriched DataFrame, exactly 0 events have null amounts and none are converted to 0.0."""
        self.assertEqual(self.enriched_events["amount"].isna().sum(), 0)
        resolved_amounts = self.enriched_events[
            self.enriched_events["is_amount_resolved_from_image"]
        ]["amount"]
        self.assertEqual(len(resolved_amounts), 16)
        self.assertTrue((resolved_amounts > 0).all())


if __name__ == "__main__":
    unittest.main()
