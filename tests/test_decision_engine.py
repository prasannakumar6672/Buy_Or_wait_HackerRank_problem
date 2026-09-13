"""
tests/test_decision_engine.py

Comprehensive unit tests for DecisionEngine and OutputValidator.
Verifies all 4 affordability tiers and strict contract enforcement.
"""

import unittest
from decimal import Decimal
from pathlib import Path
import tempfile
import csv

from code.data_fusion import DataFusionLoader
from code.decision_engine import DecisionEngine
from code.validator import OutputValidator

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDecisionEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fusion = DataFusionLoader(REPO_ROOT / "dataset")

    def test_tier1_affordable_now(self):
        """Request 01 is affordable now: pay full amount today."""
        ctx = self.fusion.get_request_context("request_01")
        engine = DecisionEngine(ctx)
        res = engine.evaluate()
        self.assertEqual(res.affordability_status, "affordable_now")
        self.assertEqual(res.recommended_payment_method, "full_payment")
        self.assertEqual(res.earliest_date_for_full_payment, ctx.request_date)
        self.assertEqual(res.spending_changes_needed, "none")
        self.assertTrue("Pay ZAR 25,256 today" in res.decision_explanation)

    def test_tier2_installments(self):
        """Request 02 is affordable with an installment plan."""
        ctx = self.fusion.get_request_context("request_02")
        engine = DecisionEngine(ctx)
        res = engine.evaluate()
        self.assertEqual(res.affordability_status, "affordable_with_plan")
        self.assertEqual(res.recommended_payment_method, "installments")
        self.assertTrue("|" in res.payment_plan)
        self.assertEqual(len(res.payment_plan.split("|")), 3)

    def test_tier2_partial_payment(self):
        """Request 19 utilizes a 2-payment partial payment schedule."""
        ctx = self.fusion.get_request_context("request_19")
        engine = DecisionEngine(ctx)
        res = engine.evaluate()
        self.assertEqual(res.affordability_status, "affordable_with_plan")
        self.assertEqual(res.recommended_payment_method, "partial_payment")
        payments = res.payment_plan.split("|")
        self.assertEqual(len(payments), 2)
        p1_amt = Decimal(payments[0].split(":")[1])
        p2_amt = Decimal(payments[1].split(":")[1])
        self.assertEqual(p1_amt + p2_amt, ctx.requested_amount)

    def test_tier3_affordable_later_wait(self):
        """Request 03 waits until salary arrival."""
        ctx = self.fusion.get_request_context("request_03")
        engine = DecisionEngine(ctx)
        res = engine.evaluate()
        self.assertEqual(res.affordability_status, "affordable_later")
        self.assertEqual(res.recommended_payment_method, "wait")
        self.assertIsNotNone(res.earliest_date_for_full_payment)
        self.assertEqual(res.spending_changes_needed, "none")

    def test_tier4_not_affordable(self):
        """Request 05 is not affordable within 90 days."""
        ctx = self.fusion.get_request_context("request_05")
        engine = DecisionEngine(ctx)
        res = engine.evaluate()
        self.assertEqual(res.affordability_status, "not_affordable")
        self.assertEqual(res.recommended_payment_method, "not_recommended")
        self.assertEqual(res.payment_plan, "none")
        self.assertIsNone(res.earliest_date_for_full_payment)

    def test_output_validator_success(self):
        """Verifies that root output.csv passes strict validation."""
        output_path = REPO_ROOT / "output.csv"
        if output_path.exists():
            validator = OutputValidator(REPO_ROOT / "dataset", output_path)
            is_valid, errors = validator.validate()
            self.assertTrue(is_valid, f"Validation errors: {errors}")


if __name__ == "__main__":
    unittest.main()
