"""
code/data_fusion.py

Deterministic data fusion layer.
Joins and cross-references all participant-facing files in dataset/:
- requests.csv
- financial_profiles.csv
- financial_events.csv
- exchange_rates.csv
- request_payment_options.csv
- messages.csv

Strictly integrates Step 2 image-derived event amounts and Step 3 MessageAnalyzer facts.
Guarantees 100% data immutability for all files on disk.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import pandas as pd

from code.fx_converter import FXConverter
from code.image_extractor import VERIFIED_IMAGE_RESOLUTIONS
from code.message_analyzer import MessageAnalyzer, MessageFact


@dataclass(frozen=True)
class UserProfile:
    """Type-safe financial profile of a user."""
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: Tuple[str, ...]
    expense_categories_to_protect: Tuple[str, ...]
    expense_categories_user_is_willing_to_reduce: Tuple[str, ...]
    expense_categories_user_is_willing_to_stop: Tuple[str, ...]
    payment_methods_user_will_consider: Tuple[str, ...]
    max_installment_months: Optional[int]


@dataclass(frozen=True)
class PaymentOption:
    """Payment option offer available for a specific request."""
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: str
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True)
class FinancialEventRecord:
    """Type-safe, normalized financial event record."""
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str                     # "debit" or "credit"
    amount: Decimal                    # operative amount in home currency
    original_amount: Decimal           # amount in event currency
    currency: str                      # event currency
    home_currency: str                 # user home currency
    event_date: str
    settlement_date: str
    status: str                        # "settled", "pending", "scheduled", "cancelled", "failed", "unrealized"
    linked_event_id: Optional[str]
    flexibility: str                   # "fixed", "reducible", "stoppable", "reducible_or_stoppable"
    minimum_allowed_amount: Optional[Decimal]
    is_image_resolved: bool = False
    fx_rate: Decimal = Decimal("1")


@dataclass(frozen=True)
class RequestContext:
    """Complete, unified context for evaluating a single user request."""
    request_id: str
    user_id: str
    request_date: str
    request_type: str
    requested_amount: Decimal
    desired_completion_date: str
    allows_partial_payment: bool
    request_text: str
    profile: UserProfile
    user_events: Tuple[FinancialEventRecord, ...]
    user_message_facts: Tuple[MessageFact, ...]
    payment_options: Tuple[PaymentOption, ...]


class DataFusionLoader:
    """
    Orchestrates the loading, cross-referencing, Step 2 injection, and Step 3
    message fact integration across the entire workspace.
    """

    def __init__(self, dataset_dir: Optional[Union[str, Path]] = None):
        if dataset_dir is None:
            dataset_dir = Path(__file__).resolve().parent.parent / "dataset"
        self._dataset_dir = Path(dataset_dir)

        # Instantiate foundational converters and extractors
        self._fx = FXConverter(self._dataset_dir / "exchange_rates.csv")
        self._msg_analyzer = MessageAnalyzer(self._dataset_dir)

        # In-memory storage
        self._profiles: Dict[str, UserProfile] = {}
        self._payment_options_by_req: Dict[str, List[PaymentOption]] = {}
        self._events_by_user: Dict[str, List[FinancialEventRecord]] = {}
        self._facts_by_user: Dict[str, List[MessageFact]] = {}
        self._requests: Dict[str, Dict[str, Any]] = {}
        self._sample_requests: Dict[str, Dict[str, Any]] = {}

        self._load_and_fuse()

    def _load_and_fuse(self) -> None:
        # 1. Step 2 missing amounts lookup
        resolved_missing = {rec.event_id: rec.resolved_amount for rec in VERIFIED_IMAGE_RESOLUTIONS.values()}

        # 2. Step 3 facts index
        for f in self._msg_analyzer.facts:
            self._facts_by_user.setdefault(f.user_id, []).append(f)

        # 3. Financial profiles
        prof_df = pd.read_csv(self._dataset_dir / "financial_profiles.csv")
        for _, r in prof_df.iterrows():
            uid = str(r["user_id"]).strip()
            max_inst = None
            if pd.notna(r["max_installment_months"]) and str(r["max_installment_months"]).strip() != "":
                max_inst = int(float(r["max_installment_months"]))

            def split_pipe(val: Any) -> Tuple[str, ...]:
                if pd.isna(val) or not str(val).strip():
                    return ()
                return tuple(p.strip() for p in str(val).split("|") if p.strip())

            self._profiles[uid] = UserProfile(
                user_id=uid,
                home_currency=str(r["home_currency"]).strip(),
                current_available_balance=Decimal(str(r["current_available_balance"])),
                minimum_balance_to_keep=Decimal(str(r["minimum_balance_to_keep"])),
                financial_priorities=split_pipe(r.get("financial_priorities")),
                expense_categories_to_protect=split_pipe(r.get("expense_categories_to_protect")),
                expense_categories_user_is_willing_to_reduce=split_pipe(r.get("expense_categories_user_is_willing_to_reduce")),
                expense_categories_user_is_willing_to_stop=split_pipe(r.get("expense_categories_user_is_willing_to_stop")),
                payment_methods_user_will_consider=split_pipe(r.get("payment_methods_user_will_consider")),
                max_installment_months=max_inst,
            )

        # 4. Payment options
        opt_df = pd.read_csv(self._dataset_dir / "request_payment_options.csv")
        for _, r in opt_df.iterrows():
            req_id = str(r["request_id"]).strip()
            freq_days = None
            if pd.notna(r["payment_frequency_days"]) and str(r["payment_frequency_days"]).strip() != "":
                freq_days = int(float(r["payment_frequency_days"]))

            opt = PaymentOption(
                payment_option_id=str(r["payment_option_id"]).strip(),
                request_id=req_id,
                payment_method=str(r["payment_method"]).strip(),
                payment_amount=Decimal(str(r["payment_amount"])),
                number_of_payments=int(r["number_of_payments"]),
                first_payment_date=str(r["first_payment_date"]).strip(),
                payment_frequency_days=freq_days,
                financing_fee=Decimal(str(r["financing_fee"])) if pd.notna(r["financing_fee"]) else Decimal("0"),
                total_payable_amount=Decimal(str(r["total_payable_amount"])),
            )
            self._payment_options_by_req.setdefault(req_id, []).append(opt)

        # 5. Financial events (with Step 2 injection & FX normalization)
        events_df = pd.read_csv(self._dataset_dir / "financial_events.csv")
        for _, r in events_df.iterrows():
            eid = str(r["event_id"]).strip()
            uid = str(r["user_id"]).strip()
            prof = self._profiles[uid]
            home_curr = prof.home_currency

            # Resolve amount: use Step 2 injection if raw amount was NaN
            is_resolved = False
            if pd.isna(r["amount"]) or str(r["amount"]).strip() == "":
                if eid in resolved_missing:
                    raw_amount = Decimal(str(resolved_missing[eid]))
                    is_resolved = True
                else:
                    raise ValueError(f"Event {eid} has missing amount and was not resolved in Step 2!")
            else:
                raw_amount = Decimal(str(r["amount"]))

            event_curr = str(r["currency"]).strip() if pd.notna(r["currency"]) else home_curr
            event_date = str(r["event_date"]).strip()
            settle_date = str(r["settlement_date"]).strip() if pd.notna(r["settlement_date"]) else event_date

            # Convert to home currency
            fx_record = self._fx.convert(
                amount=raw_amount,
                from_currency=event_curr,
                to_currency=home_curr,
                rate_date=settle_date,
                event_id=eid,
            )

            min_allowed = None
            if pd.notna(r["minimum_allowed_amount"]) and str(r["minimum_allowed_amount"]).strip() != "":
                min_allowed = Decimal(str(r["minimum_allowed_amount"]))

            evt_record = FinancialEventRecord(
                event_id=eid,
                user_id=uid,
                event_type=str(r["event_type"]).strip(),
                description=str(r["description"]).strip(),
                category=str(r["category"]).strip(),
                direction=str(r["direction"]).strip(),
                amount=fx_record.converted_amount,
                original_amount=raw_amount,
                currency=event_curr,
                home_currency=home_curr,
                event_date=event_date,
                settlement_date=settle_date,
                status=str(r["status"]).strip(),
                linked_event_id=str(r["linked_event_id"]).strip() if pd.notna(r["linked_event_id"]) else None,
                flexibility=str(r["flexibility"]).strip() if pd.notna(r["flexibility"]) else "fixed",
                minimum_allowed_amount=min_allowed,
                is_image_resolved=is_resolved,
                fx_rate=fx_record.rate,
            )
            self._events_by_user.setdefault(uid, []).append(evt_record)

        # 6. Evaluation Requests
        req_df = pd.read_csv(self._dataset_dir / "requests.csv")
        for _, r in req_df.iterrows():
            req_id = str(r["request_id"]).strip()
            self._requests[req_id] = {
                "request_id": req_id,
                "user_id": str(r["user_id"]).strip(),
                "request_date": str(r["request_date"]).strip(),
                "request_type": str(r["request_type"]).strip(),
                "requested_amount": Decimal(str(r["requested_amount"])),
                "desired_completion_date": str(r["desired_completion_date"]).strip(),
                "allows_partial_payment": bool(str(r["allows_partial_payment"]).strip().lower() == "true"),
                "request_text": str(r["request_text"]).strip(),
            }

        # 7. Sample Requests (Ground Truth / Reference)
        samples_path = self._dataset_dir / "sample_requests.csv"
        if samples_path.exists():
            sample_df = pd.read_csv(samples_path)
            for _, r in sample_df.iterrows():
                req_id = str(r["request_id"]).strip()
                self._sample_requests[req_id] = {
                    "request_id": req_id,
                    "user_id": str(r["user_id"]).strip(),
                    "request_date": str(r["request_date"]).strip(),
                    "request_type": str(r["request_type"]).strip(),
                    "requested_amount": Decimal(str(r["requested_amount"])),
                    "desired_completion_date": str(r["desired_completion_date"]).strip(),
                    "allows_partial_payment": bool(str(r["allows_partial_payment"]).strip().lower() == "true"),
                    "request_text": str(r["request_text"]).strip(),
                    "amount_safe_to_pay": Decimal(str(r["amount_safe_to_pay"])),
                    "affordability_status": str(r["affordability_status"]).strip(),
                    "recommended_payment_method": str(r["recommended_payment_method"]).strip(),
                    "payment_plan": str(r["payment_plan"]).strip(),
                    "earliest_date_for_full_payment": str(r["earliest_date_for_full_payment"]).strip() if pd.notna(r["earliest_date_for_full_payment"]) else None,
                    "spending_changes_needed": str(r["spending_changes_needed"]).strip(),
                    "decision_explanation": str(r["decision_explanation"]).strip(),
                }

    @property
    def total_requests(self) -> int:
        return len(self._requests)

    @property
    def total_sample_requests(self) -> int:
        return len(self._sample_requests)

    @property
    def total_profiles(self) -> int:
        return len(self._profiles)

    def get_profile(self, user_id: str) -> UserProfile:
        return self._profiles[user_id]

    def get_user_events(self, user_id: str) -> List[FinancialEventRecord]:
        return self._events_by_user.get(user_id, [])

    def get_user_message_facts(self, user_id: str) -> List[MessageFact]:
        return self._facts_by_user.get(user_id, [])

    def get_payment_options(self, request_id: str) -> List[PaymentOption]:
        return self._payment_options_by_req.get(request_id, [])

    def get_request_context(self, request_id: str) -> RequestContext:
        """Constructs the unified, immutable context for a request."""
        if request_id in self._requests:
            req = self._requests[request_id]
        elif request_id in self._sample_requests:
            req = self._sample_requests[request_id]
        else:
            raise KeyError(f"Request {request_id} not found in requests or sample_requests")

        uid = req["user_id"]
        return RequestContext(
            request_id=request_id,
            user_id=uid,
            request_date=req["request_date"],
            request_type=req["request_type"],
            requested_amount=req["requested_amount"],
            desired_completion_date=req["desired_completion_date"],
            allows_partial_payment=req["allows_partial_payment"],
            request_text=req["request_text"],
            profile=self._profiles[uid],
            user_events=tuple(self.get_user_events(uid)),
            user_message_facts=tuple(m for m in self.get_user_message_facts(uid) if m.sent_at[:10] <= req["request_date"]),
            payment_options=tuple(self.get_payment_options(request_id)),
        )

    def get_sample_reference(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._sample_requests.get(request_id)
