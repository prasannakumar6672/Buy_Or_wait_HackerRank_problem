"""
evaluation/reference_reconciliation.py

Comprehensive reference reconciliation test for HackerRank Orchestrate (Step 4 Gate).
Compares deterministic simulation output against ground-truth sample requests.
Reports exact discrepancies, root-cause classifications, and contributing event IDs.
Fails loudly if any safe amount or earliest date mismatch remains.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from code.data_loader import DataLoader
from code.data_fusion import DataFusionLoader
from code.event_normalizer import EventNormalizer
from code.cashflow_engine import CashflowEngine


def run_reference_reconciliation():
    fusion = DataFusionLoader(REPO_ROOT / "dataset")
    samples_df = pd.read_csv(REPO_ROOT / "dataset" / "sample_requests.csv")
    
    print("=" * 140)
    print(" HACKERRANK ORCHESTRATE — STEP 4 REFERENCE RECONCILIATION GATE")
    print("=" * 140)
    header = f"{'request_id':<12} | {'ref_safe':>12} | {'calc_safe':>12} | {'safe_diff':>12} | {'ref_early':<10} | {'calc_early':<10} | {'root_cause':<25} | {'responsible_events'}"
    print(header)
    print("-" * 140)
    
    safe_matches = 0
    earliest_matches = 0
    records = []
    
    known_event_contributions = {
        'request_01': ('exact_match', 'none'),
        'request_02': ('variable_living_forecast', 'event_137, event_138, event_139, event_140, event_142, event_159, event_182'),
        'request_03': ('discretionary_savings_cap', 'event_188, event_235, event_237, event_252'),
        'request_04': ('discretionary_savings_cap', 'event_357, recurring_groceries_transport'),
        'request_05': ('minimum_buffer_allowance', 'event_424, event_431, event_432'),
        'request_06': ('pre_spending_change_baseline', 'event_476'),
        'request_07': ('variable_living_forecast', 'event_595, event_601, event_602'),
        'request_08': ('discretionary_savings_cap', 'event_681, event_692, event_642'),
        'request_09': ('exact_match', 'none'),
        'request_10': ('minimum_buffer_allowance', 'event_828, event_838, gig_platform_payouts'),
        'request_11': ('base_salary_dom_selection', 'event_904, event_905, event_940, event_989'),
        'request_12': ('seasonal_contract_boundary', 'event_1012, event_1013, message_09'),
        'request_13': ('discretionary_savings_cap', 'event_1054, event_1055, event_1160'),
        'request_14': ('discretionary_savings_cap', 'event_1223, event_1224, event_1225'),
        'request_15': ('minimum_buffer_allowance', 'event_1371, event_1372'),
        'request_16': ('exact_match', 'none'),
        'request_17': ('variable_living_forecast', 'event_1501, event_1502, event_1546'),
        'request_18': ('discretionary_savings_cap', 'event_1573, event_1574, event_1575, event_1576'),
        'request_19': ('discretionary_savings_cap', 'event_1680, event_1681, event_1693'),
        'request_20': ('minimum_buffer_allowance', 'event_1773, event_1785, event_1786, event_1787'),
        'request_21': ('variable_living_forecast', 'event_1814, event_1815, event_1816, event_1817, event_1834, event_1844, event_1853, event_1857'),
        'request_22': ('variable_living_forecast', 'event_1889, event_1890, event_1891, event_1892, event_1961'),
        'request_23': ('discretionary_savings_cap', 'event_1967, event_1975, event_2042'),
        'request_24': ('discretionary_savings_cap', 'event_2113, event_2166'),
        'request_25': ('minimum_buffer_allowance', 'event_2190, event_2272, event_2287'),
    }
    
    for idx, s in samples_df.iterrows():
        req_id = str(s['request_id']).strip()
        ref_safe = Decimal(str(s['amount_safe_to_pay']))
        ref_early = str(s['earliest_date_for_full_payment']).strip() if pd.notna(s['earliest_date_for_full_payment']) else 'None'
        
        ctx = fusion.get_request_context(req_id)
        engine = CashflowEngine(ctx)
        trace = engine.simulate()
        
        calc_safe = trace.safe_amount
        calc_early = str(trace.earliest_date_for_full_payment) if trace.earliest_date_for_full_payment else 'None'
        
        safe_diff = calc_safe - ref_safe
        is_safe_match = (calc_safe == ref_safe)
        is_early_match = (calc_early == ref_early)
        
        if is_safe_match:
            safe_matches += 1
        if is_early_match:
            earliest_matches += 1
            
        root_cause, resp_events = known_event_contributions.get(req_id, ('recurrence', 'unknown'))
        if is_safe_match and is_early_match:
            root_cause = 'exact_match'
            resp_events = 'none'
            
        row_str = f"{req_id:<12} | {ref_safe:>12.2f} | {calc_safe:>12.2f} | {safe_diff:>12.2f} | {ref_early:<10} | {calc_early:<10} | {root_cause:<25} | {resp_events}"
        print(row_str)
        
    print('-' * 140)
    print(f"Summary Results: Safe Amount: {safe_matches}/25 exact matches | Earliest Date: {earliest_matches}/25 exact matches")
    print('=' * 140)
    
    if safe_matches < 25 or earliest_matches < 25:
        print(f"\nGATE STATUS: STEP 4 FAIL — unresolved discrepancies remain ({25 - safe_matches} safe mismatches, {25 - earliest_matches} earliest date mismatches).")
        return False
    else:
        print("\nGATE STATUS: STEP 4 PASS — 25/25 public samples exact.")
        return True


if __name__ == '__main__':
    success = run_reference_reconciliation()
    if not success:
        sys.exit(1)
    sys.exit(0)
