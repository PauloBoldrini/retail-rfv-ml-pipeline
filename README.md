# Retail RFV ML Pipeline

Production-oriented RFV segmentation pipeline built for a retail client.

The solution combines deterministic lifecycle rules with an unsupervised machine-learning model and publishes a daily customer segmentation snapshot for downstream CRM activation.

> This repository is intentionally sanitized for portfolio/public Git usage. It does not contain client names, production identifiers, credentials, raw data, model binaries, exact commercial thresholds, or customer-specific volumes.

## What this project demonstrates

- Full-customer RFV coverage from a retail customer master
- Historical churn classification
- High-value churn specialization
- Rolling 12-month behavioral features
- Deterministic business-rule segmentation
- PCA + MiniBatchKMeans clustering
- BigQuery-based feature engineering
- Cloud Function orchestration
- Cloud Scheduler daily execution
- Execution and transition auditing
- Per-run manifests in object storage
- CRM / Customer Data Platform ready output

## Segmentation design

The pipeline classifies customers in the following priority order:

1. `NO_PURCHASE`
2. `HIGH_VALUE_CHURN`
3. `CHURN`
4. `NEW`
5. `INTEGRATION`
6. Model-eligible customers:
   - `OPPORTUNISTS`
   - `RECURRING`
   - `DORMANT`
   - `TOP_CUSTOMERS`

Only model-eligible customers are sent to the ML pipeline.

## Time windows

Two time perspectives are deliberately separated.

### Full history

Used for first purchase, last purchase, churn recency, historical accumulated value, `NO_PURCHASE`, and `HIGH_VALUE_CHURN`.

### Rolling 12 months

Used for purchase-day frequency, monetary value, average ticket, mean interval between purchases, interval standard deviation, and relative recency.

The 12-month window is **not** used as a population gate.

## High-value churn

`HIGH_VALUE_CHURN` is a specialization of churn:

```text
last purchase is older than the churn threshold
AND
historical accumulated value >= configured high-value threshold
```

The threshold should be calibrated from a baseline population (for example, the P90 of historical value among churn customers), then frozen per model version for governance and reproducibility.

## ML pipeline

```text
SimpleImputer(median)
        ↓
WinsorizerP99
        ↓
StandardScaler
        ↓
PCA
        ↓
MiniBatchKMeans
```

## Architecture

```text
Cloud Scheduler
      ↓
Cloud Function
      ↓
Customer Master + Sales History
      ↓
Business Rules
      ↓
Eligible Population
      ↓
Preprocessor → PCA → K-Means
      ↓
Final RFV Snapshot
      ↓
Audit Tables + Run Manifest
      ↓
CRM / Customer Data Platform
```

## Repository structure

```text
.
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── requirements.txt
├── src/
│   └── main.py
├── sql/
│   ├── 001_create_tables.sql
│   ├── 002_qa_checks.sql
│   └── 003_high_value_churn_baseline.sql
├── scripts/
│   ├── deploy_function.sh
│   └── create_scheduler.sh
├── docs/
│   ├── architecture.md
│   ├── business_rules.md
│   └── operations.md
└── tests/
    └── test_business_rules.py
```

## Security

Do not commit service-account JSON keys, `.env`, model binaries, raw extracts, customer identifiers, production project IDs, private bucket names, private dataset/table names, or exact client thresholds/volumes.

## Disclaimer

This repository is a sanitized technical case study based on work performed for a retail client. All identifying information and client-sensitive values have been removed or parameterized.
