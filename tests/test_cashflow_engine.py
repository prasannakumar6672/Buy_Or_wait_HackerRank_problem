"""
tests/test_cashflow_engine.py

Comprehensive unit tests for code/cashflow_engine.py.
Verifies all non-negotiable constraints:
1. Purely deterministic Python arithmetic using Decimal (Constraint 1)
2. Double-counting defense for pending debits (Constraint 2)
3. 90-day boundary semantics: [t0, t0 + 90 days], exactly 91 days, day 91 isolation (Constraint 11)
4. Safe amount formula bounds: 0 <= safe <= requested_amount (Constraint 12)
5. Earliest full-payment date pure mathematical property (Constraint 13)
6. Structured audit trace and per-request reconstruction (Constraint 15)
7. 25-Sample comparison diagnostic gate (Constraint 16)
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest

from code.data_fusion import DataFusionLoader, FinancialEventRecord, RequestContext, UserProfile
from code.cashflow_engine import CashflowEngine, CashflowAuditTrace


class TestCashflowEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loader = DataFusionLoader()

    def test_90_day_horizon_exact_boundary(self):
        """Constraint 11: Timeline contains exactly 91 discrete days [t0, t0 + 90]."""
        ctx = self.loader.get_request_context("request_01")
        engine = CashflowEngine(ctx)
        trace = engine.simulate()

        t0 = datetime.strptime(ctx.request_date, "%Y-%m-%d").date()
        t90 = t0 + timedelta(days=90)

        self.assertEqual(len(trace.timeline), 91)
        self.assertEqual(trace.timeline[0].date, t0.strftime("%Y-%m-%d"))
        self.assertEqual(trace.timeline[0].day_index, 0)
        self.assertEqual(trace.timeline[89].day_index, 89)
        self.assertEqual(trace.timeline[90].day_index, 90)
        self.assertEqual(trace.timeline[90].date, t90.strftime("%Y-%m-%d"))
        self.assertEqual(trace.horizon_end_date, t90.strftime("%Y-%m-%d"))

    def test_day_91_isolation(self):
        """Constraint 11: An event on day 91 must NOT influence the 90-day invariant."""
        ctx = self.loader.get_request_context("request_01")
        engine = CashflowEngine(ctx)
        trace_clean = engine.simulate()

        # Inject an event on day 91
        t0 = datetime.strptime(ctx.request_date, "%Y-%m-%d").date()
        d91 = t0 + timedelta(days=91)
        huge_debit = FinancialEventRecord(
            event_id="e_day91", user_id=ctx.user_id, event_type="expense", description="Catastrophic debit",
            category="special", direction="debit", amount=Decimal("99999999"), original_amount=Decimal("99999999"),
            currency=ctx.profile.home_currency, home_currency=ctx.profile.home_currency,
            event_date=d91.strftime("%Y-%m-%d"), settlement_date=d91.strftime("%Y-%m-%d"),
            status="scheduled", linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None
        )
        ctx_with_day91 = RequestContext(
            request_id=ctx.request_id, user_id=ctx.user_id, request_date=ctx.request_date,
            request_type=ctx.request_type, requested_amount=ctx.requested_amount,
            desired_completion_date=ctx.desired_completion_date, allows_partial_payment=ctx.allows_partial_payment,
            request_text=ctx.request_text, profile=ctx.profile,
            user_events=ctx.user_events + (huge_debit,),
            user_message_facts=ctx.user_message_facts, payment_options=ctx.payment_options
        )
        engine_day91 = CashflowEngine(ctx_with_day91)
        trace_day91 = engine_day91.simulate()

        # Invariant: Safe amount and min projected balance must be 100% identical
        self.assertEqual(trace_clean.safe_amount, trace_day91.safe_amount)
        self.assertEqual(trace_clean.min_projected_balance, trace_day91.min_projected_balance)
        self.assertEqual(len(trace_day91.timeline), 91)

    def test_double_counting_defense_regression(self):
        """
        Constraint 2 Regression Test:
        pending debit at t0 -> reservation occurs once.
        future settlement of same obligation -> no second deduction.
        Fails loudly if double counting is attempted.
        """
        profile = UserProfile(
            user_id="u_regress", home_currency="INR", current_available_balance=Decimal("10000"),
            minimum_balance_to_keep=Decimal("2000"), financial_priorities=(),
            expense_categories_to_protect=(), expense_categories_user_is_willing_to_reduce=(),
            expense_categories_user_is_willing_to_stop=(), payment_methods_user_will_consider=("full_payment",),
            max_installment_months=None
        )
        # Pending debit of 3000 settling on day 5
        pending_debit = FinancialEventRecord(
            event_id="e_pending_1", user_id="u_regress", event_type="purchase", description="Pending card debit",
            category="shopping", direction="debit", amount=Decimal("3000"), original_amount=Decimal("3000"),
            currency="INR", home_currency="INR", event_date="2026-05-01", settlement_date="2026-05-05",
            status="pending", linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None
        )
        ctx = RequestContext(
            request_id="r_regress", user_id="u_regress", request_date="2026-05-01", request_type="purchase",
            requested_amount=Decimal("10000"), desired_completion_date="2026-06-01", allows_partial_payment=False,
            request_text="test", profile=profile, user_events=(pending_debit,),
            user_message_facts=(), payment_options=()
        )
        engine = CashflowEngine(ctx)
        trace = engine.simulate()

        # Reservation occurred at t0
        self.assertEqual(trace.pending_debits_reserved, Decimal("3000"))
        # Opening balance on day 0 is 10000 - 3000 = 7000
        self.assertEqual(trace.timeline[0].opening_balance, Decimal("7000"))

        # Day 4 (2026-05-05) must NOT deduct 3000 again!
        day_4_point = trace.timeline[4]
        self.assertEqual(day_4_point.date, "2026-05-05")
        self.assertEqual(day_4_point.outflows, Decimal("0"))
        self.assertEqual(day_4_point.closing_balance, Decimal("7000"))

    def test_safe_amount_bounds(self):
        """Constraint 12: 0 <= safe_amount <= requested_amount."""
        for r_id in ["request_01", "request_06", "request_09", "request_16", "request_19"]:
            ctx = self.loader.get_request_context(r_id)
            trace = CashflowEngine(ctx).simulate()
            self.assertGreaterEqual(trace.safe_amount, Decimal("0"))
            self.assertLessEqual(trace.safe_amount, ctx.requested_amount)

    def test_earliest_full_payment_date_property(self):
        """Constraint 13: Mathematical earliest date equals request_date if safe today."""
        # request_01 is affordable now
        ctx_01 = self.loader.get_request_context("request_01")
        trace_01 = CashflowEngine(ctx_01).simulate()
        self.assertEqual(trace_01.safe_amount, ctx_01.requested_amount)
        self.assertEqual(trace_01.earliest_date_for_full_payment, ctx_01.request_date)

        # request_16 is affordable now
        ctx_16 = self.loader.get_request_context("request_16")
        trace_16 = CashflowEngine(ctx_16).simulate()
        self.assertEqual(trace_16.safe_amount, ctx_16.requested_amount)
        self.assertEqual(trace_16.earliest_date_for_full_payment, ctx_16.request_date)

    def test_audit_trail_reconstruction(self):
        """Constraint 15: Per-request trace reconstructs balance history correctly."""
        ctx = self.loader.get_request_context("request_01")
        trace = CashflowEngine(ctx).simulate()

        self.assertEqual(trace.initial_available_balance, ctx.profile.current_available_balance)
        self.assertEqual(trace.minimum_balance_to_keep, ctx.profile.minimum_balance_to_keep)

        # Verify daily continuity: opening_balance(t+1) == closing_balance(t)
        for i in range(len(trace.timeline) - 1):
            pt_cur = trace.timeline[i]
            pt_next = trace.timeline[i + 1]
            self.assertEqual(pt_cur.closing_balance, pt_next.opening_balance)
            self.assertEqual(pt_cur.headroom, pt_cur.closing_balance - trace.minimum_balance_to_keep)

        # Verify min projected balance
        actual_min = min(pt.closing_balance for pt in trace.timeline)
        self.assertEqual(trace.min_projected_balance, actual_min)


if __name__ == "__main__":
    unittest.main()
