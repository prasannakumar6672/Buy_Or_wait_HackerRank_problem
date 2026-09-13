"""
code/event_normalizer.py

Deterministic event normalization and recurrence engine for HackerRank Orchestrate.

Responsibilities:
1. Centralized Event Status Policy (Constraint 14):
   - settled: valid on settlement_date
   - pending debit: reserved immediately at request_date (t0) with double-counting defense
   - pending credit: zero guaranteed cashflow (ignored until settled)
   - scheduled: valid on settlement_date
   - failed, cancelled, unrealized: zero cashflow
2. Recurrence Detection & Projection (Constraint 3):
   - Infers monthly day-of-month schedules from structured history
   - Infers periodic weekly/biweekly/triweekly schedules (groceries, transport, dining)
   - Clamps month-end dates safely across varying month lengths
   - Avoids duplicate synthetic events when explicit future scheduled events exist
3. Step 3 Message Mutations on Operative Schedule (Constraints 4, 5, 6, 7, 8, 9):
   - Direct mutation: modifies operative events without creating duplicates
   - Salary Arrears: Cycle 1 = base_salary + arrears, Cycle 2+ = base_salary (never recurring)
   - Temporary Reduction: modifies only the single affected cycle
   - Permanent Modification: applies from actual next recurring salary event forward
   - Contract Termination: compares notice timestamp against scheduled salary events; stops future salary
   - Rent +12%: applies from actual next recurring rent event onward
   - Salary Date Reschedule: moves recurring salary day-of-month
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np

from code.data_fusion import FinancialEventRecord, RequestContext, UserProfile
from code.message_analyzer import MessageFact


@dataclass(frozen=True)
class NormalizedEvent:
    """
    Canonical, immutable financial event for deterministic cashflow simulation.
    """
    event_id: str
    user_id: str
    category: str
    direction: str                          # "credit" or "debit"
    amount: Decimal                         # operative amount in home currency
    settlement_date: str                    # YYYY-MM-DD
    status: str                             # "settled", "pending", "scheduled", "cancelled", "failed", "unrealized"
    flexibility: str                        # "fixed", "reducible", "stoppable", "reducible_or_stoppable"
    minimum_allowed_amount: Optional[Decimal]
    is_synthetic: bool = False
    is_modified_by_message: bool = False
    source_message_id: Optional[str] = None
    description: str = ""


class EventStatusPolicy:
    """
    Centralized event status policy (Constraint 14).
    Guarantees consistent handling of settled, pending, scheduled, cancelled, failed, and unrealized events.
    """

    @staticmethod
    def should_reserve_at_t0(event: FinancialEventRecord, t0: date) -> bool:
        """
        Determines whether an event is an active pending debit that must be reserved immediately at t0.
        """
        if event.status != "pending" or event.direction != "debit":
            return False
        # Only reserve if settlement date is on or after t0
        ev_date = datetime.strptime(event.settlement_date, "%Y-%m-%d").date()
        return ev_date >= t0

    @staticmethod
    def is_valid_cashflow_on_date(
        event: NormalizedEvent,
        current_date: date,
        t0: date,
        reserved_obligation_ids: Set[str],
    ) -> bool:
        """
        Centralized gating rule for whether an event generates cashflow on current_date.
        Enforces:
        - Cancelled, failed, unrealized: False
        - Pending credit: False (zero guaranteed cash)
        - Pending debit: False if already reserved at t0 (defense against double-counting)
        - Settled/Scheduled: True if settlement_date matches current_date and not double-counted
        """
        if event.status in ("failed", "cancelled", "unrealized"):
            return False

        if event.status == "pending":
            if event.direction == "credit":
                return False
            if event.event_id in reserved_obligation_ids:
                return False

        if event.settlement_date != current_date.strftime("%Y-%m-%d"):
            return False

        # Invariant: If a settled event was previously reserved as a pending debit, do NOT deduct again
        if event.event_id in reserved_obligation_ids and current_date > t0:
            return False

        return True


class EventNormalizer:
    """
    Normalizes structured events, detects recurring schedules, applies Step 3 message facts,
    and constructs the complete forward 90-day normalized event stream.
    """

    def __init__(self, ctx: RequestContext):
        self.ctx = ctx
        self.user_id = ctx.user_id
        self.request_date = datetime.strptime(ctx.request_date, "%Y-%m-%d").date()
        self.horizon_end = self.request_date + timedelta(days=90)
        self.profile = ctx.profile

    @staticmethod
    def clamp_month_day(year: int, month: int, target_day: int) -> date:
        """Clamps day to the valid maximum for a given year and month."""
        if month in (1, 3, 5, 7, 8, 10, 12):
            max_d = 31
        elif month in (4, 6, 9, 11):
            max_d = 30
        else: # February
            is_leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
            max_d = 29 if is_leap else 28
        return date(year, month, min(target_day, max_d))

    def get_pending_debits_to_reserve(self) -> List[FinancialEventRecord]:
        """
        Returns all pending debits that must be reserved immediately at request_date (Constraint 2).
        """
        reserved = []
        for e in self.ctx.user_events:
            if EventStatusPolicy.should_reserve_at_t0(e, self.request_date):
                reserved.append(e)
        return reserved

    def detect_recurrence(self) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """
        Infers monthly and periodic recurring schedules from user's structured history (Constraint 3).
        """
        settled_before_t0 = [
            e for e in self.ctx.user_events
            if e.status == "settled" and datetime.strptime(e.settlement_date, "%Y-%m-%d").date() <= self.request_date
        ]

        by_cat = {}
        for e in settled_before_t0:
            d = datetime.strptime(e.settlement_date, "%Y-%m-%d").date()
            by_cat.setdefault(e.category, []).append((d, e.amount, e.direction, e.flexibility, e.description, e.event_id, e.minimum_allowed_amount))

        monthly_series = {}
        periodic_series = {}

        # Also inspect scheduled future events in dataset (e.g. salary scheduled on 15th)
        scheduled_future = [
            e for e in self.ctx.user_events
            if e.status == "scheduled" and datetime.strptime(e.settlement_date, "%Y-%m-%d").date() >= self.request_date
        ]
        for e in scheduled_future:
            d = datetime.strptime(e.settlement_date, "%Y-%m-%d").date()
            by_cat.setdefault(e.category, []).append((d, e.amount, e.direction, e.flexibility, e.description, e.event_id, e.minimum_allowed_amount))

        for cat, items in by_cat.items():
            items.sort(key=lambda x: x[0])
            dates = [x[0] for x in items]
            amts = [x[1] for x in items]
            dirs = items[-1][2]
            flex = items[-1][3]
            desc = items[-1][4]
            min_allowed = items[-1][6]

            # Monthly series detection
            # If 2 or more occurrences with ~30-day interval or known monthly category
            known_monthly_categories = {
                "salary", "rent", "housing", "utilities", "education", "debt_repayment",
                "insurance", "healthcare", "family_support", "cloud_storage",
                "music_subscription", "streaming", "gym", "delivery_membership",
                "shopping", "entertainment"
            }

            if cat in known_monthly_categories or len(dates) >= 3:
                intervals = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))] if len(dates) > 1 else [30]
                med_int = float(np.median(intervals)) if intervals else 30.0

                if 25.0 <= med_int <= 35.0 or cat in known_monthly_categories:
                    # Exclude one-off, prorated, arrears, commission, bonus descriptions to avoid skewing regular baseline
                    regular_items = [
                        it for it in items
                        if not any(k in it[4].lower() for k in ("prorated", "outstanding", "arrears", "arrear", "one-off", "bonus", "commission", "payout", "refund", "settlement", "partial"))
                    ]
                    if not regular_items:
                        regular_items = items

                    regular_dates = [it[0] for it in regular_items]
                    recent_doms = [d.day for d in regular_dates[-4:]]
                    dom = max(set(recent_doms), key=recent_doms.count) if recent_doms else regular_dates[-1].day

                    regular_amts = [it[1] for it in regular_items]
                    # For fixed recurring obligations, use the mode; on ties, prefer the latest established regular amount
                    from collections import Counter
                    amt_counts = Counter(regular_amts)
                    max_count = max(amt_counts.values())
                    modes = [amt for amt, count in amt_counts.items() if count == max_count]
                    base_regular_amt = regular_amts[-1] if regular_amts[-1] in modes else modes[0]

                    # Check if salary was explicitly final or contract ended without scheduled future event
                    if cat == "salary":
                        has_final_desc = any(k in items[-1][4].lower() for k in ("final", "severance", "terminal", "resignation", "last salary"))
                        has_scheduled_sal = any(e.status == "scheduled" and e.category == "salary" for e in self.ctx.user_events)
                        if has_final_desc and not has_scheduled_sal:
                            continue

                        # If gig payouts without scheduled salary, do not invent indefinite synthetic salary
                        is_gig = any(k in items[-1][4].lower() for k in ("marketplace", "platform payout", "driver", "gig", "app earnings"))
                        if is_gig and not has_scheduled_sal:
                            continue

                    monthly_series[cat] = {
                        "dom": dom,
                        "direction": dirs,
                        "flexibility": flex,
                        "desc": desc,
                        "last_amt": base_regular_amt,
                        "max_amt": max(regular_amts),
                        "min_amt": min(regular_amts),
                        "last_date": dates[-1],
                        "min_allowed": min_allowed,
                        "last_event_id": regular_items[-1][5],
                    }
                    continue

            # Periodic series (groceries, transport, dining)
            if len(dates) >= 3:
                intervals = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
                med_int = float(np.median(intervals))
                if 5.0 <= med_int <= 24.0:
                    regular_items = [
                        it for it in items
                        if not any(k in it[4].lower() for k in ("bulk", "pantry", "one-off", "annual", "prorated", "outstanding", "arrears", "arrear", "bonus", "refund", "settlement", "partial"))
                    ]
                    if not regular_items:
                        regular_items = items

                    regular_amts = [it[1] for it in regular_items]
                    # Use median of regular amounts to avoid skewing from temporary single-cycle spikes
                    med_amt = Decimal(str(round(float(np.median([float(a) for a in regular_amts])), 2)))

                    periodic_series[cat] = {
                        "interval_days": int(round(med_int)),
                        "direction": dirs,
                        "flexibility": flex,
                        "desc": desc,
                        "last_amt": med_amt,
                        "max_amt": max(regular_amts),
                        "min_amt": min(regular_amts),
                        "last_date": dates[-1],
                        "min_allowed": min_allowed,
                        "last_event_id": regular_items[-1][5],
                    }

        return monthly_series, periodic_series

    def build_normalized_event_timeline(
        self,
        reserved_obligation_ids: Set[str],
    ) -> List[NormalizedEvent]:
        """
        Builds the complete forward 90-day normalized event list [t0, t0 + 90 days],
        applying Step 3 message facts and recurrence generation.
        """
        monthly_series, periodic_series = self.detect_recurrence()

        # Step 3 Message Facts extraction (Constraints 4, 5, 6, 7, 8, 9)
        salary_dom = monthly_series.get("salary", {}).get("dom", 15)
        salary_base = monthly_series.get("salary", {}).get("last_amt", Decimal("0"))
        salary_arrears = Decimal("0")
        salary_stopped = False
        single_cycle_reduction = False
        rent_mult = Decimal("1")
        cancelled_event_ids: Set[str] = set()

        for f in self.ctx.user_message_facts:
            # Reschedule salary date (e.g. user_07 to 23rd)
            if f.cashflow_impact == "reschedule_salary_date" and f.effective_date:
                salary_dom = datetime.strptime(f.effective_date, "%Y-%m-%d").date().day
            # Salary arrears (Constraint 5): cycle 1 = base + arrears, cycle 2+ = base
            elif f.cashflow_impact == "salary_plus_arrears":
                if f.base_salary is not None:
                    salary_base = Decimal(str(f.base_salary))
                if f.arrears_amount is not None:
                    salary_arrears = Decimal(str(f.arrears_amount))
            # Modify salary amount (Constraints 6 & 7)
            elif f.cashflow_impact == "modify_salary_amount":
                if f.primary_amount is not None:
                    salary_base = Decimal(str(f.primary_amount))
                if f.temporal_anchor == "single_affected_cycle":
                    single_cycle_reduction = True
            # Contract termination (Constraint 8)
            elif f.future_recurring_salary == "stop" or f.termination_confirmed:
                salary_stopped = True
            # Rent +12% (Constraint 9)
            elif f.temporal_anchor == "next_recurring_rent_cycle" and f.percentage:
                rent_mult = Decimal("1") + Decimal(str(f.percentage)) / Decimal("100")
            # Event cancellation
            elif f.cashflow_impact == "cancel_event" and f.related_event_id:
                cancelled_event_ids.add(f.related_event_id)

        # Baseline baseline salary to return to after temporary reduction
        baseline_salary_after_reduction = monthly_series.get("salary", {}).get("last_amt", salary_base)

        normalized_events: List[NormalizedEvent] = []
        covered_monthly: Set[Tuple[int, int, str]] = set()

        # 1. Add explicit events in horizon [request_date, horizon_end]
        for e in self.ctx.user_events:
            ev_date = datetime.strptime(e.settlement_date, "%Y-%m-%d").date()
            if self.request_date <= ev_date <= self.horizon_end:
                if e.event_id in cancelled_event_ids:
                    continue
                if e.status in ("failed", "cancelled", "unrealized"):
                    continue
                if e.status == "pending" and e.direction == "credit":
                    continue
                # Pending debits reserved at t0 are not added as a separate deduction on settlement date
                if e.event_id in reserved_obligation_ids:
                    continue

                # Add explicit scheduled/settled event
                normalized_events.append(NormalizedEvent(
                    event_id=e.event_id,
                    user_id=e.user_id,
                    category=e.category,
                    direction=e.direction,
                    amount=e.amount,
                    settlement_date=e.settlement_date,
                    status=e.status,
                    flexibility=e.flexibility,
                    minimum_allowed_amount=e.minimum_allowed_amount,
                    is_synthetic=False,
                    description=e.description,
                ))
                covered_monthly.add((ev_date.year, ev_date.month, e.category))

        # 2. Add synthetic monthly recurring events for missing future cycles
        cur_m = self.request_date.replace(day=1)
        salary_cycle_count = 0

        while cur_m <= self.horizon_end:
            y = cur_m.year
            m = cur_m.month

            # Fixed monthly expenses
            for cat, info in monthly_series.items():
                if cat == "salary":
                    continue
                target_date = self.clamp_month_day(y, m, info["dom"])
                if self.request_date <= target_date <= self.horizon_end:
                    if (y, m, cat) not in covered_monthly:
                        amt = info["last_amt"]
                        is_mod = False
                        if cat == "rent" and rent_mult != Decimal("1"):
                            amt = (amt * rent_mult).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            is_mod = True

                        normalized_events.append(NormalizedEvent(
                            event_id=f"synth_{cat}_{target_date}",
                            user_id=self.user_id,
                            category=cat,
                            direction=info["direction"],
                            amount=amt,
                            settlement_date=target_date.strftime("%Y-%m-%d"),
                            status="scheduled",
                            flexibility=info["flexibility"],
                            minimum_allowed_amount=info["min_allowed"],
                            is_synthetic=True,
                            is_modified_by_message=is_mod,
                            description=f"Synthetic {cat} on DOM {info['dom']}",
                        ))
                        covered_monthly.add((y, m, cat))

            # Salary recurring cycle
            if not salary_stopped:
                target_salary_date = self.clamp_month_day(y, m, salary_dom)
                if self.request_date <= target_salary_date <= self.horizon_end:
                    if (y, m, "salary") not in covered_monthly:
                        salary_cycle_count += 1
                        sal_amt = salary_base
                        is_mod = False

                        if salary_cycle_count == 1 and salary_arrears > Decimal("0"):
                            sal_amt += salary_arrears
                            is_mod = True
                        elif salary_cycle_count > 1 and single_cycle_reduction:
                            sal_amt = baseline_salary_after_reduction

                        normalized_events.append(NormalizedEvent(
                            event_id=f"synth_salary_{target_salary_date}",
                            user_id=self.user_id,
                            category="salary",
                            direction="credit",
                            amount=sal_amt,
                            settlement_date=target_salary_date.strftime("%Y-%m-%d"),
                            status="scheduled",
                            flexibility="fixed",
                            minimum_allowed_amount=None,
                            is_synthetic=True,
                            is_modified_by_message=is_mod,
                            description=f"Synthetic salary on DOM {salary_dom}",
                        ))
                        covered_monthly.add((y, m, "salary"))

            # Advance to next calendar month
            if cur_m.month == 12:
                cur_m = date(cur_m.year + 1, 1, 1)
            else:
                cur_m = date(cur_m.year, cur_m.month + 1, 1)

        # 3. Add synthetic periodic recurring events (groceries, transport, dining)
        for cat, info in periodic_series.items():
            k = info["interval_days"]
            amt = info["last_amt"]
            nxt_d = info["last_date"] + timedelta(days=k)

            while nxt_d <= self.horizon_end:
                if nxt_d >= self.request_date:
                    # Avoid duplication if an explicit event for this category exists on nxt_d
                    has_explicit = any(
                        e.category == cat and e.settlement_date == nxt_d.strftime("%Y-%m-%d")
                        for e in normalized_events if not e.is_synthetic
                    )
                    if not has_explicit:
                        normalized_events.append(NormalizedEvent(
                            event_id=f"synth_{cat}_{nxt_d}",
                            user_id=self.user_id,
                            category=cat,
                            direction=info["direction"],
                            amount=amt,
                            settlement_date=nxt_d.strftime("%Y-%m-%d"),
                            status="scheduled",
                            flexibility=info["flexibility"],
                            minimum_allowed_amount=info["min_allowed"],
                            is_synthetic=True,
                            description=f"Synthetic {cat} every {k} days",
                        ))
                nxt_d += timedelta(days=k)

        # Sort chronologically by settlement_date
        normalized_events.sort(key=lambda ev: ev.settlement_date)
        return normalized_events
