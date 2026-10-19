#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Missing PROJECT_ID}"
: "${BQ_LOCATION:?Missing BQ_LOCATION}"
: "${RFV_FUNCTION_URL:?Missing RFV_FUNCTION_URL}"
: "${RFV_SERVICE_ACCOUNT:?Missing RFV_SERVICE_ACCOUNT}"

JOB="${RFV_SCHEDULER_JOB:-scheduler-rfv-pipeline}"
SCHEDULE="${RFV_SCHEDULE:-30 10 * * *}"
TIME_ZONE="${RFV_TIME_ZONE:-America/Sao_Paulo}"

gcloud scheduler jobs create http "${JOB}" \
  --project="${PROJECT_ID}" \
  --location="${BQ_LOCATION}" \
  --description="Daily RFV segmentation pipeline" \
  --schedule="${SCHEDULE}" \
  --time-zone="${TIME_ZONE}" \
  --uri="${RFV_FUNCTION_URL}" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{}' \
  --oidc-service-account-email="${RFV_SERVICE_ACCOUNT}" \
  --oidc-token-audience="${RFV_FUNCTION_URL}" \
  --attempt-deadline=1800s
