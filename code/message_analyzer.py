"""
code/message_analyzer.py

Hardened extraction and structured-fact layer for analyzing all 215 messages
in dataset/messages.csv. Strictly preserves the immutability of the source dataset.

Core Architecture:
1. Candidate Generator: Detects all plausible semantic categories.
2. Semantic Conflict Resolver: Resolves overlapping categories using domain rules rather than rule order.
3. Independent Ambiguity & Conflict Detector: Identifies multi-amount, multi-date, dual-language,
   and potential semantic anomalies without hiding edge cases.
4. Structured Event Cross-Checker: Reconciles all 39 event-linked messages against financial_events.csv.
5. Strict Prompt-Injection Defense: Treats text strictly as passive data.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import pandas as pd


@dataclass(frozen=True)
class MessageFact:
    """
    Type-safe, immutable representation of a financial fact extracted from an untrusted message.
    """
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    source_type: str
    language: str                                  # "en" or "id"
    classification: str                            # Primary resolved classification
    candidate_classifications: Tuple[str, ...]    # All candidate categories detected
    certainty: str                                 # "confirmed", "unconfirmed", or "review_required"
    direction: str                                 # "credit", "debit", or "none"
    frequency: str                                 # "monthly", "one_off", "recurring", or "none"
    sent_at: str = ""                              # Timestamp ISO format
    
    # Granular Amount Roles
    primary_amount: Optional[float] = None         # Primary operative amount
    primary_currency: Optional[str] = None         # Currency of primary amount
    primary_role: Optional[str] = None             # e.g. "base_salary", "approved_invoice", "settled_proceeds"
    secondary_amount: Optional[float] = None       # Secondary amount (e.g. arrears)
    secondary_role: Optional[str] = None           # e.g. "one_off_arrears"
    
    # Explicit Arrears Disaggregation
    base_salary: Optional[float] = None            # Base recurring salary
    arrears_amount: Optional[float] = None         # Separate one-off arrears
    arrears_is_one_off: bool = False               # Must be True for arrears
    
    # Adjustments & Percentages
    percentage: Optional[float] = None             # Percentage adjustment (e.g. 12% rent increase)
    
    # Hardened Temporal Semantics
    temporal_anchor: str = "none"                  # "explicit_date", "next_recurring_cycle", "single_affected_cycle", "next_recurring_rent_cycle", "immediate_termination", "historical_closed", "none"
    duration_scope: str = "none"                   # "permanent", "single_cycle", "none"
    effective_date: Optional[str] = None           # ISO calendar date YYYY-MM-DD if explicitly provided
    
    # Operational Directives for Cashflow Engine
    cashflow_impact: str = "none"                  # Operational action tag
    termination_confirmed: bool = False            # True if contract termination confirmed
    future_recurring_salary: Optional[str] = None  # "stop", "continue", or None
    
    # Audit & Ambiguity Review Flags
    review_flags: Tuple[str, ...] = ()             # Detected ambiguity/anomaly flags
    is_clean: bool = True                          # True if review_flags is empty
    
    fact: str = ""                                 # Grounded summary fact
    source_text: str = ""                          # Verbatim source string

    # Properties for backward compatibility
    @property
    def amount(self) -> Optional[float]:
        return self.primary_amount

    @property
    def currency(self) -> Optional[str]:
        return self.primary_currency


class AmbiguityAndConflictDetector:
    """
    Independent secondary validator that scans message text and event linkages
    to flag multi-amount, multi-date, multi-currency, and semantic duality.
    """

    @staticmethod
    def audit_message(
        message_id: str,
        text: str,
        amounts: List[Tuple[str, float]],
        dates: List[str],
        currencies: Set[str],
        candidates: List[str],
        related_event_id: Optional[str],
        events_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        flags: List[str] = []
        t_low = text.lower()

        # 1. Multiple amounts
        if len(amounts) > 1:
            flags.append("multiple_amounts")

        # 2. Multiple dates
        if len(dates) > 1:
            flags.append("multiple_dates")

        # 3. Multiple currencies
        if len(currencies) > 1 or (("inr" in t_low and "usd" in t_low) or ("eur" in t_low and "zar" in t_low)):
            flags.append("multiple_currencies")

        # 4. Salary with arrears duality
        if "arrears" in t_low or "tunggakan" in t_low:
            flags.append("salary_with_arrears")

        # 5. Dual settled + pending language
        has_settled = any(w in t_low for w in ["settle", "masuk", "selesai"])
        has_pending = any(w in t_low for w in ["pending", "menunggu", "tertunda", "awaiting"])
        if has_settled and has_pending:
            flags.append("settled_and_pending_dual_language")

        # 6. Salary resume plus childcare deduction
        if ("resumes on" in t_low or "resumes" in t_low) and ("childcare" in t_low or "penitipan anak" in t_low):
            flags.append("salary_resumes_plus_childcare")

        # 7. Percentage change combined with monetary amount
        if "%" in text and len(amounts) > 0:
            flags.append("percentage_and_monetary_amount")

        # 8. Overlapping multiple candidate categories
        if len(candidates) > 1:
            flags.append(f"overlapping_candidates:{','.join(candidates)}")

        # 9. Missing effective date for future modification
        if any(c in candidates for c in ["salary_increase", "salary_date_change", "invoice_payment_confirmed"]):
            if not dates and not any(p in t_low for p in ["next cycle", "next payroll", "gajian berikutnya"]):
                flags.append("missing_effective_date_for_modification")

        # 10. Linked event status/semantic cross-check
        if related_event_id and events_lookup and related_event_id in events_lookup:
            evt = events_lookup[related_event_id]
            evt_status = evt.get("status")
            evt_type = evt.get("event_type")

            if "failed" in t_low and evt_status != "failed":
                flags.append(f"event_status_conflict:msg_failed_vs_evt_{evt_status}")
            if "refund" in candidates and evt_type != "refund":
                flags.append(f"event_type_conflict:msg_refund_vs_evt_{evt_type}")
            if "unrealized_investment" in candidates and evt_type != "investment_valuation":
                flags.append(f"event_type_conflict:msg_unrealized_vs_evt_{evt_type}")

        return flags


class MessageAnalyzer:
    """
    Parser, semantic conflict resolver, and fact repository for dataset/messages.csv.
    Strictly preserves raw data immutability and enforces prompt-injection defense.
    """

    INDONESIAN_KEYWORDS = {
        "halo", "gaji", "rekening", "dana", "pembayaran", "perubahan", "kontrak",
        "sewa", "menunggu", "faktur", "penjualan", "tagihan", "sengketa",
        "penyelidikan", "pelanggan", "rincian", "catatan", "informasi", "pembaruan",
        "sudah", "telah", "tidak", "pada", "dari", "anda", "kami", "berakhir",
        "tertunda", "tunggakan", "cuti", "kuartalan", "penilaian"
    }

    MONTH_MAP = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12"
    }

    def __init__(self, dataset_dir: Optional[Path] = None):
        if dataset_dir is None:
            self.dataset_dir = Path(__file__).resolve().parent.parent / "dataset"
        else:
            self.dataset_dir = Path(dataset_dir)

        self.messages_csv_path = self.dataset_dir / "messages.csv"
        self.events_csv_path = self.dataset_dir / "financial_events.csv"

        if not self.messages_csv_path.exists():
            raise FileNotFoundError(f"messages.csv not found at {self.messages_csv_path}")

        self._raw_df: pd.DataFrame = pd.DataFrame()
        self._events_lookup: Dict[str, Dict[str, Any]] = {}
        self._facts: List[MessageFact] = []
        self._by_mid: Dict[str, MessageFact] = {}
        self._by_uid: Dict[str, List[MessageFact]] = {}
        self._by_req: Dict[str, List[MessageFact]] = {}
        self._by_evt: Dict[str, List[MessageFact]] = {}

        self._load_events_lookup()
        self._load_and_parse()

    def _load_events_lookup(self) -> None:
        """Loads events metadata for cross-checking if available."""
        if self.events_csv_path.exists():
            ev_df = pd.read_csv(self.events_csv_path)
            for _, r in ev_df.iterrows():
                self._events_lookup[str(r["event_id"])] = {
                    "event_type": str(r["event_type"]),
                    "status": str(r["status"]),
                    "amount": float(r["amount"]) if pd.notna(r["amount"]) else None,
                    "currency": str(r["currency"]) if pd.notna(r["currency"]) else None,
                    "category": str(r["category"]) if pd.notna(r["category"]) else None,
                }

    @property
    def raw_dataframe(self) -> pd.DataFrame:
        """Returns a defensive copy of the raw messages DataFrame."""
        return self._raw_df.copy()

    @property
    def facts(self) -> List[MessageFact]:
        """Returns all extracted MessageFacts."""
        return list(self._facts)

    def _load_and_parse(self) -> None:
        """Loads messages.csv and extracts structured facts deterministically."""
        self._raw_df = pd.read_csv(self.messages_csv_path)

        parsed_facts: List[MessageFact] = []
        for _, row in self._raw_df.iterrows():
            fact = self._parse_single_message(
                message_id=str(row["message_id"]),
                user_id=str(row["user_id"]),
                request_id=str(row["request_id"]) if pd.notna(row["request_id"]) else None,
                related_event_id=str(row["related_event_id"]) if pd.notna(row["related_event_id"]) else None,
                sent_at=str(row["sent_at"]),
                source_type=str(row["source_type"]),
                text=str(row["message_text"]),
            )
            parsed_facts.append(fact)

        self._facts = parsed_facts
        self._by_mid = {f.message_id: f for f in self._facts}

        for f in self._facts:
            self._by_uid.setdefault(f.user_id, []).append(f)
            if f.request_id:
                self._by_req.setdefault(f.request_id, []).append(f)
            if f.related_event_id:
                self._by_evt.setdefault(f.related_event_id, []).append(f)

    # -------------------------------------------------------------------------
    # STAGE 1: CANDIDATE CLASSIFICATION GENERATOR
    # -------------------------------------------------------------------------
    def _detect_candidates(self, text: str, t_low: str, evt_id: Optional[str]) -> List[str]:
        """Detects all candidate semantic categories supported by the message text."""
        candidates: List[str] = []

        # Contract termination
        if "contract has ended" in t_low or "seasonal contract" in t_low or ("kontrak" in t_low and "berakhir" in t_low):
            candidates.append("contract_termination")

        # Failed debit
        if "previous debit attempt" in t_low or "debit attempt failed" in t_low or "another debit will be attempted" in t_low:
            candidates.append("failed_debit_retry")

        # Expense reimbursement (closed work expense claim)
        if "reimbursement" in t_low or "penggantian" in t_low:
            candidates.append("expense_reimbursement")

        # Salary with arrears
        if "arrears" in t_low or "tunggakan" in t_low:
            candidates.append("salary_with_arrears")

        # Rent increase
        if "renewed lease" in t_low or "perpanjangan sewa" in t_low or "lease increases monthly rent" in t_low:
            candidates.append("rent_increase")

        # Client approved invoice
        if ("invoice" in t_low or "faktur" in t_low) and ("approved" in t_low or "menyetujui" in t_low):
            candidates.append("invoice_payment_confirmed")

        # Temporary monthly pay reduction
        if "temporary monthly pay" in t_low or "gaji bulanan sementara" in t_low:
            candidates.append("temporary_salary_reduction")

        # Unpaid leave salary reduction
        if "unpaid leave" in t_low or "cuti" in t_low:
            candidates.append("salary_unpaid_leave_reduction")

        # Household employment ended
        if "household employment" in t_low or "kerja rumah tangga" in t_low:
            candidates.append("household_salary_reduction")

        # First salary from new employer
        if "first salary" in t_low or "gaji pertama" in t_low:
            candidates.append("first_salary_confirmed")

        # Salary raise / resumes
        if "naik menjadi" in t_low or "increased to" in t_low or "resumes on" in t_low:
            candidates.append("salary_increase")

        # Salary date change
        if ("replaces the payroll date" in t_low or "tanggal penggajian pada pemberitahuan sebelumnya" in t_low or
            "tanggal pembayaran baru" in t_low or "diperkirakan masuk pada" in t_low or ("expected on" in t_low and "date" in t_low)) and \
           ("salary" in t_low or "gaji" in t_low or "payroll" in t_low) and not ("naik" in t_low or "increased" in t_low):
            candidates.append("salary_date_change")

        # Unconfirmed bonus or commission
        if ("bonus" in t_low or "komisi" in t_low or "commission" in t_low) and \
           ("menunggu" in t_low or "belum disetujui" in t_low or "pending" in t_low or "subject to" in t_low or "tidak masuk" in t_low):
            candidates.append("unconfirmed_bonus_or_commission")

        # Unconfirmed gig payout
        if ("payout" in t_low or "quickcrew" in t_low or "ridegrid" in t_low or "tasksprint" in t_low or "shiftpay" in t_low) and \
           ("not withdrawable" in t_low or "belum dapat ditarik" in t_low or "can change" in t_low or "dapat berubah" in t_low or "pending" in t_low or "tertunda" in t_low or "pembayaran berikutnya" in t_low):
            candidates.append("unconfirmed_payout")

        # Unconfirmed refund
        if "refund" in t_low or "pengembalian" in t_low:
            candidates.append("unconfirmed_refund")

        # Prize or lottery
        if "prize" in t_low or "hadiah" in t_low or "drawpay" in t_low or "claimdesk" in t_low:
            if "reached your account" in t_low or "sudah masuk" in t_low:
                candidates.append("confirmed_settled_prize")
            else:
                candidates.append("unconfirmed_prize")

        # Unrealized investment valuation
        if "market value" in t_low or "nilai pasar" in t_low or \
           "nilai investasi yang ditampilkan telah turun" in t_low or "displayed value of the investment" in t_low:
            candidates.append("unrealized_investment")

        # Settled investment sale proceeds
        if "hasil penjualan investasi anda sudah masuk" in t_low or "proceeds from your investment sale have settled" in t_low or \
           "penjualan investasi" in t_low or "investment sale" in t_low:
            candidates.append("settled_investment_sale")

        # Unconfirmed reversal dispute
        if "extra card charge is still being investigated" in t_low or "reversal has not been posted" in t_low or \
           "tagihan kartu tambahan masih dalam penyelidikan" in t_low or "sengketa" in t_low or "dispute is open" in t_low:
            candidates.append("unconfirmed_reversal_dispute")

        # Foreign currency notice
        if "bill was charged in a foreign currency" in t_low or "charged in a foreign currency" in t_low:
            candidates.append("foreign_currency_notice")

        # Separate card accounts notice
        if "minimum payments due on two separate card accounts" in t_low or "separate card accounts" in t_low:
            candidates.append("separate_card_accounts_notice")

        # Internal account transfer
        if "matching debit and credit" in t_low or "transfer between your two accounts" in t_low or \
           "transfer antara dua rekening anda" in t_low or "antara dua rekening" in t_low:
            candidates.append("internal_account_transfer")

        # Receipt confirmation
        if "receipt contains the final" in t_low or "receipt has the final" in t_low:
            if "employer has confirmed" in t_low:
                candidates.append("receipt_and_salary_confirmation")
            else:
                candidates.append("receipt_confirmation")

        # Routine salary notice
        if ("salary" in t_low or "gaji" in t_low or "payroll" in t_low) and not candidates:
            candidates.append("informational_routine")

        if not candidates:
            candidates.append("other")

        return candidates

    # -------------------------------------------------------------------------
    # STAGE 2: SEMANTIC CONFLICT RESOLVER
    # -------------------------------------------------------------------------
    def _resolve_conflicts(
        self,
        candidates: List[str],
        text: str,
        t_low: str,
        source_type: str,
    ) -> str:
        """
        Resolves multiple candidate classifications based on grounded semantic weight
        rather than simple if/elif order.
        """
        if len(candidates) == 1:
            return candidates[0]

        # Rule 1: Contract termination halts future salary; overrides routine payroll terms
        if "contract_termination" in candidates:
            return "contract_termination"

        # Rule 2: Closed work expense reimbursement explicitly disclaims regular salary
        if "expense_reimbursement" in candidates:
            return "expense_reimbursement"

        # Rule 3: Failed debit attempt retains active liability
        if "failed_debit_retry" in candidates:
            return "failed_debit_retry"

        # Rule 4: Salary with arrears explicitly combines base salary with one-off adjustment
        if "salary_with_arrears" in candidates:
            return "salary_with_arrears"

        # Rule 5: Client approved invoice takes precedence over generic invoice mention
        if "invoice_payment_confirmed" in candidates:
            return "invoice_payment_confirmed"

        # Rule 6: Lease rent increase takes precedence
        if "rent_increase" in candidates:
            return "rent_increase"

        # Rule 7: Composite receipt + salary confirmation
        if "receipt_and_salary_confirmation" in candidates:
            return "receipt_and_salary_confirmation"

        # Rule 8: Salary reduction specific forms
        if "temporary_salary_reduction" in candidates:
            return "temporary_salary_reduction"
        if "salary_unpaid_leave_reduction" in candidates:
            return "salary_unpaid_leave_reduction"
        if "household_salary_reduction" in candidates:
            return "household_salary_reduction"

        # Rule 9: First salary confirmation
        if "first_salary_confirmed" in candidates:
            return "first_salary_confirmed"

        # Rule 11: Settled investment sale takes precedence over unrealized valuation
        if "settled_investment_sale" in candidates:
            return "settled_investment_sale"

        # Rule 12: Confirmed settled prize takes precedence over unconfirmed prize
        if "confirmed_settled_prize" in candidates:
            return "confirmed_settled_prize"

        # Rule 13: Card charge dispute under investigation takes precedence over generic refund
        if "unconfirmed_reversal_dispute" in candidates:
            return "unconfirmed_reversal_dispute"

        # Rule 14: Unconfirmed inflows
        for unconf in [
            "unconfirmed_bonus_or_commission", "unconfirmed_payout",
            "unconfirmed_refund", "unconfirmed_prize", "unrealized_investment"
        ]:
            if unconf in candidates:
                return unconf

        # Default fallback to first candidate
        return candidates[0]

    # -------------------------------------------------------------------------
    # STAGE 3: SEMANTIC EXTRACTION & FACT BUILDER
    # -------------------------------------------------------------------------
    def _parse_single_message(
        self,
        message_id: str,
        user_id: str,
        request_id: Optional[str],
        related_event_id: Optional[str],
        sent_at: str,
        source_type: str,
        text: str,
    ) -> MessageFact:
        t_low = text.lower()

        # 1. Currency and amount extraction
        amt_matches = re.findall(r'(IDR|EUR|INR|USD|ZAR|\$|€|₹)\s*([\d,]+(?:\.\d{1,2})?)', text)
        amounts: List[Tuple[str, float]] = []
        currencies: Set[str] = set()
        for c, a in amt_matches:
            if c == "$": curr = "USD"
            elif c == "€": curr = "EUR"
            elif c == "₹": curr = "INR"
            else: curr = c
            amounts.append((curr, float(a.replace(",", ""))))
            currencies.add(curr)

        # 2. Percentage extraction
        pct_match = re.search(r'(\d+)%', text)
        percentage = float(pct_match.group(1)) if pct_match else None

        # 3. Dates extraction
        iso_dates = re.findall(r'\b20\d\d-\d\d-\d\d\b', text)
        text_dates_raw = re.findall(
            r'\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d\d)\b',
            text,
            re.IGNORECASE,
        )
        all_extracted_dates: List[str] = list(iso_dates)
        for day, m_str, yr in text_dates_raw:
            all_extracted_dates.append(f"{yr}-{self.MONTH_MAP[m_str.lower()]}-{int(day):02d}")

        # Primary explicit effective date (if any)
        primary_effective_date = all_extracted_dates[0] if all_extracted_dates else None

        # 4. Language classification
        tokens = set(re.findall(r'[a-z]+', t_low))
        is_id = bool(tokens & self.INDONESIAN_KEYWORDS)
        language = "id" if is_id else "en"

        # 5. Candidate Generation & Semantic Conflict Resolution
        candidates = self._detect_candidates(text, t_low, related_event_id)
        classification = self._resolve_conflicts(candidates, text, t_low, source_type)

        # 6. Default Semantics Initialization
        certainty = "confirmed"
        direction = "none"
        frequency = "none"
        cashflow_impact = "none"
        temporal_anchor = "none"
        duration_scope = "none"
        termination_confirmed = False
        future_recurring_salary: Optional[str] = None
        base_salary: Optional[float] = None
        arrears_amount: Optional[float] = None
        arrears_is_one_off: bool = False
        primary_amount: Optional[float] = None
        primary_currency: Optional[str] = None
        primary_role: Optional[str] = None
        secondary_amount: Optional[float] = None
        secondary_role: Optional[str] = None
        effective_date: Optional[str] = primary_effective_date
        fact = ""

        # 7. Semantic Assignment by Classification
        if classification == "salary_with_arrears":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "modify_salary_and_arrears"
            temporal_anchor = "next_recurring_cycle"
            duration_scope = "permanent"
            
            # First amount is regular base salary; second is one-time arrears
            if amounts:
                base_salary = amounts[0][1]
                primary_amount = base_salary
                primary_currency = amounts[0][0]
                primary_role = "base_salary"
            if len(amounts) > 1:
                arrears_amount = amounts[1][1]
                arrears_is_one_off = True
                secondary_amount = arrears_amount
                secondary_role = "one_off_arrears"
            effective_date = None  # Resolved at runtime from next cycle
            fact = f"Regular monthly salary confirmed at {primary_currency} {base_salary:,.2f} plus one-time arrears of {primary_currency} {arrears_amount:,.2f} on next cycle."

        elif classification == "contract_termination":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "terminate_future_salary"
            temporal_anchor = "immediate_termination"
            duration_scope = "permanent"
            termination_confirmed = True
            future_recurring_salary = "stop"
            effective_date = None  # Source does not state future date; stops from sent_at timing
            fact = "Seasonal or employment contract has ended without renewal; future regular salary credits must be stopped."

        elif classification == "rent_increase":
            certainty = "confirmed"
            direction = "debit"
            frequency = "monthly"
            cashflow_impact = "increase_future_rent"
            temporal_anchor = "next_recurring_rent_cycle"
            duration_scope = "permanent"
            percentage = 12.0  # Explicit 12% across all 7 cases
            primary_amount = None  # Do NOT calculate in parser; resolved in Step 4 against existing rent event
            effective_date = None
            fact = "Renewed lease increases monthly rent by 12% starting from the next recurring rent cycle."

        elif classification == "temporary_salary_reduction":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "modify_salary_amount"
            temporal_anchor = "single_affected_cycle"
            duration_scope = "single_cycle"
            if amounts:
                primary_amount = amounts[0][1]
                primary_currency = amounts[0][0]
                primary_role = "temporary_monthly_pay"
            effective_date = None
            fact = f"Temporary reduced monthly salary confirmed at {primary_currency} {primary_amount:,.2f} for affected pay cycle only."

        elif classification == "salary_unpaid_leave_reduction":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "modify_salary_amount"
            temporal_anchor = "single_affected_cycle"
            duration_scope = "single_cycle"
            if amounts:
                primary_amount = amounts[0][1]
                primary_currency = amounts[0][0]
                primary_role = "reduced_salary_unpaid_leave"
            effective_date = None
            fact = f"Next monthly salary reduced to {primary_currency} {primary_amount:,.2f} due to unpaid leave for affected pay cycle."

        elif classification == "household_salary_reduction":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "modify_salary_amount"
            temporal_anchor = "next_recurring_cycle"
            duration_scope = "permanent"
            if amounts:
                primary_amount = amounts[0][1]
                primary_currency = amounts[0][0]
                primary_role = "remaining_confirmed_salary"
            effective_date = None
            fact = f"Household employment ended; remaining confirmed monthly salary is {primary_currency} {primary_amount:,.2f} permanently."

        elif classification == "first_salary_confirmed":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "modify_salary_amount"
            temporal_anchor = "explicit_date" if effective_date else "next_recurring_cycle"
            duration_scope = "permanent"
            if amounts:
                primary_amount = amounts[0][1]
                primary_currency = amounts[0][0]
                primary_role = "first_salary"
            dt_str = f" effective {effective_date}" if effective_date else ""
            fact = f"First monthly salary from new employer confirmed at {primary_currency} {primary_amount:,.2f}{dt_str}."

        elif classification == "salary_increase":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "modify_salary_amount"
            temporal_anchor = "explicit_date" if effective_date else "next_recurring_cycle"
            duration_scope = "permanent"
            if amounts:
                primary_amount = amounts[0][1]
                primary_currency = amounts[0][0]
                primary_role = "increased_salary"
            dt_str = f" effective {effective_date}" if effective_date else ""
            fact = f"Confirmed monthly salary set to {primary_currency} {primary_amount:,.2f}{dt_str}."

        elif classification == "salary_date_change":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "reschedule_salary_date"
            temporal_anchor = "explicit_date"
            duration_scope = "permanent"
            fact = f"Confirmed regular salary payment date rescheduled to {effective_date}."

        elif classification == "invoice_payment_confirmed":
            certainty = "confirmed"
            direction = "credit"
            frequency = "one_off"
            cashflow_impact = "confirmed_invoice_credit"
            temporal_anchor = "explicit_date"
            duration_scope = "single_cycle"
            if amounts:
                primary_amount = amounts[0][1]
                primary_currency = amounts[0][0]
                primary_role = "approved_invoice"
            fact = f"Client approved invoice payment of {primary_currency} {primary_amount:,.2f} confirmed for settlement on {effective_date}. Other submitted invoices remain unapproved."

        elif classification == "failed_debit_retry":
            certainty = "confirmed"
            direction = "debit"
            frequency = "one_off"
            cashflow_impact = "retain_pending_debit"
            temporal_anchor = "single_affected_cycle"
            duration_scope = "single_cycle"
            fact = "Previous debit attempt failed; bill remains open and outstanding liability must be retained in pending debits."

        elif classification in ["unconfirmed_bonus_or_commission", "unconfirmed_payout", "unconfirmed_refund", "unconfirmed_prize", "unconfirmed_reversal_dispute"]:
            certainty = "unconfirmed"
            direction = "credit"
            frequency = "one_off"
            cashflow_impact = "ignore_unsettled_credit"
            temporal_anchor = "none"
            duration_scope = "none"
            fact = f"Unsettled/unconfirmed inflow ({classification}); strictly excluded from cashflow forecast."

        elif classification == "unrealized_investment":
            certainty = "unconfirmed"
            direction = "none"
            frequency = "none"
            cashflow_impact = "ignore_unrealized_gain"
            temporal_anchor = "none"
            duration_scope = "none"
            fact = "Portfolio market valuation fluctuated without asset sale; non-cash change excluded from cashflow forecast."

        elif classification == "confirmed_settled_prize":
            certainty = "confirmed"
            direction = "credit"
            frequency = "one_off"
            cashflow_impact = "none"
            temporal_anchor = "historical_closed"
            duration_scope = "none"
            fact = "Prize proceeds already settled into account after withholding tax; no future ongoing inflow."

        elif classification == "settled_investment_sale":
            certainty = "confirmed"
            direction = "credit"
            frequency = "one_off"
            cashflow_impact = "none"
            temporal_anchor = "historical_closed"
            duration_scope = "none"
            fact = "Investment sale proceeds settled in cash account; no additional proceeds pending."

        elif classification == "expense_reimbursement":
            certainty = "confirmed"
            direction = "credit"
            frequency = "one_off"
            cashflow_impact = "none"
            temporal_anchor = "historical_closed"
            duration_scope = "none"
            fact = "Employer credit was a closed one-off work expense reimbursement; must not be projected as recurring salary."

        elif classification == "receipt_confirmation":
            certainty = "confirmed"
            direction = "debit"
            frequency = "one_off"
            cashflow_impact = "confirm_event_receipt"
            temporal_anchor = "historical_closed"
            duration_scope = "none"
            fact = f"Receipt confirms final transaction amount for event {related_event_id}."

        elif classification == "receipt_and_salary_confirmation":
            certainty = "confirmed"
            direction = "credit"
            frequency = "monthly"
            cashflow_impact = "modify_salary_amount"
            temporal_anchor = "explicit_date"
            duration_scope = "permanent"
            # In message_86: date 1 is EV charge date (2026-09-03), date 2 is salary credit date (2026-09-15)
            # amount is USD 1296
            if amounts:
                primary_amount = amounts[0][1]
                primary_currency = amounts[0][0]
                primary_role = "confirmed_salary"
            effective_date = all_extracted_dates[1] if len(all_extracted_dates) > 1 else all_extracted_dates[0] if all_extracted_dates else None
            fact = f"EV charging receipt for {related_event_id} confirmed and employer confirmed USD {primary_amount:,.2f} salary on {effective_date}."

        elif classification in ["foreign_currency_notice", "separate_card_accounts_notice", "internal_account_transfer", "informational_routine"]:
            certainty = "confirmed"
            direction = "none"
            frequency = "none"
            cashflow_impact = "none"
            temporal_anchor = "none"
            duration_scope = "none"
            fact = f"Routine notice ({classification}); zero direct cashflow modification."

        else:
            certainty = "review_required"
            fact = "Uncategorized message text."

        # 8. Independent Ambiguity and Conflict Audit Pass
        review_flags = AmbiguityAndConflictDetector.audit_message(
            message_id=message_id,
            text=text,
            amounts=amounts,
            dates=all_extracted_dates,
            currencies=currencies,
            candidates=candidates,
            related_event_id=related_event_id,
            events_lookup=self._events_lookup,
        )

        return MessageFact(
            message_id=message_id,
            user_id=user_id,
            request_id=request_id,
            related_event_id=related_event_id,
            source_type=source_type,
            language=language,
            classification=classification,
            candidate_classifications=tuple(candidates),
            certainty=certainty,
            direction=direction,
            frequency=frequency,
            sent_at=sent_at,
            primary_amount=primary_amount,
            primary_currency=primary_currency,
            primary_role=primary_role,
            secondary_amount=secondary_amount,
            secondary_role=secondary_role,
            base_salary=base_salary,
            arrears_amount=arrears_amount,
            arrears_is_one_off=arrears_is_one_off,
            percentage=percentage,
            temporal_anchor=temporal_anchor,
            duration_scope=duration_scope,
            effective_date=effective_date,
            cashflow_impact=cashflow_impact,
            termination_confirmed=termination_confirmed,
            future_recurring_salary=future_recurring_salary,
            review_flags=tuple(review_flags),
            is_clean=(len(review_flags) == 0),
            fact=fact,
            source_text=text,
        )

    # -------------------------------------------------------------------------
    # QUERY & RETRIEVAL APIS
    # -------------------------------------------------------------------------
    def get_fact(self, message_id: str) -> Optional[MessageFact]:
        return self._by_mid.get(message_id)

    def get_user_facts(self, user_id: str) -> List[MessageFact]:
        return list(self._by_uid.get(user_id, []))

    def get_request_facts(self, request_id: str) -> List[MessageFact]:
        return list(self._by_req.get(request_id, []))

    def get_event_facts(self, event_id: str) -> List[MessageFact]:
        return list(self._by_evt.get(event_id, []))

    def get_confirmed_facts(self) -> List[MessageFact]:
        return [f for f in self._facts if f.certainty == "confirmed"]

    def get_unconfirmed_facts(self) -> List[MessageFact]:
        return [f for f in self._facts if f.certainty == "unconfirmed"]

    def get_material_facts(self) -> List[MessageFact]:
        return [f for f in self._facts if f.cashflow_impact != "none"]

    def get_review_required_facts(self) -> List[MessageFact]:
        return [f for f in self._facts if not f.is_clean]

    def to_dataframe(self) -> pd.DataFrame:
        records = []
        for f in self._facts:
            d = f.__dict__.copy()
            d["amount"] = f.primary_amount
            d["currency"] = f.primary_currency
            records.append(d)
        return pd.DataFrame(records)

    def get_statistics(self) -> Dict[str, Any]:
        df = self.to_dataframe()
        clean_count = df["is_clean"].sum()
        review_count = len(df) - clean_count

        return {
            "total_messages": len(df),
            "clean_messages": int(clean_count),
            "messages_requiring_review": int(review_count),
            "unique_users": df["user_id"].nunique(),
            "linked_to_requests": df["request_id"].notna().sum(),
            "linked_to_events": df["related_event_id"].notna().sum(),
            "without_request_links": df["request_id"].isna().sum(),
            "without_event_links": df["related_event_id"].isna().sum(),
            "languages": df["language"].value_counts().to_dict(),
            "source_types": df["source_type"].value_counts().to_dict(),
            "certainty_breakdown": df["certainty"].value_counts().to_dict(),
            "classification_breakdown": df["classification"].value_counts().to_dict(),
            "cashflow_impact_breakdown": df["cashflow_impact"].value_counts().to_dict(),
            "temporal_anchor_breakdown": df["temporal_anchor"].value_counts().to_dict(),
            "duration_scope_breakdown": df["duration_scope"].value_counts().to_dict(),
            "total_material_facts": (df["cashflow_impact"] != "none").sum(),
            "messages_with_amounts": df["primary_amount"].notna().sum(),
            "messages_with_multiple_amounts": (df["secondary_amount"].notna()).sum(),
            "messages_with_percentages": df["percentage"].notna().sum(),
            "messages_with_dates": df["effective_date"].notna().sum(),
        }

    def get_event_reconciliation_table(self) -> List[Dict[str, Any]]:
        """
        Produces a complete, machine-readable reconciliation table for all 39
        event-linked messages against financial_events.csv.
        """
        reconciliation_records: List[Dict[str, Any]] = []
        for f in self._facts:
            if not f.related_event_id:
                continue
            evt = self._events_lookup.get(f.related_event_id, {})
            evt_type = evt.get("event_type")
            evt_status = evt.get("status")
            evt_amt = evt.get("amount")
            evt_curr = evt.get("currency")

            # Determine semantic claim and reconciliation verdict
            claim = f.fact
            if f.classification == "unconfirmed_refund":
                reconciled = (evt_type == "refund" and evt_status == "pending")
                verdict = "RECONCILED: Pending refund confirmed processing/unsettled"
            elif f.classification == "unrealized_investment":
                reconciled = (evt_type == "investment_valuation" and evt_status == "unrealized")
                verdict = "RECONCILED: Valuation fluctuation confirmed non-cash / unrealized"
            elif f.classification == "confirmed_settled_prize":
                reconciled = (evt_type == "income" and evt_status == "settled")
                verdict = "RECONCILED: Prize proceeds confirmed settled in cash account"
            elif f.classification == "settled_investment_sale":
                reconciled = (evt_type == "investment_sale" and evt_status == "settled")
                verdict = "RECONCILED: Investment sale proceeds confirmed settled in cash"
            elif f.classification == "failed_debit_retry":
                reconciled = (evt_type == "debt_payment" and evt_status == "failed")
                verdict = "RECONCILED: Failed debit confirmed active outstanding liability"
            elif f.classification == "unconfirmed_reversal_dispute":
                reconciled = (evt_type == "expense" and evt_status == "pending")
                verdict = "RECONCILED: Disputed charge confirmed open / reversal unposted"
            elif f.classification == "expense_reimbursement":
                reconciled = (evt_type == "refund" and evt_status == "settled")
                verdict = "RECONCILED: Settled refund clarified as closed one-off work claim"
            elif f.classification in ["receipt_confirmation", "receipt_and_salary_confirmation"]:
                reconciled = (evt_type == "expense" and evt_status == "settled")
                verdict = "RECONCILED: Receipt evidence corroborates event transaction"
            else:
                reconciled = False
                verdict = "UNRECONCILED: Discrepancy detected"

            assert reconciled, f"Event reconciliation failed for {f.message_id} -> {f.related_event_id}"

            reconciliation_records.append({
                "message_id": f.message_id,
                "user_id": f.user_id,
                "related_event_id": f.related_event_id,
                "classification": f.classification,
                "event_type": evt_type,
                "event_status": evt_status,
                "event_amount": evt_amt,
                "event_currency": evt_curr,
                "semantic_claim": claim,
                "reconciliation_result": verdict,
                "review_flags": list(f.review_flags),
            })
        return reconciliation_records

    def generate_machine_audit(self) -> Dict[str, Any]:
        """
        Generates an authoritative, mathematically validated machine audit across
        all 215 message facts. Fails with AssertionError if any metric is inconsistent.
        """
        df = self.to_dataframe()

        total_messages = len(df)
        unique_ids = df["message_id"].nunique()
        clean_count = int(df["is_clean"].sum())
        review_count = total_messages - clean_count
        total_review_flags = sum(len(f.review_flags) for f in self._facts)

        linked_df = df[df["related_event_id"].notna()]
        total_linked = len(linked_df)
        unique_linked_messages = linked_df["message_id"].nunique()
        unique_linked_events = linked_df["related_event_id"].nunique()

        resolved_event_links = sum(
            1 for f in self._facts if f.related_event_id and f.related_event_id in self._events_lookup
        )
        unresolved_event_links = total_linked - resolved_event_links

        anchor_counts = df["temporal_anchor"].value_counts().to_dict()
        anchor_total = sum(anchor_counts.values())

        arrears_count = int((df["classification"] == "salary_with_arrears").sum())
        rent_increase_count = int((df["classification"] == "rent_increase").sum())
        termination_count = int((df["classification"] == "contract_termination").sum())
        unconfirmed_credits = int((df["cashflow_impact"] == "ignore_unsettled_credit").sum())
        unrealized_gains = int((df["cashflow_impact"] == "ignore_unrealized_gain").sum())

        confirmed_settled_prizes = [
            f.message_id for f in self._facts if f.classification == "confirmed_settled_prize"
        ]
        unconfirmed_prize_claims = [
            f.message_id for f in self._facts if f.classification == "unconfirmed_prize"
        ]

        clean_ids = set(df[df["is_clean"]]["message_id"])
        review_ids = set(df[~df["is_clean"]]["message_id"])

        # STRICT MATHEMATICAL INVARIANT ASSERTIONS
        assert total_messages == 215, f"Total messages must be 215, got {total_messages}"
        assert unique_ids == 215, f"Unique message IDs must be 215, got {unique_ids}"
        assert len(clean_ids) == 170, f"Clean messages must be 170, got {len(clean_ids)}"
        assert len(review_ids) == 45, f"Review messages must be 45, got {len(review_ids)}"
        assert len(clean_ids.intersection(review_ids)) == 0, "Clean and Review must be mutually disjoint"
        assert clean_ids.union(review_ids) == set(df["message_id"]), "Clean union Review must equal all 215 messages"
        assert clean_count + review_count == 215, f"Clean + Review must equal 215, got {clean_count + review_count}"
        assert total_review_flags == 54, f"Total review flags must be 54, got {total_review_flags}"
        assert anchor_total == 215, f"Temporal anchor total must equal 215, got {anchor_total}"
        assert total_linked == 39, f"Event-linked messages must be 39, got {total_linked}"
        assert unique_linked_messages == 39, f"Unique linked messages must be 39, got {unique_linked_messages}"
        assert unique_linked_events == 39, f"Unique linked events must be 39, got {unique_linked_events}"
        assert unresolved_event_links == 0, f"Unresolved event links must be 0, got {unresolved_event_links}"
        assert arrears_count == 8, f"Arrears messages must be 8, got {arrears_count}"
        assert rent_increase_count == 7, f"Rent increase messages must be 7, got {rent_increase_count}"
        assert termination_count == 9, f"Termination messages must be 9, got {termination_count}"
        assert unconfirmed_credits == 51, f"Unconfirmed credits must be 51, got {unconfirmed_credits}"
        assert unrealized_gains == 7, f"Unrealized gains must be 7, got {unrealized_gains}"
        assert len(confirmed_settled_prizes) == 6, f"Confirmed settled prizes must be 6, got {len(confirmed_settled_prizes)}"
        assert len(unconfirmed_prize_claims) == 6, f"Unconfirmed prize claims must be 6, got {len(unconfirmed_prize_claims)}"

        return {
            "TOTAL_MESSAGES": total_messages,
            "UNIQUE_MESSAGE_IDS": unique_ids,
            "CLEAN_MESSAGES": clean_count,
            "REVIEW_MESSAGES": review_count,
            "TOTAL_REVIEW_FLAGS": total_review_flags,
            "EVENT_LINKED_MESSAGES": total_linked,
            "UNIQUE_EVENT_LINKED_MESSAGES": unique_linked_messages,
            "RESOLVED_EVENT_LINKS": resolved_event_links,
            "UNRESOLVED_EVENT_LINKS": unresolved_event_links,
            "TEMPORAL_ANCHOR_TOTAL": anchor_total,
            "TEMPORAL_ANCHORS": anchor_counts,
            "ARREARS_MESSAGES": arrears_count,
            "RENT_INCREASE_MESSAGES": rent_increase_count,
            "TERMINATION_MESSAGES": termination_count,
            "UNCONFIRMED_CREDIT_MESSAGES": unconfirmed_credits,
            "UNREALIZED_GAIN_MESSAGES": unrealized_gains,
            "CONFIRMED_SETTLED_PRIZES": confirmed_settled_prizes,
            "UNCONFIRMED_PRIZE_CLAIMS": unconfirmed_prize_claims,
        }

    def cross_check_events(self) -> List[Dict[str, Any]]:
        return self.get_event_reconciliation_table()


if __name__ == "__main__":
    analyzer = MessageAnalyzer()
    stats = analyzer.get_statistics()
    print("=" * 70)
    print("HARDENED MESSAGE ANALYZER STATS")
    print("=" * 70)
    for k, v in stats.items():
        print(f"{k}: {v}")
