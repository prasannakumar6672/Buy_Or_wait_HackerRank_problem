# HackerRank Orchestrate — Buy or Wait?

This project implements an AI-assisted financial affordability agent that determines whether a requested expense can be safely paid while maintaining the user's required financial safety buffer over the specified future horizon.

---

## 1. Problem Overview

For every purchase or payment request, the agent evaluates the user's complete financial picture:
* Current available balance
* Minimum required balance (`minimum_balance_to_keep`)
* Future confirmed income (salary and settled earnings)
* Future essential expenses
* Pending transactions (reserving pending debits; withholding unconfirmed credits)
* Recurring financial obligations
* Available payment options (full payment, installments, and partial payments)
* User-authorized spending adjustments on flexible categories

Using this information, the system recommends an optimal, personalized payment strategy: pay in full now, pay with an installment or partial plan, wait for safe cashflow, or decline the purchase.

---

## 2. Architecture

The solution uses a layered pipeline separating evidence interpretation from deterministic calculation:

```text
Input Dataset
      ↓
Data Loading / Fusion
      ↓
Unstructured Evidence Extraction
      ↓
Event Normalization
      ↓
Cashflow Projection
      ↓
Deterministic Decision Engine
      ↓
Validation
      ↓
output.csv
```

### Core Separation Principle
> **AI is used for interpreting unstructured evidence such as messages and images, while deterministic code performs monetary arithmetic, cashflow projection, constraint checking, and final affordability decisions.**

---

## 3. AI Usage

AI capabilities are focused strictly on extracting structured financial intelligence from unstructured media:
* Extracting financial facts, transaction updates, and settlement adjustments from informal messages.
* Interpreting unstructured financial evidence such as bank notices, cancellation messages, and salary rescheduling.
* Extracting missing financial amounts from payroll statements, utility bills, and receipts in `images.csv`.

The extracted facts are normalized into typed, verifiable data structures before being passed into the decision pipeline.

> **The AI layer does not perform final financial calculations or make the final affordability decision.**

---

## 4. Deterministic Financial Engine

All financial calculations and affordability decisions are executed by deterministic code:
* **Exact Monetary Arithmetic:** Implemented entirely with Python's `decimal.Decimal` to avoid floating-point inaccuracies.
* **Future Cashflow Projection:** Daily simulation of available balances across the 90-day evaluation horizon.
* **Minimum-Balance Protection:** Ensuring the user's balance never falls below `minimum_balance_to_keep`.
* **Payment-Plan Validation:** Feasibility checks on each installment date and partial payment milestone.
* **Date-Aware Financial Events:** Precise scheduling of recurring and one-time cashflows.
* **Exchange Rate Normalization:** Dated, fixed exchange-rate lookups applied deterministically.
* **Pending Transaction Handling:** Conservative reservation of pending debits; exclusion of unsettled credits.
* **Recurring Obligations:** Conservative forward forecasting of essential recurring commitments.

---

## 5. 90-Day Safety Principle

> **A recommended payment strategy must keep the projected balance at or above the user's `minimum_balance_to_keep` throughout the relevant 90-day evaluation horizon while satisfying applicable financial obligations.**

The engine evaluates complete future cashflow rather than looking only at today's balance. Even if a user currently holds sufficient funds, a purchase is deferred or rejected if upcoming essential expenses or debt obligations would breach the safety buffer.

---

## 6. Financial Event Handling

The event normalization engine classifies and handles all transaction states conservatively:
* **Confirmed Income:** Counted strictly on its confirmed settlement date.
* **Recurring Expenses:** Forecasted conservatively forward across the projection window.
* **Pending Debits:** Immediately reserved against available headroom.
* **Pending Credits:** Excluded from cashflow until officially settled.
* **Transaction Settlement:** Life-cycle state transitions tracked via event linkage.
* **Cancellations & Failed Transactions:** Excluded from future cashflow projections.
* **Recurring Modifications & Salary Changes:** Reflected upon explicit confirmation in supporting evidence.
* **Unconfirmed / Non-Cash Assets:** Unrealized investments, lottery claims, bonuses, and commission estimates are not treated as available cash.

