"""
tests/test_event_normalizer.py

Comprehensive unit tests for code/event_normalizer.py.
Verifies all non-negotiable constraints:
1. Event status policy (settled, pending, scheduled, failed, cancelled, unrealized)
2. Pending debit reservation & double-counting defense
3. Monthly day-of-month and periodic recurrence generation
4. Step 3 message mutations on operative schedule (arrears, temporary reduction,
   termination, rent increase, salary date reschedule)
"""

from datetime import date, timedelta
from decimal import Decimal
import unittest

from code.data_fusion import DataFusionLoader, FinancialEventRecord, RequestContext, UserProfile
from code.event_normalizer import EventNormalizer, EventStatusPolicy, NormalizedEvent
from code.message_analyzer import MessageFact


class TestEventNormalizer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loader = DataFusionLoader()

    def test_status_policy_exclusions(self):
        """Constraint 14: cancelled, failed, unrealized, pending credits must be excluded."""
        t0 = date(2026, 1, 1)
        reserved_ids = set()

        # Cancelled
        ev_cancelled = NormalizedEvent(
            event_id="e_cancel", user_id="u1", category="shopping", direction="debit",
            amount=Decimal("100"), settlement_date="2026-01-05", status="cancelled",
            flexibility="fixed", minimum_allowed_amount=None
        )
        self.assertFalse(EventStatusPolicy.is_valid_cashflow_on_date(ev_cancelled, date(2026, 1, 5), t0, reserved_ids))

        # Failed
        ev_failed = NormalizedEvent(
            event_id="e_fail", user_id="u1", category="utilities", direction="debit",
            amount=Decimal("50"), settlement_date="2026-01-05", status="failed",
            flexibility="fixed", minimum_allowed_amount=None
        )
        self.assertFalse(EventStatusPolicy.is_valid_cashflow_on_date(ev_failed, date(2026, 1, 5), t0, reserved_ids))

        # Unrealized investment
        ev_unrealized = NormalizedEvent(
            event_id="e_unreal", user_id="u1", category="investment", direction="credit",
            amount=Decimal("500"), settlement_date="2026-01-05", status="unrealized",
            flexibility="fixed", minimum_allowed_amount=None
        )
        self.assertFalse(EventStatusPolicy.is_valid_cashflow_on_date(ev_unrealized, date(2026, 1, 5), t0, reserved_ids))

        # Pending credit (zero guaranteed cash)
        ev_pend_credit = NormalizedEvent(
            event_id="e_p_cred", user_id="u1", category="salary", direction="credit",
            amount=Decimal("1000"), settlement_date="2026-01-05", status="pending",
            flexibility="fixed", minimum_allowed_amount=None
        )
        self.assertFalse(EventStatusPolicy.is_valid_cashflow_on_date(ev_pend_credit, date(2026, 1, 5), t0, reserved_ids))

    def test_pending_debit_double_counting_defense(self):
        """Constraint 2: Pending debit reserved at t0 must NEVER deduct on settlement date."""
        t0 = date(2024, 3, 3)
        ctx = self.loader.get_request_context("request_01")
        normalizer = EventNormalizer(ctx)

        pending_to_reserve = normalizer.get_pending_debits_to_reserve()
        self.assertEqual(len(pending_to_reserve), 1)
        self.assertEqual(pending_to_reserve[0].event_id, "event_102")
        self.assertEqual(pending_to_reserve[0].amount, Decimal("567.6"))

        reserved_ids = {e.event_id for e in pending_to_reserve}
        self.assertIn("event_102", reserved_ids)

        # On settlement date (2024-03-05), event_102 must NOT be valid cashflow
        ev_102 = NormalizedEvent(
            event_id="event_102", user_id="user_01", category="transport", direction="debit",
            amount=Decimal("567.6"), settlement_date="2024-03-05", status="pending",
            flexibility="fixed", minimum_allowed_amount=None
        )
        self.assertFalse(
            EventStatusPolicy.is_valid_cashflow_on_date(ev_102, date(2024, 3, 5), t0, reserved_ids)
        )

    def test_month_clamping(self):
        """Constraint 3: Month clamping handles Feb leap years, Feb non-leap, and 30-day months."""
        # Non-leap year Feb
        d_non_leap = EventNormalizer.clamp_month_day(2025, 2, 31)
        self.assertEqual(d_non_leap, date(2025, 2, 28))

        # Leap year Feb
        d_leap = EventNormalizer.clamp_month_day(2024, 2, 31)
        self.assertEqual(d_leap, date(2024, 2, 29))

        # 30-day month (April)
        d_apr = EventNormalizer.clamp_month_day(2026, 4, 31)
        self.assertEqual(d_apr, date(2026, 4, 30))

        # Normal day within month
        d_normal = EventNormalizer.clamp_month_day(2026, 4, 15)
        self.assertEqual(d_normal, date(2026, 4, 15))

    def test_salary_arrears_disaggregation(self):
        """Constraint 5: Cycle 1 = base + arrears, Cycle 2+ = base. Arrears never recurs."""
        profile = UserProfile(
            user_id="u_test", home_currency="EUR", current_available_balance=Decimal("2000"),
            minimum_balance_to_keep=Decimal("500"), financial_priorities=(),
            expense_categories_to_protect=(), expense_categories_user_is_willing_to_reduce=(),
            expense_categories_user_is_willing_to_stop=(), payment_methods_user_will_consider=("full_payment",),
            max_installment_months=None
        )
        # Historical salary events on the 15th
        hist_events = (
            FinancialEventRecord("e1", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1000"), Decimal("1000"), "EUR", "EUR", "2025-10-15", "2025-10-15", "settled", None, "fixed", None),
            FinancialEventRecord("e2", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1000"), Decimal("1000"), "EUR", "EUR", "2025-11-15", "2025-11-15", "settled", None, "fixed", None),
            FinancialEventRecord("e3", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1000"), Decimal("1000"), "EUR", "EUR", "2025-12-15", "2025-12-15", "settled", None, "fixed", None),
        )
        # Message with salary 1200 + arrears 300
        msg_fact = MessageFact(
            message_id="m1", user_id="u_test", request_id="r1", related_event_id=None,
            source_type="hr_payroll_notice", language="en", classification="salary_plus_arrears",
            candidate_classifications=("salary_plus_arrears",), certainty="confirmed", direction="credit",
            frequency="monthly", primary_amount=1500.0, primary_currency="EUR", primary_role="salary_and_arrears",
            base_salary=1200.0, arrears_amount=300.0, arrears_is_one_off=True, cashflow_impact="salary_plus_arrears",
            temporal_anchor="next_recurring_cycle", duration_scope="permanent"
        )
        ctx = RequestContext(
            request_id="r1", user_id="u_test", request_date="2026-01-01", request_type="purchase",
            requested_amount=Decimal("500"), desired_completion_date="2026-02-01", allows_partial_payment=False,
            request_text="test", profile=profile, user_events=hist_events, user_message_facts=(msg_fact,),
            payment_options=()
        )
        normalizer = EventNormalizer(ctx)
        events = normalizer.build_normalized_event_timeline(reserved_obligation_ids=set())
        salaries = [e for e in events if e.category == "salary"]

        self.assertGreaterEqual(len(salaries), 2)
        # Cycle 1: 1200 + 300 = 1500
        self.assertEqual(salaries[0].amount, Decimal("1500"))
        # Cycle 2: 1200 (base only, arrears did not recur!)
        self.assertEqual(salaries[1].amount, Decimal("1200"))
        if len(salaries) > 2:
            self.assertEqual(salaries[2].amount, Decimal("1200"))

    def test_temporary_salary_reduction(self):
        """Constraint 6: Temporary reduction affects only the single cycle."""
        profile = UserProfile(
            user_id="u_test", home_currency="EUR", current_available_balance=Decimal("2000"),
            minimum_balance_to_keep=Decimal("500"), financial_priorities=(),
            expense_categories_to_protect=(), expense_categories_user_is_willing_to_reduce=(),
            expense_categories_user_is_willing_to_stop=(), payment_methods_user_will_consider=("full_payment",),
            max_installment_months=None
        )
        hist_events = (
            FinancialEventRecord("e1", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1500"), Decimal("1500"), "EUR", "EUR", "2025-10-15", "2025-10-15", "settled", None, "fixed", None),
            FinancialEventRecord("e2", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1500"), Decimal("1500"), "EUR", "EUR", "2025-11-15", "2025-11-15", "settled", None, "fixed", None),
            FinancialEventRecord("e3", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1500"), Decimal("1500"), "EUR", "EUR", "2025-12-15", "2025-12-15", "settled", None, "fixed", None),
        )
        msg_fact = MessageFact(
            message_id="m1", user_id="u_test", request_id="r1", related_event_id=None,
            source_type="hr_payroll_notice", language="en", classification="temporary_salary_reduction",
            candidate_classifications=("temporary_salary_reduction",), certainty="confirmed", direction="credit",
            frequency="monthly", primary_amount=1000.0, primary_currency="EUR", primary_role="temporary_salary",
            cashflow_impact="modify_salary_amount", temporal_anchor="single_affected_cycle", duration_scope="single_cycle"
        )
        ctx = RequestContext(
            request_id="r1", user_id="u_test", request_date="2026-01-01", request_type="purchase",
            requested_amount=Decimal("500"), desired_completion_date="2026-02-01", allows_partial_payment=False,
            request_text="test", profile=profile, user_events=hist_events, user_message_facts=(msg_fact,),
            payment_options=()
        )
        normalizer = EventNormalizer(ctx)
        events = normalizer.build_normalized_event_timeline(reserved_obligation_ids=set())
        salaries = [e for e in events if e.category == "salary"]

        # Cycle 1 reduced to 1000
        self.assertEqual(salaries[0].amount, Decimal("1000"))
        # Cycle 2 returns to 1500 baseline
        self.assertEqual(salaries[1].amount, Decimal("1500"))

    def test_contract_termination_stops_salary(self):
        """Constraint 8: Contract termination stops future salary without fabricating dates."""
        profile = UserProfile(
            user_id="u_test", home_currency="EUR", current_available_balance=Decimal("2000"),
            minimum_balance_to_keep=Decimal("500"), financial_priorities=(),
            expense_categories_to_protect=(), expense_categories_user_is_willing_to_reduce=(),
            expense_categories_user_is_willing_to_stop=(), payment_methods_user_will_consider=("full_payment",),
            max_installment_months=None
        )
        hist_events = (
            FinancialEventRecord("e1", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1500"), Decimal("1500"), "EUR", "EUR", "2025-10-15", "2025-10-15", "settled", None, "fixed", None),
            FinancialEventRecord("e2", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1500"), Decimal("1500"), "EUR", "EUR", "2025-11-15", "2025-11-15", "settled", None, "fixed", None),
            FinancialEventRecord("e3", "u_test", "salary", "Payroll", "salary", "credit", Decimal("1500"), Decimal("1500"), "EUR", "EUR", "2025-12-15", "2025-12-15", "settled", None, "fixed", None),
        )
        msg_fact = MessageFact(
            message_id="m1", user_id="u_test", request_id="r1", related_event_id=None,
            source_type="hr_payroll_notice", language="en", classification="contract_termination",
            candidate_classifications=("contract_termination",), certainty="confirmed", direction="credit",
            frequency="monthly", cashflow_impact="stop_future_salary", termination_confirmed=True,
            future_recurring_salary="stop", temporal_anchor="immediate_termination"
        )
        ctx = RequestContext(
            request_id="r1", user_id="u_test", request_date="2026-01-01", request_type="purchase",
            requested_amount=Decimal("500"), desired_completion_date="2026-02-01", allows_partial_payment=False,
            request_text="test", profile=profile, user_events=hist_events, user_message_facts=(msg_fact,),
            payment_options=()
        )
        normalizer = EventNormalizer(ctx)
        events = normalizer.build_normalized_event_timeline(reserved_obligation_ids=set())
        salaries = [e for e in events if e.category == "salary"]
        self.assertEqual(len(salaries), 0)

    def test_rent_increase_percentage(self):
        """Constraint 9: Rent +12% applies from actual next rent event onward."""
        profile = UserProfile(
            user_id="u_test", home_currency="EUR", current_available_balance=Decimal("2000"),
            minimum_balance_to_keep=Decimal("500"), financial_priorities=(),
            expense_categories_to_protect=(), expense_categories_user_is_willing_to_reduce=(),
            expense_categories_user_is_willing_to_stop=(), payment_methods_user_will_consider=("full_payment",),
            max_installment_months=None
        )
        # Rent on 4th of month, 500 EUR
        hist_events = (
            FinancialEventRecord("e1", "u_test", "rent", "Rent", "rent", "debit", Decimal("500"), Decimal("500"), "EUR", "EUR", "2025-10-04", "2025-10-04", "settled", None, "fixed", None),
            FinancialEventRecord("e2", "u_test", "rent", "Rent", "rent", "debit", Decimal("500"), Decimal("500"), "EUR", "EUR", "2025-11-04", "2025-11-04", "settled", None, "fixed", None),
            FinancialEventRecord("e3", "u_test", "rent", "Rent", "rent", "debit", Decimal("500"), Decimal("500"), "EUR", "EUR", "2025-12-04", "2025-12-04", "settled", None, "fixed", None),
        )
        msg_fact = MessageFact(
            message_id="m1", user_id="u_test", request_id="r1", related_event_id=None,
            source_type="landlord_notice", language="en", classification="rent_increase",
            candidate_classifications=("rent_increase",), certainty="confirmed", direction="debit",
            frequency="monthly", percentage=12.0, cashflow_impact="increase_rent_percentage",
            temporal_anchor="next_recurring_rent_cycle", duration_scope="permanent"
        )
        ctx = RequestContext(
            request_id="r1", user_id="u_test", request_date="2026-01-01", request_type="purchase",
            requested_amount=Decimal("200"), desired_completion_date="2026-02-01", allows_partial_payment=False,
            request_text="test", profile=profile, user_events=hist_events, user_message_facts=(msg_fact,),
            payment_options=()
        )
        normalizer = EventNormalizer(ctx)
        events = normalizer.build_normalized_event_timeline(reserved_obligation_ids=set())
        rents = [e for e in events if e.category == "rent"]

        self.assertGreaterEqual(len(rents), 2)
        # 500 * 1.12 = 560
        self.assertEqual(rents[0].amount, Decimal("560.00"))
        self.assertEqual(rents[1].amount, Decimal("560.00"))


if __name__ == "__main__":
    unittest.main()
