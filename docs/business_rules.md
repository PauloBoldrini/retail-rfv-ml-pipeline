# Business Rules

## Priority

```text
NO_PURCHASE
↓
HIGH_VALUE_CHURN
↓
CHURN
↓
NEW
↓
INTEGRATION
↓
MODEL_CANDIDATE
```

Only `MODEL_CANDIDATE` customers are sent to the ML model.

## NO_PURCHASE

Customer exists in the master but has no valid historical purchase up to the reference date.

## HIGH_VALUE_CHURN

Customer has purchase history, the last purchase is older than the configured churn horizon, and historical accumulated value is greater than or equal to a fixed high-value threshold.

Historical value is the sum of all valid deduplicated purchases up to the reference date. It is not the value of one transaction.

## CHURN

Customer has purchase history, the last purchase is older than the churn horizon, and the customer does not meet the high-value threshold.

## NEW

Customer is not churned and the first historical purchase occurred within the configured new-customer window.

## INTEGRATION

Customer is not churned, is not new, and has fewer than the minimum number of distinct purchase days in the rolling 12-month window.

## MODEL_CANDIDATE

Customer is not classified by any of the previous lifecycle rules.

## High-value threshold governance

Recommended process:

1. Choose a historical baseline date.
2. Build the churn population with production rules.
3. Calculate the exact P90 of historical accumulated value.
4. Evaluate value concentration.
5. Freeze the monetary threshold for the model version.

A fixed threshold improves interpretability, auditability, campaign stability, and model-version governance.