---

## 7. Currency Handling

* Financial events and requests may occur in multiple currencies (INR, USD, EUR, IDR, ZAR).
* Exchange rates are sourced directly from `dataset/exchange_rates.csv` for the transaction settlement date and conversion direction.
* All conversions are executed deterministically using `Decimal` arithmetic rounded to 2 decimal places.
* No external live exchange-rate or market-data APIs are used.

---

## 8. Decision Outcomes

The decision engine outputs four standardized affordability statuses:
1. `affordable_now`: The purchase can be paid in full immediately on `request_date` without breaching the safety buffer.
2. `affordable_with_plan`: The purchase can be completed via an installment schedule, a 2-part payment schedule, or permitted flexible spending adjustments.
3. `affordable_later`: The purchase cannot be paid today, but a single full payment is forecast safe on a future date before the deadline.
4. `not_affordable`: The purchase cannot be safely completed within the forecast period without violating the user's minimum balance.

### Recommended Payment Methods:
* `full_payment`: One single payment on `request_date` or with permitted spending reductions.
* `partial_payment`: Exactly two payments adding to the requested amount (headroom today, remainder on earliest safe date).
* `installments`: Matching an available seller payment option within the user's allowed installment tenure.
* `wait`: Deferring payment until `earliest_date_for_full_payment`.
* `not_recommended`: Expense rejected due to insufficient cashflow headroom.

---

## 9. Spending Changes

Spending changes are evaluated only when authorized by the user's profile:
* **Protected Categories:** Core essentials (e.g., housing, utilities, groceries) are strictly protected and never modified.
* **Adjustable Categories:** Flexible expenses (e.g., entertainment, subscriptions, dining) may be reduced or stopped if explicitly permitted by the user's profile preferences.
* **Strict Limits:** Permitted actions are constrained to at most three `stop:<event_id>` or `reduce_to:<event_id>:<new_amount>` modifications targeting non-protected recurring expenses.

---

## 10. Validation & Verification

The pipeline has been thoroughly verified:
* **Unit Tests:** 65 automated tests covering all modules (`tests/`).
* **Evaluation Requests:** Full production dataset of 250 evaluation requests processed.
* **Adversarial Audit:** 0 financial invariant violations across all 250 requests.
* **Output Schema Validation:** 100% compliance with column specifications, date ordering, and enum constraints.

---

## 11. Running the Project

### Prerequisites
* Python 3.10+ (tested on Python 3.13 and 3.14)
* Dependencies in `requirements.txt`:
  ```bash
  python -m pip install -r requirements.txt
  ```

### Production Execution
To evaluate all 250 requests and generate the final submission files:

```bash
python code/main.py
```

On Windows systems with the Python Launcher:
```powershell
py -3.13 code/main.py
```

### Execution Steps
1. Loads and fuses the challenge dataset from `dataset/`.
2. Evaluates all 250 production requests through the decision engine.
3. Writes final predictions to `output.csv`.
4. Runs strict compliance validation via `OutputValidator`.
5. Updates `evaluation/usage_report.md`.

---

## 12. Output Format

The final predictions are written to `output.csv` with these exact columns:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

* Exactly 250 rows corresponding to `request_26` through `request_275`.
* Grounded, auditable explanations for every financial recommendation.

---

## 13. Dependencies

* `pandas>=2.2,<4`
* `numpy>=2.0,<3`

No external network access or paid third-party libraries are required at runtime.

---

## 14. Security & Configuration

* Zero hardcoded API keys, tokens, or credentials exist in the codebase.
* If optional external AI extraction models are configured, credentials are read exclusively from environment variables.
* Machine-specific absolute paths are completely avoided; all paths are resolved dynamically relative to project root.

---

## 15. Project Design Philosophy

> **The design intentionally separates probabilistic interpretation from deterministic financial decision-making. This reduces the risk of hallucinated arithmetic and makes affordability decisions reproducible, testable, and auditable.**
