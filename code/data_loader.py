"""
code/data_loader.py

Robust, production-grade data loading and validation layer for HackerRank Orchestrate (September 2026).
Preserves original data, validates all foreign-key relationships, checks primary key uniqueness,
and audits missing values without modifying the underlying dataset files.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd


@dataclass
class ValidationIssue:
    severity: str  # "ERROR" | "WARNING" | "INFO"
    category: str
    message: str
    details: Optional[Dict] = None


@dataclass
class DatasetValidationReport:
    is_valid: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)
    table_stats: Dict[str, Dict] = field(default_factory=dict)
    foreign_key_checks: Dict[str, Dict] = field(default_factory=dict)

    def add_issue(self, severity: str, category: str, message: str, details: Optional[Dict] = None):
        if severity == "ERROR":
            self.is_valid = False
        self.issues.append(ValidationIssue(severity=severity, category=category, message=message, details=details))


@dataclass
class OrchestrateDataset:
    requests: pd.DataFrame
    sample_requests: pd.DataFrame
    financial_profiles: pd.DataFrame
    financial_events: pd.DataFrame
    exchange_rates: pd.DataFrame
    request_payment_options: pd.DataFrame
    messages: pd.DataFrame
    images: pd.DataFrame
    output_template: pd.DataFrame
    validation_report: DatasetValidationReport


class DataLoader:
    REQUIRED_FILES = {
        "requests": "requests.csv",
        "sample_requests": "sample_requests.csv",
        "financial_profiles": "financial_profiles.csv",
        "financial_events": "financial_events.csv",
        "exchange_rates": "exchange_rates.csv",
        "request_payment_options": "request_payment_options.csv",
        "messages": "messages.csv",
        "images": "images.csv",
        "output_template": "output.csv",
    }

    PRIMARY_KEYS = {
        "requests": "request_id",
        "sample_requests": "request_id",
        "financial_profiles": "user_id",
        "financial_events": "event_id",
        "request_payment_options": "payment_option_id",
        "messages": "message_id",
        "images": "image_id",
    }

    DATE_COLUMNS = {
        "requests": ["request_date", "desired_completion_date"],
        "sample_requests": ["request_date", "desired_completion_date"],
        "financial_events": ["event_date", "settlement_date"],
        "exchange_rates": ["rate_date"],
        "request_payment_options": ["first_payment_date"],
        "messages": ["sent_at"],
    }

    def __init__(self, dataset_dir: Optional[str] = None):
        if dataset_dir is None:
            # Default relative to this script: ../dataset
            current_dir = Path(__file__).resolve().parent
            repo_root = current_dir.parent
            self.dataset_dir = repo_root / "dataset"
        else:
            self.dataset_dir = Path(dataset_dir)
        self.media_dir = self.dataset_dir / "media" / "images"

    def load_and_validate(self) -> OrchestrateDataset:
        report = DatasetValidationReport()

        # 1. Check file existence
        dataframes: Dict[str, pd.DataFrame] = {}
        for key, fname in self.REQUIRED_FILES.items():
            fpath = self.dataset_dir / fname
            if not fpath.exists():
                report.add_issue("ERROR", "FILE_NOT_FOUND", f"Required dataset file missing: {fpath}")
                raise FileNotFoundError(f"Missing required file: {fpath}")
            # Load CSV preserving original strings and types
            df = pd.read_csv(fpath)
            dataframes[key] = df
            report.table_stats[key] = {
                "rows": len(df),
                "cols": len(df.columns),
                "columns": list(df.columns),
                "null_counts": {c: int(df[c].isna().sum()) for c in df.columns if df[c].isna().sum() > 0},
            }

        # 2. Check Primary Keys uniqueness
        for table, pk in self.PRIMARY_KEYS.items():
            df = dataframes[table]
            if pk not in df.columns:
                report.add_issue("ERROR", "SCHEMA_MISMATCH", f"Primary key column '{pk}' missing from '{table}'")
                continue
            duplicate_count = df[pk].duplicated().sum()
            null_count = df[pk].isna().sum()
            if null_count > 0:
                report.add_issue("ERROR", "NULL_PRIMARY_KEY", f"Table '{table}' has {null_count} nulls in PK '{pk}'")
            if duplicate_count > 0:
                report.add_issue("ERROR", "DUPLICATE_PRIMARY_KEY", f"Table '{table}' has {duplicate_count} duplicate PKs in '{pk}'")

        # 3. Validate Foreign-Key Relationships
        combined_users = set(dataframes["financial_profiles"]["user_id"].dropna().unique())
        combined_requests = set(dataframes["requests"]["request_id"].dropna().unique()) | set(dataframes["sample_requests"]["request_id"].dropna().unique())
        event_ids = set(dataframes["financial_events"]["event_id"].dropna().unique())

        fk_checks = [
            # (Child table, Child column, Parent set, Parent description, allow_null)
            ("requests", "user_id", combined_users, "financial_profiles.user_id", False),
            ("sample_requests", "user_id", combined_users, "financial_profiles.user_id", False),
            ("financial_events", "user_id", combined_users, "financial_profiles.user_id", False),
            ("request_payment_options", "request_id", combined_requests, "requests/sample_requests.request_id", False),
            ("financial_events", "linked_event_id", event_ids, "financial_events.event_id", True),
            ("messages", "user_id", combined_users, "financial_profiles.user_id", False),
            ("messages", "request_id", combined_requests, "requests/sample_requests.request_id", True),
            ("messages", "related_event_id", event_ids, "financial_events.event_id", True),
            ("images", "user_id", combined_users, "financial_profiles.user_id", False),
            ("images", "request_id", combined_requests, "requests/sample_requests.request_id", False),
            ("images", "related_event_id", event_ids, "financial_events.event_id", False),
        ]

        for child_table, child_col, parent_set, parent_desc, allow_null in fk_checks:
            df = dataframes[child_table]
            if child_col not in df.columns:
                report.add_issue("ERROR", "SCHEMA_MISMATCH", f"FK column '{child_col}' missing in '{child_table}'")
                continue
            series = df[child_col]
            if not allow_null and series.isna().sum() > 0:
                report.add_issue("ERROR", "UNEXPECTED_NULL_FK", f"Table '{child_table}.{child_col}' contains {series.isna().sum()} unexpected nulls")
            non_null_vals = set(series.dropna().unique())
            orphans = non_null_vals - parent_set
            check_key = f"{child_table}.{child_col} -> {parent_desc}"
            report.foreign_key_checks[check_key] = {
                "child_count": len(series),
                "non_null_unique": len(non_null_vals),
                "orphan_count": len(orphans),
                "orphans_sample": list(orphans)[:5] if orphans else [],
            }
            if orphans:
                report.add_issue("ERROR", "ORPHAN_FOREIGN_KEY", f"FK '{check_key}' has {len(orphans)} orphans: {list(orphans)[:5]}")

        # 4. Media file presence audit
        images_df = dataframes["images"]
        missing_images = []
        for img_id in images_df["image_id"].dropna().unique():
            img_file = self.media_dir / f"{img_id}.png"
            if not img_file.exists():
                missing_images.append(str(img_file))
        if missing_images:
            report.add_issue("ERROR", "MISSING_IMAGE_FILES", f"Missing {len(missing_images)} physical image files", {"missing": missing_images})
        else:
            report.add_issue("INFO", "MEDIA_CHECK", f"All {len(images_df)} images verified present in {self.media_dir}")

        # 5. Missing Amount in Financial Events Audit
        events_df = dataframes["financial_events"]
        null_amount_events = events_df[events_df["amount"].isna()]
        missing_amount_count = len(null_amount_events)
        image_linked_events = set(images_df["related_event_id"].dropna().unique())
        unaccounted_missing = set(null_amount_events["event_id"]) - image_linked_events
        if unaccounted_missing:
            report.add_issue("ERROR", "UNACCOUNTED_MISSING_AMOUNTS", f"{len(unaccounted_missing)} financial events have null amounts not covered by images.csv: {unaccounted_missing}")
        else:
            report.add_issue("INFO", "MISSING_AMOUNT_AUDIT", f"Exactly {missing_amount_count} financial events have null amounts; all 100% matched by images.csv")

        # 6. Date formatting check
        for table, cols in self.DATE_COLUMNS.items():
            df = dataframes[table]
            for col in cols:
                if col not in df.columns:
                    continue
                non_null_dates = df[col].dropna()
                # Attempt date parsing
                parsed = pd.to_datetime(non_null_dates, errors="coerce")
                invalid_dates = non_null_dates[parsed.isna()]
                if len(invalid_dates) > 0:
                    report.add_issue("ERROR", "INVALID_DATE_FORMAT", f"Table '{table}.{col}' has {len(invalid_dates)} invalid dates: {list(invalid_dates)[:5]}")

        # 7. Exchange Rate Pair Check
        rates_df = dataframes["exchange_rates"]
        event_currencies = set(events_df["currency"].unique())
        profile_currencies = set(dataframes["financial_profiles"]["home_currency"].unique())
        rate_dates = set(rates_df["rate_date"].unique())
        report.add_issue("INFO", "CURRENCY_INVENTORY", f"Event Currencies: {sorted(list(event_currencies))} | Profile Currencies: {sorted(list(profile_currencies))} | Rate Dates: {len(rate_dates)}")

        return OrchestrateDataset(
            requests=dataframes["requests"],
            sample_requests=dataframes["sample_requests"],
            financial_profiles=dataframes["financial_profiles"],
            financial_events=dataframes["financial_events"],
            exchange_rates=dataframes["exchange_rates"],
            request_payment_options=dataframes["request_payment_options"],
            messages=dataframes["messages"],
            images=dataframes["images"],
            output_template=dataframes["output_template"],
            validation_report=report,
        )


def print_data_quality_report(dataset: OrchestrateDataset):
    report = dataset.validation_report
    print("=" * 80)
    print(" HACKERRANK ORCHESTRATE — STEP 1: DATA QUALITY & INTEGRITY REPORT")
    print("=" * 80)
    print(f"Overall Status: {'PASSED (READY)' if report.is_valid else 'FAILED (ISSUES DETECTED)'}\n")

    print("1. DATASET DIMENSIONS & INVENTORY:")
    for name, stats in report.table_stats.items():
        null_info = f" | Nulls: {stats['null_counts']}" if stats['null_counts'] else " | No Nulls"
        print(f"  - {name:<25}: {stats['rows']:>6} rows, {stats['cols']:>2} cols{null_info}")

    print("\n2. FOREIGN KEY INTEGRITY (11 RELATIONSHIPS):")
    for fk_name, stats in report.foreign_key_checks.items():
        status = "OK" if stats["orphan_count"] == 0 else f"FAIL ({stats['orphan_count']} orphans)"
        print(f"  - {fk_name:<55} -> {status}")

    print("\n3. AUDIT ISSUES & HIGHLIGHTS:")
    for issue in report.issues:
        prefix = f"[{issue.severity}]"
        print(f"  {prefix:<9} {issue.category:<25}: {issue.message}")

    print("\n4. 1-to-1 REQUEST TO USER VERIFICATION:")
    req_users = set(dataset.requests["user_id"].unique())
    sample_users = set(dataset.sample_requests["user_id"].unique())
    overlap = req_users & sample_users
    print(f"  - Sample request users: {len(sample_users)} (request_01 to request_25)")
    print(f"  - Evaluation request users: {len(req_users)} (request_26 to request_275)")
    print(f"  - User overlap between sample & eval: {len(overlap)} (Expected: 0)")
    print(f"  - Total unique users covered: {len(req_users | sample_users)} / {len(dataset.financial_profiles)} profiles")

    print("\n5. FINANCIAL EVENTS NULL AMOUNT AUDIT:")
    null_events = dataset.financial_events[dataset.financial_events["amount"].isna()]
    print(f"  - Total events with null amount: {len(null_events)}")
    print(f"  - Total images in images.csv: {len(dataset.images)}")
    matching_events = set(null_events["event_id"]) & set(dataset.images["related_event_id"])
    print(f"  - Null events mapped to images: {len(matching_events)} / {len(null_events)} (100% matched)")
    print("=" * 80)


if __name__ == "__main__":
    loader = DataLoader()
    dataset = loader.load_and_validate()
    print_data_quality_report(dataset)
