# Operations

## Daily execution

When the scheduler sends an empty JSON body, the function uses the current local date as reference.

```json
{}
```

For controlled backfills:

```json
{"reference_date":"YYYY-MM-DD"}
```

## Scheduler

Recommended configuration:

```text
frequency: daily
time zone: America/Sao_Paulo
authentication: OIDC
HTTP method: POST
deadline: 1800 seconds
```

The exact execution time should be scheduled after upstream ingestion.

## Validation checklist

```text
total rows = distinct customer IDs
null customer IDs = 0
null segments = 0
model rows without cluster = 0
sum of final segments = total rows
sum of model clusters = model candidates
single reference date in snapshot
single model version in snapshot
```

## Reprocessing the same reference date

A rerun of the same date should replace the snapshot without producing false transition history. Persist transitions only when the previous reference date is earlier than the new one.

## Run audit

Every run should persist run ID, reference date, timestamps, status, model version, total customers, model candidates, counts by segment, high-value threshold, and error message when applicable.

## Immutable run manifest

Recommended path:

```text
runs/reference_date=YYYYMMDD/run=<run_id>/audit/rfv_run_manifest.json
```

Keep failed-run manifests as part of the operational history.

## Security

Use a managed runtime service account. Never deploy production JSON keys inside the source tree.
