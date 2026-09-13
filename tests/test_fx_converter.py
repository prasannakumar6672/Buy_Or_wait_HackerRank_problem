"""
tests/test_fx_converter.py

Unit test suite for the deterministic FX converter.
Verifies exact rate lookup, same-currency identity, Decimal precision,
auditable records, and hard failures on missing rates.
"""

from decimal import Decimal
from pathlib import Path
import unittest

from code.fx_converter import FXConverter, FXConversionRecord, MissingExchangeRateError


class TestFXConverter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.converter = FXConverter()

    def test_01_rates_loaded(self):
        """Invariant 1: All exchange rates from exchange_rates.csv are loaded."""
        self.assertGreater(self.converter.total_rates, 100)

    def test_02_same_currency_conversion(self):
        """Invariant 2: Converting same currency returns exact amount and rate 1.0."""
        currencies = ["INR", "USD", "EUR", "IDR", "ZAR"]
        for curr in currencies:
            res = self.converter.convert(
                amount=1000,
                from_currency=curr,
                to_currency=curr,
                rate_date="2024-01-15",
                event_id="evt_test",
            )
            self.assertEqual(res.rate, Decimal("1"))
            self.assertEqual(res.original_amount, Decimal("1000"))
            self.assertEqual(res.converted_amount, Decimal("1000"))
            self.assertEqual(res.home_currency, curr)
            self.assertEqual(res.original_currency, curr)

    def test_03_foreign_currency_conversion_accuracy(self):
        """Invariant 3: Foreign currency conversion matches exchange_rates.csv exactly."""
        # 2024-01-15: USD -> INR rate is 83.33
        res = self.converter.convert(
            amount=100,
            from_currency="USD",
            to_currency="INR",
            rate_date="2024-01-15",
            event_id="evt_100",
        )
        self.assertEqual(res.rate, Decimal("83.33"))
        self.assertEqual(res.converted_amount, Decimal("8333.00"))
        self.assertEqual(res.event_id, "evt_100")

        # 2023-10-15: EUR -> ZAR rate is 20
        res_zar = self.converter.convert(
            amount=50,
            from_currency="EUR",
            to_currency="ZAR",
            rate_date="2023-10-15",
        )
        self.assertEqual(res_zar.rate, Decimal("20"))
        self.assertEqual(res_zar.converted_amount, Decimal("1000"))

        # 2023-10-15: USD -> IDR rate is 15833.33
        res_idr = self.converter.convert(
            amount=10,
            from_currency="USD",
            to_currency="IDR",
            rate_date="2023-10-15",
        )
        self.assertEqual(res_idr.rate, Decimal("15833.33"))
        self.assertEqual(res_idr.converted_amount, Decimal("158333.30"))

    def test_04_missing_rate_hard_failure(self):
        """Invariant 4: Missing rate raises MissingExchangeRateError. Never approximates."""
        # Invalid date or unsupported pair
        with self.assertRaises(MissingExchangeRateError):
            self.converter.convert(
                amount=100,
                from_currency="JPY",
                to_currency="USD",
                rate_date="2024-01-15",
            )

        with self.assertRaises(MissingExchangeRateError):
            self.converter.convert(
                amount=100,
                from_currency="USD",
                to_currency="INR",
                rate_date="1999-01-01",
            )

    def test_05_decimal_precision_no_float_drift(self):
        """Invariant 5: Uses exact Decimal arithmetic without binary floating point drift."""
        res = self.converter.convert(
            amount="1234.5678",
            from_currency="USD",
            to_currency="INR",
            rate_date="2024-01-15",
        )
        expected = Decimal("1234.5678") * Decimal("83.33")
        self.assertEqual(res.converted_amount, expected)
        self.assertIsInstance(res.converted_amount, Decimal)

    def test_06_audit_record_serialization(self):
        """Invariant 6: FXConversionRecord produces a complete audit dict."""
        res = self.converter.convert(
            amount=250,
            from_currency="USD",
            to_currency="EUR",
            rate_date="2023-10-15",
            event_id="evt_audit",
        )
        d = res.to_dict()
        self.assertEqual(d["event_id"], "evt_audit")
        self.assertEqual(d["original_amount"], 250.0)
        self.assertEqual(d["rate"], 0.92)
        self.assertEqual(d["converted_amount"], 230.0)
        self.assertEqual(d["rate_date"], "2023-10-15")


if __name__ == "__main__":
    unittest.main()
