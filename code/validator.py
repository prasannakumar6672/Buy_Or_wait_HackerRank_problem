"""
code/validator.py

Strict output validator for HackerRank Orchestrate (Buy or Wait?).
Verifies every single field, enum, syntax rule, date constraint,
and financial invariant required by §6.2 of problem_statement.md.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import pandas as pd

from code.cashflow_engine import CashflowEngine
from code.data_fusion import DataFusionLoader, RequestContext
from code.event_normalizer import EventNormalizer


REQUIRED_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

ALLOWED_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}

ALLOWED_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}

PLAN_PAYMENT_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}:[0-9]+(\.[0-9]{1,2})?$")
SPENDING_STOP_REGEX = re.compile(r"^stop:event_\d+$")
SPENDING_REDUCE_REGEX = re.compile(r"^reduce_to:event_\d+:[0-9]+(\.[0-9]{1,2})?$")


class OutputValidator:
    """Strict evaluation validator for the final submission output.csv."""

    def __init__(self, dataset_dir: Path, output_file: Path):
        self.dataset_dir = Path(dataset_dir)
        self.output_file = Path(output_file)
        self.requests_df = pd.read_csv(self.dataset_dir / "requests.csv")
        self.expected_request_ids = list(self.requests_df["request_id"])
        self.fusion = DataFusionLoader(self.dataset_dir)

    @staticmethod
    def _parse_plan(plan: str) -> Optional[List[Tuple[datetime, Decimal]]]:
        """Parse a payment plan and reject malformed, non-chronological entries."""
        if plan == "none":
            return []
        parsed: List[Tuple[datetime, Decimal]] = []
        try:
            for entry in plan.split("|"):
                date_text, amount_text = entry.split(":", 1)
                parsed.append((datetime.strptime(date_text, "%Y-%m-%d"), Decimal(amount_text)))
        except (ValueError, ArithmeticError):
            return None
        if any(amount <= Decimal("0") for _, amount in parsed):
            return None
        if any(parsed[index][0] > parsed[index + 1][0] for index in range(len(parsed) - 1)):
            return None
        return parsed

    @staticmethod
    def _schedule_is_safe(ctx: RequestContext, trace, payments: List[Tuple[datetime, Decimal]]) -> bool:
        """Independently apply a listed plan against the generated base trace."""
        request_date = datetime.strptime(ctx.request_date, "%Y-%m-%d")
        horizon_end = request_date + timedelta(days=90)
        additions = [Decimal("0")] * 91
        for payment_date, amount in payments:
            if not request_date <= payment_date <= horizon_end:
                return False
            additions[(payment_date - request_date).days] += amount

        cumulative = Decimal("0")
        for day, point in enumerate(trace.timeline):
            cumulative += additions[day]
            if point.closing_balance - cumulative < ctx.profile.minimum_balance_to_keep:
                return False
        return True

    def validate(self) -> Tuple[bool, List[str]]:
        """
        Executes all validation checks.
        Returns (is_valid, list_of_error_messages).
        """
        errors = []

        if not self.output_file.exists():
            return False, [f"Output file does not exist: {self.output_file}"]

        # Read CSV as strings to preserve formatting
        try:
            df = pd.read_csv(self.output_file, dtype=str, keep_default_na=False)
        except Exception as e:
            return False, [f"Failed to read CSV: {e}"]

        # 1. Column names and order
        if list(df.columns) != REQUIRED_COLUMNS:
            errors.append(
                f"Column mismatch! Expected: {REQUIRED_COLUMNS}, Got: {list(df.columns)}"
            )

        # 2. Row count and request_id ordering
        actual_ids = list(df["request_id"])
        if len(actual_ids) != len(self.expected_request_ids):
            errors.append(
                f"Row count mismatch! Expected: {len(self.expected_request_ids)}, Got: {len(actual_ids)}"
            )

        if actual_ids != self.expected_request_ids:
            errors.append("request_id order mismatch or missing/extra IDs relative to requests.csv!")

        # Build lookup for request attributes
        req_lookup = {r["request_id"]: r for _, r in self.requests_df.iterrows()}

        # 3. Row-level validations
        for idx, row in df.iterrows():
            req_id = row["request_id"]
            if req_id not in req_lookup:
                continue
            req_info = req_lookup[req_id]
            req_date = req_info["request_date"]
            req_amount = Decimal(str(req_info["requested_amount"]))
            ctx = self.fusion.get_request_context(req_id)
            trace = CashflowEngine(ctx).simulate()

            # Safe amount validation
            safe_str = str(row["amount_safe_to_pay"]).strip()
            try:
                safe_amt = Decimal(safe_str)
                if safe_amt < Decimal("0"):
                    errors.append(f"Row {idx} ({req_id}): amount_safe_to_pay is negative ({safe_amt})")
                if safe_amt > req_amount:
                    errors.append(f"Row {idx} ({req_id}): amount_safe_to_pay ({safe_amt}) exceeds requested_amount ({req_amount})")
                expected_safe = trace.safe_amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                if safe_amt != expected_safe:
                    errors.append(f"Row {idx} ({req_id}): amount_safe_to_pay ({safe_amt}) does not equal deterministic safe amount ({expected_safe})")
            except Exception:
                errors.append(f"Row {idx} ({req_id}): invalid amount_safe_to_pay format '{safe_str}'")

            # Status enum validation
            status = str(row["affordability_status"]).strip()
            if status not in ALLOWED_STATUSES:
                errors.append(f"Row {idx} ({req_id}): invalid affordability_status '{status}'")

            # Method enum validation
            method = str(row["recommended_payment_method"]).strip()
            if method not in ALLOWED_METHODS:
                errors.append(f"Row {idx} ({req_id}): invalid recommended_payment_method '{method}'")

            # Status & Method semantic consistency
            if status == "affordable_now" and method != "full_payment":
                errors.append(f"Row {idx} ({req_id}): affordable_now requires full_payment method, got '{method}'")

            if status == "affordable_later" and method != "wait":
                errors.append(f"Row {idx} ({req_id}): affordable_later requires wait method, got '{method}'")

            if status == "not_affordable" and method != "not_recommended":
                errors.append(f"Row {idx} ({req_id}): not_affordable requires not_recommended method, got '{method}'")

            # Earliest date validation
            early_str = str(row["earliest_date_for_full_payment"]).strip()
            if status == "affordable_now":
                if early_str != req_date:
                    errors.append(f"Row {idx} ({req_id}): affordable_now requires earliest_date == request_date ({req_date}), got '{early_str}'")
            elif status == "not_affordable":
                if early_str != "":
                    errors.append(f"Row {idx} ({req_id}): not_affordable requires empty earliest_date, got '{early_str}'")
            else:
                if early_str != "":
                    try:
                        datetime.strptime(early_str, "%Y-%m-%d")
                    except ValueError:
                        errors.append(f"Row {idx} ({req_id}): invalid date format for earliest_date '{early_str}'")

            if status == "affordable_with_plan" and method not in {"full_payment", "partial_payment", "installments"}:
                errors.append(f"Row {idx} ({req_id}): affordable_with_plan has invalid method '{method}'")

            # Payment plan syntax validation
            plan_str = str(row["payment_plan"]).strip()
            if status == "not_affordable":
                if plan_str != "none":
                    errors.append(f"Row {idx} ({req_id}): not_affordable requires payment_plan == 'none', got '{plan_str}'")
            else:
                if plan_str != "none":
                    entries = plan_str.split("|")
                    for e in entries:
                        if not PLAN_PAYMENT_REGEX.match(e):
                            errors.append(f"Row {idx} ({req_id}): invalid payment_plan entry syntax '{e}' in '{plan_str}'")

            payments = self._parse_plan(plan_str)
            if payments is None:
                errors.append(f"Row {idx} ({req_id}): payment_plan is malformed or not chronological")
                payments = []

            # Validate the method-specific plan contract and payment-option fidelity.
            request_datetime = datetime.strptime(req_date, "%Y-%m-%d")
            if method == "full_payment":
                if len(payments) != 1 or payments[0] != (request_datetime, req_amount):
                    errors.append(f"Row {idx} ({req_id}): full_payment must be one full payment on request_date")
            elif method == "partial_payment":
                if len(payments) != 2 or not ctx.allows_partial_payment:
                    errors.append(f"Row {idx} ({req_id}): partial_payment requires exactly two permitted payments")
                elif (
                    payments[0][0] != request_datetime
                    or payments[0][1] != safe_amt
                    or sum(amount for _, amount in payments) != req_amount
                    or early_str != payments[1][0].strftime("%Y-%m-%d")
                ):
                    errors.append(f"Row {idx} ({req_id}): partial_payment schedule is inconsistent with safe amount or earliest date")
            elif method == "installments":
                matching_option = False
                for option in ctx.payment_options:
                    if option.payment_method != "installments" or option.number_of_payments != len(payments):
                        continue
                    expected_dates = [
                        datetime.strptime(option.first_payment_date, "%Y-%m-%d") + timedelta(days=index * (option.payment_frequency_days or 30))
                        for index in range(option.number_of_payments)
                    ]
                    if all(payment == (expected_dates[position], option.payment_amount) for position, payment in enumerate(payments)):
                        matching_option = True
                        break
                if not matching_option:
                    errors.append(f"Row {idx} ({req_id}): installments do not exactly match an available payment option")
            elif method == "wait":
                if len(payments) != 1 or payments[0][1] != req_amount or early_str != payments[0][0].strftime("%Y-%m-%d"):
                    errors.append(f"Row {idx} ({req_id}): wait must contain one full payment on earliest_date_for_full_payment")

            # Spending changes needed syntax validation
            spending_str = str(row["spending_changes_needed"]).strip()
            if spending_str != "none":
                changes = spending_str.split("|")
                if len(changes) > 3:
                    errors.append(f"Row {idx} ({req_id}): more than 3 spending changes ({len(changes)})")
                for c in changes:
                    if not (SPENDING_STOP_REGEX.match(c) or SPENDING_REDUCE_REGEX.match(c)):
                        errors.append(f"Row {idx} ({req_id}): invalid spending change syntax '{c}' in '{spending_str}'")

            # Validate that spending changes target an allowed recurring series,
            # and replay the listed full-payment plan with those changes applied.
            changes = []
            if spending_str != "none":
                recurring_monthly, recurring_periodic = EventNormalizer(ctx).detect_recurrence()
                recurring_ids = {info["last_event_id"] for info in {**recurring_monthly, **recurring_periodic}.values()}
                events_by_id = {event.event_id: event for event in ctx.user_events}
                seen_change_ids: Set[str] = set()
                for change in spending_str.split("|"):
                    parts = change.split(":")
                    action, event_id = parts[0], parts[1]
                    event = events_by_id.get(event_id)
                    if event is None or event_id not in recurring_ids:
                        errors.append(f"Row {idx} ({req_id}): spending change must reference an eligible recurring event")
                        continue
                    if event_id in seen_change_ids:
                        errors.append(f"Row {idx} ({req_id}): stop and reduce actions cannot target the same event")
                        continue
                    seen_change_ids.add(event_id)
                    protected = set(ctx.profile.expense_categories_to_protect)
                    if event.category in protected or event.direction != "debit":
                        errors.append(f"Row {idx} ({req_id}): spending change modifies an ineligible event")
                        continue
                    if action == "stop":
                        if event.category not in set(ctx.profile.expense_categories_user_is_willing_to_stop) or event.flexibility not in {"stoppable", "reducible_or_stoppable"}:
                            errors.append(f"Row {idx} ({req_id}): stop is not permitted for {event_id}")
                    else:
                        new_amount = Decimal(parts[2])
                        if (
                            event.category not in set(ctx.profile.expense_categories_user_is_willing_to_reduce)
                            or event.flexibility not in {"reducible", "reducible_or_stoppable"}
                            or event.minimum_allowed_amount is None
                            or not event.minimum_allowed_amount <= new_amount < event.amount
                        ):
                            errors.append(f"Row {idx} ({req_id}): reduction is not permitted for {event_id}")

            if method in {"full_payment", "partial_payment", "installments", "wait"} and spending_str == "none":
                if payments and not self._schedule_is_safe(ctx, trace, payments):
                    errors.append(f"Row {idx} ({req_id}): payment plan breaches the 90-day minimum balance")
            if method == "full_payment" and spending_str != "none":
                # The decision engine's change-aware replay validates savings at
                # their actual future dates, not merely by their nominal total.
                from code.decision_engine import DecisionEngine, SpendingChange
                change_objects = []
                for change in spending_str.split("|"):
                    parts = change.split(":")
                    event = next((item for item in ctx.user_events if item.event_id == parts[1]), None)
                    if event is None:
                        continue
                    new_amount = Decimal("0") if parts[0] == "stop" else Decimal(parts[2])
                    change_objects.append(SpendingChange(parts[0], event.event_id, event.category, event.amount, new_amount, event.amount - new_amount, event.description))
                if not change_objects or not DecisionEngine(ctx)._is_full_payment_safe_with_changes(trace, tuple(change_objects)):
                    errors.append(f"Row {idx} ({req_id}): spending-change plan breaches the 90-day minimum balance")

            # Explanation validation
            expl = str(row["decision_explanation"]).strip()
            if not expl or len(expl) < 5:
                errors.append(f"Row {idx} ({req_id}): empty or trivial decision_explanation")

        is_valid = (len(errors) == 0)
        return is_valid, errors
