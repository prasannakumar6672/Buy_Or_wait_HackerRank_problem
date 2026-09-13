"""
evaluation/run_adversarial_audit.py

Comprehensive Adversarial Audit Script for HackerRank Orchestrate.
Executes Checks 1 through 7 and outputs detailed audit verification results.
"""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import re
from datetime import datetime
from decimal import Decimal
import pandas as pd

from code.data_fusion import DataFusionLoader
from code.decision_engine import DecisionEngine, DecisionResult
from code.cashflow_engine import CashflowEngine
from code.validator import OutputValidator, REQUIRED_COLUMNS, ALLOWED_STATUSES, ALLOWED_METHODS

def run_adversarial_audit():
    repo_root = Path(__file__).resolve().parent.parent
    dataset_dir = repo_root / "dataset"
    output_path = repo_root / "output.csv"

    print("=" * 80)
    print("STARTING COMPREHENSIVE ADVERSARIAL AUDIT")
    print("=" * 80)

    # =========================================================================
    # CHECK 1: DECISION HIERARCHY VERIFICATION
    # =========================================================================
    print("\n--- CHECK 1: DECISION HIERARCHY ---")
    loader = DataFusionLoader(dataset_dir)
    print("Loaded dataset and fused profiles successfully.")

    # Inspect decision engine code rules
    decision_engine_file = repo_root / "code" / "decision_engine.py"
    with open(decision_engine_file, "r", encoding="utf-8") as f:
        de_code = f.read()

    hierarchy_checks = {
        "affordable_now_priority": "if safe_amount >= self.requested_amount:" in de_code,
        "partial_payment_tier": "self._evaluate_partial_payment" in de_code,
        "installment_tier": "self._find_best_installment_plan" in de_code,
        "deadline_wait_priority": "_build_wait_plan(trace, require_deadline=True)" in de_code,
        "spending_changes_tier": "self._evaluate_spending_changes" in de_code,
        "affordable_later_fallback": "self._explain_affordable_later" in de_code,
        "not_affordable_tier": "affordability_status=\"not_affordable\"" in de_code,
        "no_balance_below_min": "if projected_balance < self.profile.minimum_balance_to_keep:" in de_code,
        "max_installment_months": "opt.number_of_payments > self.profile.max_installment_months" in de_code,
        "cross_method_candidate_ranking": "candidates.sort(key=lambda candidate: candidate[0])" in de_code,
        "payment_option_cost_ranking": "x[\"option\"].payment_option_id" in de_code,
        "change_aware_replay": "_is_full_payment_safe_with_changes" in de_code,
    }

    all_hierarchy_passed = all(hierarchy_checks.values())
    print(f"Decision Hierarchy Code Checks: {'PASS' if all_hierarchy_passed else 'FAIL'}")
    for k, v in hierarchy_checks.items():
        print(f"  - {k}: {'PASS' if v else 'FAIL'}")

    # =========================================================================
    # CHECK 2: 250 OUTPUT AUDIT
    # =========================================================================
    print("\n--- CHECK 2: 250 OUTPUT AUDIT ---")
    validator = OutputValidator(dataset_dir, output_path)
    is_valid, errors = validator.validate()

    out_df = pd.read_csv(output_path, dtype=str, keep_default_na=False)
    reqs_df = pd.read_csv(dataset_dir / "requests.csv")

    row_count = len(out_df)
    unique_ids = len(out_df["request_id"].unique())
    expected_ids = len(reqs_df)
    cols_match = list(out_df.columns) == REQUIRED_COLUMNS

    print(f"  - Total rows: {row_count} (Expected: 250) -> {'PASS' if row_count == 250 else 'FAIL'}")
    print(f"  - Unique request IDs: {unique_ids} -> {'PASS' if unique_ids == 250 else 'FAIL'}")
    print(f"  - Column schema exact match: {'PASS' if cols_match else 'FAIL'}")
    print(f"  - Strict Validator Result: {'PASS (0 errors)' if is_valid else f'FAIL ({len(errors)} errors)'}")
    if errors:
        for err in errors[:5]:
            print(f"    * {err}")

    # =========================================================================
    # CHECK 3: FINANCIAL INVARIANTS PROGRAMMATIC VERIFICATION
    # =========================================================================
    print("\n--- CHECK 3: FINANCIAL INVARIANTS ACROSS ALL 250 EVALUATION REQUESTS ---")
    invariant_violations = []

    for idx, row in out_df.iterrows():
        req_id = row["request_id"]
        ctx = loader.get_request_context(req_id)
        user_id = ctx.profile.user_id
        safe_amt = Decimal(row["amount_safe_to_pay"])
        status = row["affordability_status"]
        method = row["recommended_payment_method"]
        plan = row["payment_plan"]
        spending = row["spending_changes_needed"]
        earliest_str = row["earliest_date_for_full_payment"]

        # 1. Bounds on safe amount
        if not (Decimal("0") <= safe_amt <= ctx.requested_amount):
            invariant_violations.append(f"{req_id}: safe_amount {safe_amt} outside [0, {ctx.requested_amount}]")

        # 2. Status / Method pairings
        if status == "affordable_now":
            if method != "full_payment":
                invariant_violations.append(f"{req_id}: affordable_now has method {method}")
            if safe_amt < ctx.requested_amount:
                invariant_violations.append(f"{req_id}: affordable_now has safe_amt {safe_amt} < requested {ctx.requested_amount}")
            if earliest_str != ctx.request_date:
                invariant_violations.append(f"{req_id}: affordable_now has earliest {earliest_str} != request_date {ctx.request_date}")

        elif status == "affordable_later":
            if method != "wait":
                invariant_violations.append(f"{req_id}: affordable_later has method {method}")
            if not earliest_str:
                invariant_violations.append(f"{req_id}: affordable_later has empty earliest date")
            if plan == "none":
                invariant_violations.append(f"{req_id}: affordable_later has payment_plan == 'none'")

        elif status == "not_affordable":
            if method != "not_recommended":
                invariant_violations.append(f"{req_id}: not_affordable has method {method}")
            if earliest_str != "":
                invariant_violations.append(f"{req_id}: not_affordable has non-empty earliest date '{earliest_str}'")
            if plan != "none":
                invariant_violations.append(f"{req_id}: not_affordable has plan '{plan}' != 'none'")

        elif status == "affordable_with_plan":
            if method == "partial_payment":
                # Must have 2 payments summing to requested_amount
                parts = plan.split("|")
                if len(parts) != 2:
                    invariant_violations.append(f"{req_id}: partial_payment has {len(parts)} parts, expected 2")
                else:
                    d1, a1 = parts[0].split(":")
                    d2, a2 = parts[1].split(":")
                    if d1 != ctx.request_date:
                        invariant_violations.append(f"{req_id}: partial payment 1 date {d1} != request_date")
                    if d2 != earliest_str:
                        invariant_violations.append(f"{req_id}: partial payment 2 date {d2} != earliest {earliest_str}")
                    total = Decimal(a1) + Decimal(a2)
                    if abs(total - ctx.requested_amount) > Decimal("0.01"):
                        invariant_violations.append(f"{req_id}: partial payment total {total} != requested {ctx.requested_amount}")
                    if Decimal(a1) != safe_amt:
                        invariant_violations.append(f"{req_id}: partial payment 1 amt {a1} != safe_amt {safe_amt}")

            elif method == "installments":
                # Must follow an available payment option
                matched_option = False
                parts = plan.split("|")
                num_payments = len(parts)
                sum_plan = sum(Decimal(p.split(":")[1]) for p in parts)
                for opt in ctx.payment_options:
                    if opt.payment_method == "installments" and opt.number_of_payments == num_payments:
                        if abs(sum_plan - opt.total_payable_amount) <= Decimal("0.05"):
                            matched_option = True
                            break
                if not matched_option:
                    invariant_violations.append(f"{req_id}: installment plan {plan} did not match any provider option total")

            elif method == "full_payment":
                # Must have spending changes
                if spending == "none":
                    invariant_violations.append(f"{req_id}: affordable_with_plan + full_payment must have spending changes")

        # 3. Spending changes verification
        if spending != "none":
            changes = spending.split("|")
            if len(changes) > 3:
                invariant_violations.append(f"{req_id}: more than 3 spending changes ({len(changes)})")
            stoppable = set(ctx.profile.expense_categories_user_is_willing_to_stop)
            reducible = set(ctx.profile.expense_categories_user_is_willing_to_reduce)
            protected = set(ctx.profile.expense_categories_to_protect)

            for c in changes:
                parts = c.split(":")
                action = parts[0]
                ev_id = parts[1]
                # Find event
                ev_match = [e for e in ctx.user_events if e.event_id == ev_id]
                if not ev_match:
                    invariant_violations.append(f"{req_id}: spending change references unknown event {ev_id}")
                else:
                    ev = ev_match[0]
                    if ev.category in protected:
                        invariant_violations.append(f"{req_id}: spending change modifies protected category {ev.category}")
                    if action == "stop" and ev.category not in stoppable:
                        invariant_violations.append(f"{req_id}: stop action on non-stoppable category {ev.category}")
                    if action == "reduce_to" and ev.category not in reducible:
                        invariant_violations.append(f"{req_id}: reduce action on non-reducible category {ev.category}")

    print(f"Total invariant violations across 250 requests: {len(invariant_violations)}")
    if invariant_violations:
        for v in invariant_violations[:10]:
            print(f"  * {v}")
    else:
        print("  ALL 250 REQUESTS SATISFY 100% OF FINANCIAL INVARIANTS!")

    # =========================================================================
    # CHECK 4: EXPLANATION GROUNDING AUDIT
    # =========================================================================
    print("\n--- CHECK 4: EXPLANATION GROUNDING AUDIT ---")
    combos = out_df.groupby(["affordability_status", "recommended_payment_method"]).size().reset_index(name="count")
    print("Decision status/method distribution:")
    for _, row in combos.iterrows():
        print(f"  - {row['affordability_status']} / {row['recommended_payment_method']}: {row['count']}")

    forbidden_patterns = [
        re.compile(r"\b5%\b"),
        re.compile(r"\b20%\b"),
        re.compile(r"\b22%\b"),
        re.compile(r"\boracle\b", re.IGNORECASE),
        re.compile(r"\bpublic sample\b", re.IGNORECASE),
        re.compile(r"\bbenchmark\b", re.IGNORECASE),
        re.compile(r"\bdebug\b", re.IGNORECASE),
        re.compile(r"\btrace\b", re.IGNORECASE),
    ]

    explanation_issues = []
    for idx, row in out_df.iterrows():
        expl = row["decision_explanation"]
        for pat in forbidden_patterns:
            if pat.search(expl):
                explanation_issues.append(f"{row['request_id']}: explanation matched forbidden pattern '{pat.pattern}': {expl}")

    print(f"Forbidden pattern matches in explanations: {len(explanation_issues)}")
    if explanation_issues:
        for iss in explanation_issues:
            print(f"  * {iss}")
    else:
        print("  All 250 explanations are clean, professional, and contain NO internal debug or oracle references!")

    # Sample check 1 from each status/method combo
    print("\nSample Explanations across categories:")
    for _, row_combo in combos.iterrows():
        st = row_combo["affordability_status"]
        met = row_combo["recommended_payment_method"]
        sample_row = out_df[(out_df["affordability_status"] == st) & (out_df["recommended_payment_method"] == met)].iloc[0]
        print(f"[{st} | {met}] ({sample_row['request_id']}):\n  \"{sample_row['decision_explanation']}\"\n")

    # =========================================================================
    # CHECK 5: REPRODUCIBILITY & PORTABILITY
    # =========================================================================
    print("--- CHECK 5: PORTABILITY & REPRODUCIBILITY ---")
    # Scan code/ directory for hardcoded absolute Windows paths
    code_dir = repo_root / "code"
    abs_path_pattern = re.compile(r"[a-zA-Z]:\\[a-zA-Z0-9_\\]+")
    hardcoded_paths = []
    for root, _, files in os.walk(code_dir):
        for f in files:
            if f.endswith(".py"):
                f_path = Path(root) / f
                content = f_path.read_text(encoding="utf-8")
                matches = abs_path_pattern.findall(content)
                if matches:
                    hardcoded_paths.append((f, matches))

    print(f"Hardcoded absolute Windows paths in code/: {len(hardcoded_paths)}")
    for f, m in hardcoded_paths:
        print(f"  * {f}: {m}")

    # =========================================================================
    # CHECK 7: GIT / SECURITY AUDIT
    # =========================================================================
    print("\n--- CHECK 7: GIT / SECURITY AUDIT ---")
    # Scan for API keys or secrets in code/
    secret_patterns = [
        re.compile(r"AIza[0-9A-Za-z-_]{35}"),  # Google API key
        re.compile(r"sk-[a-zA-Z0-9]{20,}"),     # OpenAI key
        re.compile(r"ghp_[a-zA-Z0-9]{30,}"),    # GitHub token
    ]
    leaked_secrets = []
    for root, _, files in os.walk(code_dir):
        for f in files:
            if f.endswith(".py"):
                f_path = Path(root) / f
                content = f_path.read_text(encoding="utf-8")
                for sp in secret_patterns:
                    if sp.search(content):
                        leaked_secrets.append((f, sp.pattern))

    print(f"Leaked API keys/secrets in code/: {len(leaked_secrets)}")
    for f, pat in leaked_secrets:
        print(f"  * {f}: pattern {pat}")

    # Hardcoded request/user ID checks in decision_engine.py
    hardcoded_ids = []
    for req_id_match in re.findall(r"request_\d+", de_code):
        hardcoded_ids.append(req_id_match)
    for user_id_match in re.findall(r"user_\d+", de_code):
        hardcoded_ids.append(user_id_match)

    print(f"Hardcoded request_id/user_id references in decision_engine.py: {len(hardcoded_ids)}")

    print("\n" + "=" * 80)
    print("AUDIT COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    run_adversarial_audit()
