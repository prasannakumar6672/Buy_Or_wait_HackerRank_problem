"""
evaluation/generate_forensic_data.py

Computes all 22 required forensic columns for all 25 sample requests:
1. request_id
2. user_id
3. request_date
4. requested_amount
5. reference_safe_amount
6. calculated_safe_amount
7. safe_amount_difference
8. reference_earliest_date
9. calculated_earliest_date
10. earliest_date_match
11. opening_available_balance
12. minimum_balance_to_keep
13. pending_debit_reservation
14. minimum_projected_balance
15. minimum_headroom
16. minimum_balance_date
17. future_credit_total
18. future_debit_total
19. recurring_event_count
20. explicit_event_count
21. message_adjustment_count
22. fx_conversion_count
"""

import sys
from pathlib import Path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from decimal import Decimal
import json
from code.data_fusion import DataFusionLoader
from code.cashflow_engine import CashflowEngine
from code.event_normalizer import EventNormalizer

def generate_data():
    loader = DataFusionLoader()

    rows = []
    for i in range(1, 26):
        req_id = f"request_{i:02d}"
        ctx = loader.get_request_context(req_id)
        ref = loader.get_sample_reference(req_id)

        engine = CashflowEngine(ctx)
        sim_res = engine.simulate()

        # Pending debits
        norm = EventNormalizer(ctx)
        pend_debits = norm.get_pending_debits_to_reserve()
        pend_tot = sum(p.amount for p in pend_debits)

        # Event counts
        res_ids = {e.event_id for e in pend_debits}
        timeline = norm.build_normalized_event_timeline(res_ids)
        rec_count = sum(1 for e in timeline if e.is_synthetic)
        exp_count = sum(1 for e in timeline if not e.is_synthetic)

        # Future totals
        fut_credits = sum(e.amount for e in timeline if e.direction == "credit")
        fut_debits = sum(e.amount for e in timeline if e.direction == "debit")

        # FX count
        fx_count = sum(1 for e in ctx.user_events if e.fx_rate != Decimal("1"))

        # Message adjustment count
        msg_count = len(ctx.user_message_facts)

        ref_safe = Decimal(str(ref["amount_safe_to_pay"]))
        calc_safe = sim_res.safe_amount
        diff_safe = calc_safe - ref_safe

        ref_early = ref["earliest_date_for_full_payment"]
        calc_early = sim_res.earliest_date_for_full_payment
        early_match = (calc_early == ref_early)

        row = {
            "request_id": req_id,
            "user_id": ctx.user_id,
            "request_date": ctx.request_date,
            "requested_amount": float(ctx.requested_amount),
            "reference_safe_amount": float(ref_safe),
            "calculated_safe_amount": float(calc_safe),
            "safe_amount_difference": float(diff_safe),
            "reference_earliest_date": ref_early,
            "calculated_earliest_date": calc_early,
            "earliest_date_match": early_match,
            "opening_available_balance": float(ctx.profile.current_available_balance),
            "minimum_balance_to_keep": float(ctx.profile.minimum_balance_to_keep),
            "pending_debit_reservation": float(pend_tot),
            "minimum_projected_balance": float(sim_res.min_projected_balance),
            "minimum_headroom": float(sim_res.min_headroom),
            "minimum_balance_date": sim_res.min_projected_balance_date,
            "future_credit_total": float(fut_credits),
            "future_debit_total": float(fut_debits),
            "recurring_event_count": rec_count,
            "explicit_event_count": exp_count,
            "message_adjustment_count": msg_count,
            "fx_conversion_count": fx_count,
        }
        rows.append(row)

    return rows

if __name__ == "__main__":
    data = generate_data()
    print(json.dumps(data, indent=2))
