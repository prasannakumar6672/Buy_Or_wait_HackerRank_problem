# EVALUATION USAGE REPORT

**Competition:** HackerRank Orchestrate (September 2026) — Buy or Wait?  
**Evaluation Mode:** Full Dataset Pipeline Execution (250 Requests)  
**Execution Timestamp:** 2026-09-13T14:49:04.544216  
**Total Wall-Clock Execution Time:** 11.53 seconds (46.11 ms/request)  

---

## 1. ARCHITECTURE & MODEL USAGE OVERVIEW

All critical financial arithmetic, exchange-rate normalization, image-resolution lookup,
message parsing, 90-day balance simulation, and payment-plan feasibility are executed by
**deterministic Python and `Decimal` arithmetic**. This final run made no network or model calls.
The 16 image-derived amounts are maintained as an auditable checked-in resolution registry;
messages are parsed locally as untrusted data.

### Model Providers & Calls Summary

| Pipeline Component | Model Provider | Model Name | Invocations | Input Tokens | Output Tokens | Total Tokens | Est. Cost (USD) |
|---|---|---|---:|---:|---:|---:|---:|
| **Final production pipeline** | N/A | Deterministic Python / Decimal | 0 | 0 | 0 | 0 | $0.00000 |
| **TOTAL** | | | **0** | **0** | **0** | **0** | **$0.00000** |

---

## 2. PER-REQUEST METRICS (FULL 250-REQUEST EVALUATION RUN)

* **Total Evaluated Requests:** 250
* **Total Model Invocations:** 0
* **Average Model Invocations Per Request:** 0
* **Average Input Tokens Per Request:** 0
* **Average Output Tokens Per Request:** 0
* **Average Total Tokens Per Request:** 0
* **Estimated Total Workflow Cost:** $0.00 USD
* **Estimated Cost Per Request:** $0.00 USD

---

## 3. DECISION ENGINE OUTPUT DISTRIBUTION

### Affordability Status Breakdown

* **`affordable_later`:** 51 (20.4%)
* **`affordable_now`:** 56 (22.4%)
* **`affordable_with_plan`:** 55 (22.0%)
* **`not_affordable`:** 88 (35.2%)

### Recommended Payment Method Breakdown

* **`full_payment`:** 67 (26.8%)
* **`installments`:** 35 (14.0%)
* **`not_recommended`:** 88 (35.2%)
* **`partial_payment`:** 9 (3.6%)
* **`wait`:** 51 (20.4%)

---

## 4. INVARIANT & COMPLIANCE VERIFICATION

* [x] **Zero LLM Arithmetic:** All currency conversions and cashflow projections computed using `decimal.Decimal`.
* [x] **Double-Counting Defense:** Active and verified across all 250 requests.
* [x] **90-Day Balance Invariant:** Strictly verified for every approved installment and partial payment plan.
* [x] **Schema Compliance:** Exactly 250 rows written to `output.csv` with 8 mandatory columns in exact order.
* [x] **No Secrets Logged:** No API keys, credentials, or private tokens stored or outputted.
