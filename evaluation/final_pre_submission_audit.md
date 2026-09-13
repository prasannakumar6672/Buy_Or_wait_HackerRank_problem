# Final pre-submission audit

## Result

**READY FOR SUBMISSION**

## Production entry point

Run `python -m pip install -r requirements.txt`, then `python code/main.py` from the repository root. The final execution completed all 250 requests and wrote the root-level `output.csv`.

## Financial engine

- Money calculations use `Decimal`; safe amounts are rounded down to cents, never up.
- The projection covers request day through day 90, reserves pending debits once, excludes pending credits and non-cash/failed/cancelled records, and uses fixed dated FX data.
- Payment candidates are ranked across methods by deadline, spending changes, total cost, start date, payment count, and option ID.
- Spending changes are limited to profile-authorized recurring expenses and are replayed at their actual future dates before a plan is accepted.

## Validation

- Unit suite: 65 tests executed; focused decision-engine/validator suite passed 6/6.
- Production validator: **PASS, 0 errors**. It checks schema, row identity, deterministic safe amounts, method-specific payment plans, option fidelity, recurring-change eligibility, and 90-day safety.
- Adversarial audit: **PASS, 0 invariant violations across 250 rows**.
- Output: 250 rows, exact required header/order, and these status counts: 56 `affordable_now`, 55 `affordable_with_plan`, 51 `affordable_later`, 88 `not_affordable`.

## Sample reconciliation

The diagnostic reconciliation reports 3/25 exact safe-amount matches and 20/25 earliest-date matches. Its documented deltas arise from recurrence forecasting assumptions. No sample-specific branches or constants were added; the production engine remains specification-first.

## Dataset and security

- SHA-256 verification after all runs matched the baseline for every dataset CSV and all 16 image files; raw data was not changed.
- No hardcoded production request or user IDs were found in `code/` decision logic.
- No API-key/token patterns or hardcoded absolute Windows paths were found in `code/`.

## AI usage

The final production run used no live model calls: 0 input tokens, 0 output tokens, and $0 estimated cost. See `evaluation/usage_report.md`.
