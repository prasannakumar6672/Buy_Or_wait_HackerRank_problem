"""
evaluation/sample_diagnostic_gate.py

Public Sample Gate Diagnostic Runner (Constraint 16).
Runs all 25 sample requests through the deterministic cashflow simulation engine,
and compares calculated safe amount and earliest full-payment date against reference ground truth.
Produces a machine-verifiable, detailed per-request diagnostic audit.
"""

from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, Dict, List

# Ensure repo root is on path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from code.data_fusion import DataFusionLoader
from code.cashflow_engine import CashflowEngine


def run_sample_gate() -> List[Dict[str, Any]]:
    loader = DataFusionLoader()
    diagnostic_rows = []

    for i in range(1, 26):
        req_id = f"request_{i:02d}"
        ctx = loader.get_request_context(req_id)
        ref = loader.get_sample_reference(req_id)

        engine = CashflowEngine(ctx)
        trace = engine.simulate()

        ref_safe = Decimal(str(ref["amount_safe_to_pay"]))
        ref_earliest = str(ref["earliest_date_for_full_payment"]) if ref["earliest_date_for_full_payment"] is not None else "None"
        calc_earliest_str = str(trace.earliest_date_for_full_payment) if trace.earliest_date_for_full_payment is not None else "None"

        safe_match = (trace.safe_amount == ref_safe)
        earliest_match = (calc_earliest_str == ref_earliest)

        # Count events
        total_events = len(ctx.user_events)
        included_count = sum(len(pt.events) for pt in trace.timeline)
        excluded_count = total_events - len([e for e in ctx.user_events if e.status in ("settled", "scheduled")])

        # Pending reservations
        pending_reserved = float(trace.pending_debits_reserved)

        # FX conversions count
        fx_conversions = len([e for e in ctx.user_events if e.currency != ctx.profile.home_currency])

        # Message adjustments
        msg_adjustments = [
            f"{m.message_id}:{m.classification}:{m.cashflow_impact}"
            for m in ctx.user_message_facts if m.cashflow_impact != "none"
        ]

        row = {
            "request_id": req_id,
            "user_id": ctx.user_id,
            "request_date": ctx.request_date,
            "requested_amount": float(ctx.requested_amount),
            "initial_balance": float(trace.initial_available_balance),
            "minimum_balance_to_keep": float(trace.minimum_balance_to_keep),
            "pending_debits_reserved": pending_reserved,
            "fx_conversions_count": fx_conversions,
            "message_adjustments": ";".join(msg_adjustments) if msg_adjustments else "none",
            "min_projected_balance": float(trace.min_projected_balance),
            "min_projected_balance_date": trace.min_projected_balance_date,
            "min_headroom": float(trace.min_headroom),
            "calc_safe_amount": float(trace.safe_amount),
            "ref_safe_amount": float(ref_safe),
            "safe_amount_diff": float(trace.safe_amount - ref_safe),
            "safe_match": safe_match,
            "calc_earliest_date": calc_earliest_str,
            "ref_earliest_date": ref_earliest,
            "earliest_match": earliest_match,
            "ref_affordability_status": ref["affordability_status"],
            "ref_payment_method": ref["recommended_payment_method"],
            "ref_payment_plan": ref["payment_plan"],
            "ref_spending_changes": ref["spending_changes_needed"],
        }
        diagnostic_rows.append(row)

    return diagnostic_rows


def print_full_diagnostics():
    results = run_sample_gate()
    print("=" * 140)
    print(f"{'Req ID':<11} | {'User':<8} | {'Calc Safe':<12} | {'Ref Safe':<12} | {'Safe Match':<10} | {'Calc Earliest':<14} | {'Ref Earliest':<14} | {'Earliest Match':<14}")
    print("-" * 140)
    for r in results:
        print(
            f"{r['request_id']:<11} | {r['user_id']:<8} | {r['calc_safe_amount']:<12.2f} | {r['ref_safe_amount']:<12.2f} | "
            f"{str(r['safe_match']):<10} | {r['calc_earliest_date']:<14} | {r['ref_earliest_date']:<14} | {str(r['earliest_match']):<14}"
        )
    print("=" * 140)
    safe_matches = sum(1 for r in results if r["safe_match"])
    earliest_matches = sum(1 for r in results if r["earliest_match"])
    print(f"Summary: Safe Amount Matches = {safe_matches}/25 | Earliest Date Matches = {earliest_matches}/25\n")

    print("\n" + "=" * 100)
    print("DETAILED PER-REQUEST DIAGNOSTICS (Constraint 16)")
    print("=" * 100)
    for r in results:
        print(f"\n--- {r['request_id']} ({r['user_id']}) ---")
        print(f"  Request Date: {r['request_date']} | Requested Amount: {r['requested_amount']:,.2f}")
        print(f"  Initial Avail Balance: {r['initial_balance']:,.2f} | Min Keep: {r['minimum_balance_to_keep']:,.2f}")
        print(f"  Pending Debits Reserved: {r['pending_debits_reserved']:,.2f} | FX Conversions Count: {r['fx_conversions_count']}")
        print(f"  Message Adjustments: {r['message_adjustments']}")
        print(f"  Min Projected Balance: {r['min_projected_balance']:,.2f} on {r['min_projected_balance_date']}")
        print(f"  Min Headroom: {r['min_headroom']:,.2f}")
        print(f"  Calc Safe Amount: {r['calc_safe_amount']:,.2f} | Ref Safe Amount: {r['ref_safe_amount']:,.2f} (Diff: {r['safe_amount_diff']:+,.2f})")
        print(f"  Calc Earliest Date: {r['calc_earliest_date']} | Ref Earliest Date: {r['ref_earliest_date']}")
        print(f"  Ref Status: {r['ref_affordability_status']} | Ref Method: {r['ref_payment_method']}")
        print(f"  Ref Payment Plan: {r['ref_payment_plan']}")
        print(f"  Ref Spending Changes: {r['ref_spending_changes']}")


if __name__ == "__main__":
    print_full_diagnostics()
