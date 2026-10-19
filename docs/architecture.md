# Architecture

## Objective

Provide a daily, reproducible customer segmentation snapshot for a retail client using deterministic lifecycle rules plus unsupervised ML.

## Components

### BigQuery

Sources: customer master and sales history.

Targets: final RFV snapshot, transition log, and execution log.

### Cloud Storage

Stores preprocessing, PCA and clustering artifacts plus a per-run audit manifest.

### Cloud Function

Responsibilities:

1. Resolve the reference date.
2. Build the customer population.
3. Calculate historical lifecycle metrics.
4. Calculate rolling 12-month RFV features.
5. Apply business rules.
6. Score only model-eligible customers.
7. Publish the final snapshot.
8. Write execution metadata and audit artifacts.

### Cloud Scheduler

Executes the pipeline daily after source ingestion is expected to be complete.

## Population principle

The customer master defines the operational population. Sales history provides behavior, but does not define population membership.

This prevents long-inactive customers and customers without purchases from disappearing merely because they fall outside the rolling model window.

## Historical backfills

For historical snapshots:

```text
registration_date <= reference_date
```

This prevents customers created after the requested historical date from appearing in a backfill.

## Concurrency

When the target snapshot is atomically replaced, keep the function at one maximum instance to avoid concurrent writers publishing conflicting snapshots.
