"""
code/decision_engine.py

Deterministic decision engine and payment plan optimizer for HackerRank Orchestrate.
Implements the authoritative financial decision hierarchy:
1. affordable_now
2. affordable_with_plan (installments, partial payment, spending changes)
3. affordable_later (wait)
4. not_affordable (not_recommended)

Strictly adheres to:
- 100% Deterministic Python & Decimal arithmetic (zero LLM arithmetic)
- User profile payment method preferences
- Provider payment options from request_payment_options.csv
- 90-day minimum balance invariant: balance(t) >= minimum_balance_to_keep for all t in [t0, t0+90]
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, List, Optional, Set, Tuple

from code.data_fusion import PaymentOption, RequestContext, UserProfile
from code.cashflow_engine import CashflowAuditTrace, CashflowEngine
from code.event_normalizer import NormalizedEvent, EventNormalizer, EventStatusPolicy


@dataclass(frozen=True)
class SpendingChange:
    """Type-safe specification of an allowable spending reduction or termination."""
    action: str                        # "stop" or "reduce_to"
    event_id: str
    category: str
    original_amount: Decimal
    new_amount: Decimal                # 0 for stop
    savings: Decimal                   # original_amount - new_amount
    description: str


@dataclass(frozen=True)
class DecisionResult:
    """Final decision record matching the output.csv schema."""
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str          # "affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"
    recommended_payment_method: str    # "full_payment", "partial_payment", "installments", "wait", "not_recommended"
    payment_plan: str                  # pipe-delimited YYYY-MM-DD:amount or "none"
    earliest_date_for_full_payment: Optional[str]  # YYYY-MM-DD or None (empty string in CSV)
    spending_changes_needed: str       # pipe-delimited stop/reduce_to or "none"
    decision_explanation: str
    chosen_payment_option_id: Optional[str] = None


class DecisionEngine:
    """
    Deterministic financial decision engine that evaluates affordability,
    optimizes payment plans, and selects compliant spending changes.
    """

    def __init__(self, ctx: RequestContext):
        self.ctx = ctx
        self.profile = ctx.profile
        self.requested_amount = ctx.requested_amount
        self.request_date = datetime.strptime(ctx.request_date, "%Y-%m-%d").date()
        self.desired_completion_date = datetime.strptime(ctx.desired_completion_date, "%Y-%m-%d").date()
        self.allows_partial = ctx.allows_partial_payment

    def evaluate(self, trace: Optional[CashflowAuditTrace] = None) -> DecisionResult:
        """
        Executes the decision logic across the 4-tier hierarchy.
        """
        if trace is None:
            engine = CashflowEngine(self.ctx)
            trace = engine.simulate()

        safe_amount = trace.safe_amount
        earliest_full_date = trace.earliest_date_for_full_payment
        considered_methods = set(self.profile.payment_methods_user_will_consider)

        # -------------------------------------------------------------
        # TIER 1: AFFORDABLE NOW (full payment today without plan)
        # -------------------------------------------------------------
        if safe_amount >= self.requested_amount:
            if not considered_methods or "full_payment" in considered_methods:
                plan_str = f"{self.ctx.request_date}:{self._format_amount(self.requested_amount)}"
                explanation = self._explain_affordable_now(self.requested_amount)
                return DecisionResult(
                    request_id=self.ctx.request_id,
                    amount_safe_to_pay=self.requested_amount,
                    affordability_status="affordable_now",
                    recommended_payment_method="full_payment",
                    payment_plan=plan_str,
                    earliest_date_for_full_payment=self.ctx.request_date,
                    spending_changes_needed="none",
                    decision_explanation=explanation,
                )

        # -------------------------------------------------------------
        # TIER 2/3A: Rank every no-change plan that can meet the deadline.
        # The specification ranks candidates across methods, rather than
        # privileging a method merely because it was evaluated first.
        # -------------------------------------------------------------
        candidates: List[Tuple[Tuple, DecisionResult]] = []

        partial_plan = self._evaluate_partial_payment(trace)
        if partial_plan is not None:
            candidates.append(((self.requested_amount, self.request_date, 2, "", "partial_payment"), partial_plan))

        best_installment = self._find_best_installment_plan(trace)
        if best_installment is not None:
            opt = next(
                option for option in self.ctx.payment_options
                if option.payment_option_id == best_installment.chosen_payment_option_id
            )
            installment_dates = self._generate_installment_dates(opt)
            candidates.append((
                (opt.total_payable_amount, installment_dates[0], opt.number_of_payments, opt.payment_option_id, "installments"),
                best_installment,
            ))

        wait_plan = self._build_wait_plan(trace, require_deadline=True)
        if wait_plan is not None:
            wait_date = datetime.strptime(wait_plan.earliest_date_for_full_payment, "%Y-%m-%d").date()
            candidates.append(((self.requested_amount, wait_date, 1, "", "wait"), wait_plan))

        if candidates:
            candidates.sort(key=lambda candidate: candidate[0])
            return candidates[0][1]

        # -------------------------------------------------------------
        # TIER 2C: PERMITTED SPENDING CHANGES (to beat deadline)
        # -------------------------------------------------------------
        spending_plan = self._evaluate_spending_changes(trace)
        if spending_plan is not None:
            return spending_plan

        # -------------------------------------------------------------
        # TIER 3B: AFFORDABLE LATER (Wait even if after deadline, within 90 days)
        # -------------------------------------------------------------
        wait_plan = self._build_wait_plan(trace, require_deadline=False)
        if wait_plan is not None:
            return wait_plan

        # -------------------------------------------------------------
        # TIER 4: NOT AFFORDABLE
        # -------------------------------------------------------------
        explanation = self._explain_not_affordable(safe_amount)
        return DecisionResult(
            request_id=self.ctx.request_id,
            amount_safe_to_pay=safe_amount,
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payment_plan="none",
            earliest_date_for_full_payment=None,
            spending_changes_needed="none",
            decision_explanation=explanation,
        )

    def _build_wait_plan(
        self, trace: CashflowAuditTrace, require_deadline: bool
    ) -> Optional[DecisionResult]:
        """Build a wait recommendation only when the user accepts full payment."""
        considered_methods = set(self.profile.payment_methods_user_will_consider)
        if considered_methods and "full_payment" not in considered_methods:
            return None

        earliest_full_date = trace.earliest_date_for_full_payment
        if earliest_full_date is None:
            return None

        earliest_dt = datetime.strptime(earliest_full_date, "%Y-%m-%d").date()
        if require_deadline and earliest_dt > self.desired_completion_date:
            return None

        return DecisionResult(
            request_id=self.ctx.request_id,
            amount_safe_to_pay=trace.safe_amount,
            affordability_status="affordable_later",
            recommended_payment_method="wait",
            payment_plan=f"{earliest_full_date}:{self._format_amount(self.requested_amount)}",
            earliest_date_for_full_payment=earliest_full_date,
            spending_changes_needed="none",
            decision_explanation=self._explain_affordable_later(self.requested_amount, earliest_dt),
        )

    # -----------------------------------------------------------------
    # PLAN EVALUATION HELPERS
    # -----------------------------------------------------------------

    def _find_best_installment_plan(self, trace: CashflowAuditTrace) -> Optional[DecisionResult]:
        """
        Evaluates provider installment options from request_payment_options.csv.
        Returns the optimal feasible plan that maintains min_keep across 90 days.
        """
        considered_methods = set(self.profile.payment_methods_user_will_consider)
        if considered_methods and "installments" not in considered_methods:
            return None

        feasible_plans = []

        for opt in self.ctx.payment_options:
            if opt.payment_method != "installments":
                continue

            # Check max_installment_months constraint
            if self.profile.max_installment_months is not None:
                if opt.number_of_payments > self.profile.max_installment_months:
                    continue

            # Build installment dates and amounts
            dates = self._generate_installment_dates(opt)
            if not dates:
                continue

            # Check completion date constraint: last payment <= desired_completion_date
            last_date = dates[-1]
            if last_date > self.desired_completion_date:
                continue

            # Simulate cashflow with installments injected
            if not self._is_plan_safe_across_horizon(trace, dates, opt.payment_amount):
                continue

            # Calculate plan string: YYYY-MM-DD:amt|...
            amt_str = self._format_amount(opt.payment_amount)
            plan_str = "|".join(f"{d.strftime('%Y-%m-%d')}:{amt_str}" for d in dates)

            feasible_plans.append({
                "option": opt,
                "plan_str": plan_str,
                "first_date": dates[0],
                "num_payments": opt.number_of_payments,
                "total_payable": opt.total_payable_amount,
                "financing_fee": opt.financing_fee,
            })

        if not feasible_plans:
            return None

        # Rank options deterministically using the specification's final
        # payment-option-ID tie-breaker.
        feasible_plans.sort(
            key=lambda x: (
                x["total_payable"],
                x["first_date"],
                x["num_payments"],
                x["option"].payment_option_id,
            )
        )
        best = feasible_plans[0]
        opt = best["option"]

        explanation = (
            f"Use {opt.number_of_payments} installments of {self.profile.home_currency} {self._format_display_currency(opt.payment_amount)}, "
            f"starting {best['first_date'].day} {best['first_date'].strftime('%B %Y')}. "
            f"This leaves at least {self.profile.home_currency} {self._format_display_currency(self.profile.minimum_balance_to_keep)} available."
        )

        earliest_full = trace.earliest_date_for_full_payment
        if trace.safe_amount >= self.requested_amount:
            earliest_full = self.ctx.request_date

        return DecisionResult(
            request_id=self.ctx.request_id,
            amount_safe_to_pay=trace.safe_amount,
            affordability_status="affordable_with_plan",
            recommended_payment_method="installments",
            payment_plan=best["plan_str"],
            earliest_date_for_full_payment=earliest_full,
            spending_changes_needed="none",
            decision_explanation=explanation,
            chosen_payment_option_id=opt.payment_option_id,
        )

    def _generate_installment_dates(self, opt: PaymentOption) -> List[date]:
        """Generates calendar dates for each installment."""
        if not opt.first_payment_date:
            return []
        try:
            start_date = datetime.strptime(opt.first_payment_date, "%Y-%m-%d").date()
        except ValueError:
            return []

        freq_days = opt.payment_frequency_days if opt.payment_frequency_days else 30
        dates = []
        for i in range(opt.number_of_payments):
            # Frequency addition
            d = start_date + timedelta(days=int(i * freq_days))
            dates.append(d)
        return dates

    def _is_plan_safe_across_horizon(
        self, trace: CashflowAuditTrace, payment_dates: List[date], payment_amount: Decimal
    ) -> bool:
        """
        Verifies that injecting the payment schedule into daily balances
        never breaches minimum_balance_to_keep for all t in [t0, t0+90].
        """
        add_by_day = [Decimal("0")] * 91
        t0 = self.request_date
        horizon_end = t0 + timedelta(days=90)

        for p_date in payment_dates:
            if not t0 <= p_date <= horizon_end:
                return False
            day_idx = (p_date - t0).days
            add_by_day[day_idx] += payment_amount

        cum_added = Decimal("0")
        for day_idx in range(91):
            cum_added += add_by_day[day_idx]
            base_closing = trace.timeline[day_idx].closing_balance
            projected_balance = base_closing - cum_added
            if projected_balance < self.profile.minimum_balance_to_keep:
                return False

        return True

    def _evaluate_partial_payment(self, trace: CashflowAuditTrace) -> Optional[DecisionResult]:
        """
        Evaluates 2-payment partial payment schedule per §6.2:
        - Exactly two payments: safe_amount on request_date, remainder on earliest_date_for_full_payment.
        - Allowed only when request allows it, user accepts it, 0 < safe_amount < requested_amount,
          and second payment <= desired_completion_date.
        """
        considered_methods = set(self.profile.payment_methods_user_will_consider)
        if not self.allows_partial:
            return None
        if considered_methods and "partial_payment" not in considered_methods:
            return None

        safe_amount = trace.safe_amount
        if not (Decimal("0") < safe_amount < self.requested_amount):
            return None

        earliest_full = trace.earliest_date_for_full_payment
        if not earliest_full:
            return None

        second_date = datetime.strptime(earliest_full, "%Y-%m-%d").date()
        if second_date > self.desired_completion_date:
            return None

        remaining_amount = self.requested_amount - safe_amount
        # Check safety of this 2-payment schedule
        if not self._is_schedule_safe(trace, [(self.request_date, safe_amount), (second_date, remaining_amount)]):
            return None

        # Do not round an amount safe-to-pay upward.  The remainder preserves
        # the requested total exactly after the safe first payment is floored.
        payable_today = safe_amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        remaining_amount = self.requested_amount - payable_today
        p1_str = self._format_amount(payable_today)
        p2_str = self._format_amount(remaining_amount)
        plan_str = f"{self.ctx.request_date}:{p1_str}|{earliest_full}:{p2_str}"

        explanation = (
            f"Pay {self.profile.home_currency} {self._format_display_currency(payable_today)} today and the remaining "
            f"{self.profile.home_currency} {self._format_display_currency(remaining_amount)} on "
            f"{second_date.day} {second_date.strftime('%B %Y')}. "
            f"This completes the full request and keeps the {self.profile.home_currency} "
            f"{self._format_display_currency(self.profile.minimum_balance_to_keep)} minimum protected."
        )

        return DecisionResult(
            request_id=self.ctx.request_id,
            amount_safe_to_pay=payable_today,
            affordability_status="affordable_with_plan",
            recommended_payment_method="partial_payment",
            payment_plan=plan_str,
            earliest_date_for_full_payment=earliest_full,
            spending_changes_needed="none",
            decision_explanation=explanation,
        )

    def _is_schedule_safe(self, trace: CashflowAuditTrace, payments: List[Tuple[date, Decimal]]) -> bool:
        """Verifies an arbitrary schedule against minimum_balance_to_keep."""
        add_by_day = [Decimal("0")] * 91
        t0 = self.request_date
        horizon_end = t0 + timedelta(days=90)

        for p_date, amt in payments:
            if not t0 <= p_date <= horizon_end:
                return False
            day_idx = (p_date - t0).days
            add_by_day[day_idx] += amt

        cum_added = Decimal("0")
        for day_idx in range(91):
            cum_added += add_by_day[day_idx]
            base_closing = trace.timeline[day_idx].closing_balance
            if (base_closing - cum_added) < self.profile.minimum_balance_to_keep:
                return False
        return True

    def _evaluate_spending_changes(self, trace: CashflowAuditTrace) -> Optional[DecisionResult]:
        """
        Evaluates up to 3 permitted spending reductions or cancellations to unlock full payment today.
        Only flexible, non-protected events in categories user permits may be modified.
        """
        considered_methods = set(self.profile.payment_methods_user_will_consider)
        if considered_methods and "full_payment" not in considered_methods:
            return None

        deficit = self.requested_amount - trace.safe_amount
        if deficit <= Decimal("0"):
            return None

        # Find candidate recurring expense series.  Historical one-off debits
        # cannot be stopped or reduced, so they must never be used to create
        # a fictional spending-change plan.
        stoppable_cats = set(self.profile.expense_categories_user_is_willing_to_stop)
        reducible_cats = set(self.profile.expense_categories_user_is_willing_to_reduce)
        protected_cats = set(self.profile.expense_categories_to_protect)

        candidates: List[SpendingChange] = []

        normalizer = EventNormalizer(self.ctx)
        monthly_series, periodic_series = normalizer.detect_recurrence()
        recurring_series = {**monthly_series, **periodic_series}
        events_by_id = {event.event_id: event for event in self.ctx.user_events}

        for category, info in recurring_series.items():
            if info["direction"] != "debit" or category in protected_cats:
                continue

            source_event = events_by_id.get(info["last_event_id"])
            if source_event is None:
                continue

            amount = info["last_amt"]
            flexibility = info["flexibility"]
            if category in stoppable_cats and flexibility in ("stoppable", "reducible_or_stoppable"):
                candidates.append(SpendingChange(
                    action="stop",
                    event_id=source_event.event_id,
                    category=category,
                    original_amount=amount,
                    new_amount=Decimal("0"),
                    savings=amount,
                    description=source_event.description,
                ))

            if category in reducible_cats and flexibility in ("reducible", "reducible_or_stoppable"):
                min_amt = info["min_allowed"]
                if min_amt is not None and min_amt < amount:
                    candidates.append(SpendingChange(
                        action="reduce_to",
                        event_id=source_event.event_id,
                        category=category,
                        original_amount=amount,
                        new_amount=min_amt,
                        savings=amount - min_amt,
                        description=source_event.description,
                    ))

        if not candidates:
            return None

        from itertools import combinations
        feasible_combos: List[Tuple[Tuple, Tuple[SpendingChange, ...]]] = []
        for size in range(1, min(4, len(candidates) + 1)):
            for combo in combinations(candidates, size):
                # Stopping and reducing the same event are mutually exclusive.
                if len({change.event_id for change in combo}) != len(combo):
                    continue
                if not self._is_full_payment_safe_with_changes(trace, combo):
                    continue
                total_savings = sum((change.savings for change in combo), Decimal("0"))
                key = (
                    len(combo),
                    total_savings,
                    tuple((change.event_id, change.action) for change in combo),
                )
                feasible_combos.append((key, combo))

        if not feasible_combos:
            return None

        feasible_combos.sort(key=lambda item: item[0])
        best_combo = feasible_combos[0][1]

        # Format spending changes string: stop:event_id|reduce_to:event_id:amt
        parts = []
        for c in best_combo:
            if c.action == "stop":
                parts.append(f"stop:{c.event_id}")
            else:
                parts.append(f"reduce_to:{c.event_id}:{self._format_amount(c.new_amount)}")
        changes_str = "|".join(parts)

        # Build explanation
        desc_parts = []
        for c in best_combo:
            clean_desc = c.description.lower().replace("payment", "").strip()
            if c.action == "stop":
                desc_parts.append(f"stop the {clean_desc}")
            else:
                desc_parts.append(f"reduce the {clean_desc} to {self.profile.home_currency} {self._format_display_currency(c.new_amount)}")

        action_phrase = " and ".join(desc_parts).capitalize()
        plan_str = f"{self.ctx.request_date}:{self._format_amount(self.requested_amount)}"
        explanation = (
            f"{action_phrase}, then pay {self.profile.home_currency} {self._format_display_currency(self.requested_amount)} today. "
            f"This leaves at least {self.profile.home_currency} {self._format_display_currency(self.profile.minimum_balance_to_keep)} available."
        )

        return DecisionResult(
            request_id=self.ctx.request_id,
            amount_safe_to_pay=trace.safe_amount,
            affordability_status="affordable_with_plan",
            recommended_payment_method="full_payment",
            payment_plan=plan_str,
            earliest_date_for_full_payment=self.ctx.request_date,
            spending_changes_needed=changes_str,
            decision_explanation=explanation,
        )

    def _is_full_payment_safe_with_changes(
        self, trace: CashflowAuditTrace, changes: Tuple[SpendingChange, ...]
    ) -> bool:
        """Replays the trace with approved recurring-expense changes applied.

        A change affects matching debit occurrences from the request date onward.
        This is deliberately separate from the candidate-selection estimate so a
        recommendation cannot pass merely because its nominal monthly savings
        exceed a deficit on an unrelated date.
        """
        changes_by_category = {change.category: change for change in changes}
        savings_by_day = [Decimal("0")] * 91
        normalizer = EventNormalizer(self.ctx)
        normalized_events = normalizer.build_normalized_event_timeline(set(trace.reserved_obligation_ids))

        for event in normalized_events:
            change = changes_by_category.get(event.category)
            if change is None or event.direction != "debit":
                continue
            event_date = datetime.strptime(event.settlement_date, "%Y-%m-%d").date()
            day_index = (event_date - self.request_date).days
            if not 0 <= day_index <= 90:
                continue
            if change.action == "stop":
                savings_by_day[day_index] += event.amount
            else:
                savings_by_day[day_index] += max(Decimal("0"), event.amount - change.new_amount)

        cumulative_savings = Decimal("0")
        for day_index, point in enumerate(trace.timeline):
            cumulative_savings += savings_by_day[day_index]
            if point.closing_balance + cumulative_savings - self.requested_amount < self.profile.minimum_balance_to_keep:
                return False
        return True

    # -----------------------------------------------------------------
    # EXPLANATION HELPERS
    # -----------------------------------------------------------------

    def _explain_affordable_now(self, amount: Decimal) -> str:
        return (
            f"Pay {self.profile.home_currency} {self._format_display_currency(amount)} today. "
            f"This leaves at least {self.profile.home_currency} {self._format_display_currency(self.profile.minimum_balance_to_keep)} "
            f"available over the next 90 days."
        )

    def _explain_affordable_later(self, amount: Decimal, earliest_dt: date) -> str:
        day_str = f"{earliest_dt.day} {earliest_dt.strftime('%B %Y')}"
        return (
            f"Pay {self.profile.home_currency} {self._format_display_currency(amount)} in full on {day_str}. "
            f"Paying earlier would take the balance below the {self.profile.home_currency} "
            f"{self._format_display_currency(self.profile.minimum_balance_to_keep)} minimum."
        )

    def _explain_not_affordable(self, safe_amount: Decimal) -> str:
        comp_date = self.desired_completion_date
        comp_str = f"{comp_date.day} {comp_date.strftime('%B %Y')}"
        if self.ctx.payment_options and any(o.payment_method == "installments" for o in self.ctx.payment_options):
            return (
                f"Do not make this payment by {comp_str}. "
                f"None of the available options keeps the {self.profile.home_currency} "
                f"{self._format_display_currency(self.profile.minimum_balance_to_keep)} minimum protected."
            )
        else:
            return (
                f"Do not proceed with the {self.profile.home_currency} {self._format_display_currency(self.requested_amount)} request. "
                f"Although {self.profile.home_currency} {self._format_display_currency(safe_amount)} is available today, "
                f"the full amount cannot be completed safely within 90 days."
            )

    @staticmethod
    def _format_amount(val: Decimal) -> str:
        """Formats Decimal into clean string without unnecessary trailing zeros."""
        val = val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        s = f"{val:.2f}"
        if s.endswith(".00"):
            return s[:-3]
        return s

    @staticmethod
    def _format_display_currency(val: Decimal) -> str:
        """Formats number with commas and standard decimal representation for explanations."""
        val = val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        s = f"{val:,.2f}"
        if s.endswith(".00"):
            return s[:-3]
        return s
