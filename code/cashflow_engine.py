"""
code/cashflow_engine.py

Deterministic 90-day cashflow simulation engine for HackerRank Orchestrate.

Rules & Invariants (Constraints 1, 2, 11, 12, 13, 14, 15):
1. Pure Determinism: 100% Python and Decimal arithmetic. Zero LLM involvement.
2. Double-Counting Defense: Pending debits reserved immediately at t0; tracked by ID; never deducted again.
3. 90-Day Boundary: Evaluates exactly [t0, t0 + 90 days] inclusive (91 discrete calendar dates). Day 91 has zero influence.
4. Safe Amount Formula:
   safe_amount = min(requested_amount, max(0, min_t(balance_without_purchase(t) - minimum_balance_to_keep)))
5. Earliest Date for Full Payment:
   First date T in [t0, t0 + 90] where full requested amount can be paid at T and balance >= min_balance for all t in [T, t0 + 90].
6. Complete Auditability: Exposes structured daily timeline and per-request cashflow trace.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from code.data_fusion import RequestContext
from code.event_normalizer import EventNormalizer, EventStatusPolicy, NormalizedEvent


@dataclass(frozen=True)
class DailyCashflowPoint:
    """Audit point for a single discrete calendar date in the 90-day horizon."""
    date: str
    day_index: int                         # 0 to 90
    opening_balance: Decimal
    inflows: Decimal
    outflows: Decimal
    closing_balance: Decimal
    headroom: Decimal                      # closing_balance - minimum_balance_to_keep
    events: Tuple[NormalizedEvent, ...]


@dataclass(frozen=True)
class CashflowAuditTrace:
    """Complete, auditable 90-day cashflow trace for a single request evaluation."""
    request_id: str
    user_id: str
    request_date: str
    horizon_end_date: str
    initial_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    pending_debits_reserved: Decimal
    reserved_obligation_ids: Tuple[str, ...]
    min_projected_balance: Decimal
    min_projected_balance_date: str
    min_headroom: Decimal
    safe_amount: Decimal
    earliest_date_for_full_payment: Optional[str]
    timeline: Tuple[DailyCashflowPoint, ...]

    def get_timeline_dict(self) -> List[Dict[str, Any]]:
        return [
            {
                "date": pt.date,
                "day_index": pt.day_index,
                "opening_balance": float(pt.opening_balance),
                "inflows": float(pt.inflows),
                "outflows": float(pt.outflows),
                "closing_balance": float(pt.closing_balance),
                "headroom": float(pt.headroom),
                "event_count": len(pt.events),
            }
            for pt in self.timeline
        ]


class CashflowEngine:
    """
    Deterministic cashflow simulator across the 90-day horizon.
    """

    def __init__(self, ctx: RequestContext):
        self.ctx = ctx
        self.normalizer = EventNormalizer(ctx)
        self.request_date = datetime.strptime(ctx.request_date, "%Y-%m-%d").date()
        self.horizon_end = self.request_date + timedelta(days=90)
        self.initial_balance = ctx.profile.current_available_balance
        self.min_keep = ctx.profile.minimum_balance_to_keep
        self.requested_amount = ctx.requested_amount

    def _find_next_confirmed_salary_date(self) -> Optional[date]:
        """
        Finds the first confirmed regular salary credit date after request_date.
        Checks explicit scheduled salary events in the forward timeline.
        If none, uses historical settled salary dominant day-of-month.
        Returns None if user has no confirmed salary stream or employment ended.
        """
        t0 = self.request_date
        # 1. Scheduled/settled salary in forward user events
        scheduled_sals = [
            datetime.strptime(e.settlement_date, "%Y-%m-%d").date()
            for e in self.ctx.user_events
            if e.category == "salary" and e.direction == "credit" and e.status in ("scheduled", "settled")
            and datetime.strptime(e.settlement_date, "%Y-%m-%d").date() > t0
        ]
        if scheduled_sals:
            return min(scheduled_sals)

        # 2. Check if contract or employment ended per message facts
        for f in self.ctx.user_message_facts:
            if "contract_end" in f.classification or "termination" in f.classification:
                return None

        # 3. Dominant DOM from settled historical salaries
        settled_sals = [
            datetime.strptime(e.settlement_date, "%Y-%m-%d").date()
            for e in self.ctx.user_events
            if e.category == "salary" and e.direction == "credit" and e.status == "settled"
        ]
        if not settled_sals:
            return None

        doms = [d.day for d in settled_sals[-4:]]
        dom = max(set(doms), key=doms.count)

        if t0.day < dom:
            try:
                return date(t0.year, t0.month, dom)
            except ValueError:
                return date(t0.year, t0.month, 28)
        else:
            m = t0.month + 1 if t0.month < 12 else 1
            y = t0.year if t0.month < 12 else t0.year + 1
            try:
                return date(y, m, dom)
            except ValueError:
                return date(y, m, 28)

    def simulate(self) -> CashflowAuditTrace:
        """
        Executes the deterministic 90-day daily balance projection.
        Enforces all non-negotiable competition rules.
        """
        # 1. Immediate Pending Debit Reservation (Constraint 2)
        pending_debits_to_reserve = self.normalizer.get_pending_debits_to_reserve()
        reserved_obligation_ids: Set[str] = {e.event_id for e in pending_debits_to_reserve}
        total_pending_reserved: Decimal = sum((e.amount for e in pending_debits_to_reserve), Decimal("0"))

        # Invariant check: pending reservations cannot be negative
        if total_pending_reserved < Decimal("0"):
            raise ValueError(f"Negative pending debit reservation detected for request {self.ctx.request_id}")

        # Starting operative balance at t0 after immediate reservation
        running_balance = self.initial_balance - total_pending_reserved

        # 2. Build forward normalized events
        normalized_events = self.normalizer.build_normalized_event_timeline(reserved_obligation_ids)

        # Index events by settlement_date
        events_by_date: Dict[str, List[NormalizedEvent]] = {}
        for ev in normalized_events:
            events_by_date.setdefault(ev.settlement_date, []).append(ev)

        # 3. Simulate discrete 91 calendar days [t0, t0 + 90]
        timeline_points: List[DailyCashflowPoint] = []
        min_projected_balance = running_balance
        min_projected_date = self.request_date
        min_headroom = running_balance - self.min_keep

        # Record for earliest full payment calculation
        daily_balances: List[Decimal] = []

        for day_offset in range(91):
            cur_date = self.request_date + timedelta(days=day_offset)
            date_str = cur_date.strftime("%Y-%m-%d")

            opening_balance = running_balance
            inflows = Decimal("0")
            outflows = Decimal("0")
            day_events: List[NormalizedEvent] = []

            if date_str in events_by_date:
                for ev in events_by_date[date_str]:
                    # Verify status policy
                    if EventStatusPolicy.is_valid_cashflow_on_date(ev, cur_date, self.request_date, reserved_obligation_ids):
                        day_events.append(ev)
                        if ev.direction == "credit":
                            inflows += ev.amount
                        elif ev.direction == "debit":
                            # Invariant: Double-counting defense check
                            if ev.event_id in reserved_obligation_ids:
                                raise RuntimeError(
                                    f"DOUBLE-COUNTING DEFENSE TRIGGERED: Reserved pending debit {ev.event_id} "
                                    f"attempted to deduct again on settlement date {date_str}!"
                                )
                            outflows += ev.amount

            closing_balance = opening_balance + inflows - outflows
            headroom = closing_balance - self.min_keep
            running_balance = closing_balance
            daily_balances.append(closing_balance)

            # Update minimum tracking
            if closing_balance < min_projected_balance:
                min_projected_balance = closing_balance
                min_projected_date = cur_date

            if headroom < min_headroom:
                min_headroom = headroom

            timeline_points.append(DailyCashflowPoint(
                date=date_str,
                day_index=day_offset,
                opening_balance=opening_balance,
                inflows=inflows,
                outflows=outflows,
                closing_balance=closing_balance,
                headroom=headroom,
                events=tuple(day_events),
            ))

        # 4. Safe Amount Calculation (Constraint 12 & problem_statement.md §182)
        # safe_amount is the most the user can pay today without breaking the 90-day safety check
        safe_amount = min(self.requested_amount, max(Decimal("0"), min_headroom))

        # 5. Earliest Date for Full Payment (Constraint 13)
        # First date T in [t0, t0 + 90] where balance(t) - requested_amount >= min_keep for all t in [T, t0 + 90]
        earliest_full_payment_date: Optional[str] = None

        if safe_amount == self.requested_amount:
            # Full amount is safe today
            earliest_full_payment_date = self.request_date.strftime("%Y-%m-%d")
        else:
            for day_idx in range(91):
                # Check if full requested_amount can be paid on day_idx without breaching min_keep subsequently
                can_pay_in_full = True
                for future_idx in range(day_idx, 91):
                    projected_after_payment = daily_balances[future_idx] - self.requested_amount
                    if projected_after_payment < self.min_keep:
                        can_pay_in_full = False
                        break

                if can_pay_in_full:
                    cand_date = self.request_date + timedelta(days=day_idx)
                    earliest_full_payment_date = cand_date.strftime("%Y-%m-%d")
                    break

        return CashflowAuditTrace(
            request_id=self.ctx.request_id,
            user_id=self.ctx.user_id,
            request_date=self.ctx.request_date,
            horizon_end_date=self.horizon_end.strftime("%Y-%m-%d"),
            initial_available_balance=self.initial_balance,
            minimum_balance_to_keep=self.min_keep,
            pending_debits_reserved=total_pending_reserved,
            reserved_obligation_ids=tuple(sorted(reserved_obligation_ids)),
            min_projected_balance=min_projected_balance,
            min_projected_balance_date=min_projected_date.strftime("%Y-%m-%d"),
            min_headroom=min_headroom,
            safe_amount=safe_amount,
            earliest_date_for_full_payment=earliest_full_payment_date,
            timeline=tuple(timeline_points),
        )
