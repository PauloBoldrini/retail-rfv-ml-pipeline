import json
import logging
import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import functions_framework
import joblib
import numpy as np
import pandas as pd
from google.cloud import bigquery, storage
from sklearn.base import BaseEstimator, TransformerMixin

logging.basicConfig(level=logging.INFO)

PROJECT_ID = os.environ["PROJECT_ID"]
BQ_LOCATION = os.getenv("BQ_LOCATION", "us-east4")
RFV_DATASET = os.environ["RFV_DATASET"]
RFV_BUCKET = os.environ["RFV_BUCKET"]
CUSTOMER_MASTER_TABLE = os.environ["CUSTOMER_MASTER_TABLE"]
SALES_TABLE = os.environ["SALES_TABLE"]
RFV_BASE_TABLE = os.environ["RFV_BASE_TABLE"]
RFV_TRANSITIONS_TABLE = os.environ["RFV_TRANSITIONS_TABLE"]
RFV_RUNS_TABLE = os.environ["RFV_RUNS_TABLE"]
RFV_MODEL_PREFIX = os.getenv("RFV_MODEL_PREFIX", "models/RFV_2.0")
RFV_MODEL_VERSION = os.getenv("RFV_MODEL_VERSION", "RFV_2.0")
HIGH_VALUE_CHURN_THRESHOLD = float(os.environ["HIGH_VALUE_CHURN_THRESHOLD"])
CHURN_DAYS = int(os.getenv("CHURN_DAYS", "180"))
NEW_CUSTOMER_DAYS = int(os.getenv("NEW_CUSTOMER_DAYS", "30"))
MIN_PURCHASE_DAYS_FOR_MODEL = int(os.getenv("MIN_PURCHASE_DAYS_FOR_MODEL", "3"))
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")

bq = bigquery.Client(project=PROJECT_ID)
gcs = storage.Client(project=PROJECT_ID)


class WinsorizerP99(BaseEstimator, TransformerMixin):
    """Compatibility transformer for a serialized preprocessing pipeline."""

    def __init__(self, upper_bounds=None):
        self.upper_bounds = upper_bounds

    def fit(self, X, y=None):
        X = np.asarray(X, dtype=float)
        if self.upper_bounds is None:
            self.upper_bounds = np.nanpercentile(X, 99, axis=0)
        return self

    def transform(self, X):
        X = np.asarray(X, dtype=float).copy()
        return np.minimum(X, np.asarray(self.upper_bounds))


def _today_local() -> date:
    return datetime.now(LOCAL_TZ).date()


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def query_features(data_ref: date) -> pd.DataFrame:
    sql = f"""
    WITH customers AS (
      SELECT
        CAST(customer_id AS INT64) AS customer_id,
        CAST(customer_document AS STRING) AS customer_document
      FROM `{CUSTOMER_MASTER_TABLE}`
      WHERE customer_id IS NOT NULL
        AND registration_date <= @data_ref
    ),

    history_raw AS (
      SELECT
        s.customer_id,
        s.sale_date,
        CAST(s.amount AS FLOAT64) AS amount,
        s.document_key,
        s.store_receipt_id,
        s.receipt_id,
        s.store_id,
        s.generated_at
      FROM `{SALES_TABLE}` s
      INNER JOIN customers c ON c.customer_id = s.customer_id
      WHERE s.sale_date IS NOT NULL
        AND s.sale_date <= @data_ref
        AND COALESCE(s.amount, 0) > 0
    ),

    history_dates AS (
      SELECT
        customer_id,
        MIN(sale_date) AS first_purchase_date,
        MAX(sale_date) AS last_purchase_date
      FROM history_raw
      GROUP BY customer_id
    ),

    history_with_id AS (
      SELECT
        *,
        CASE
          WHEN NULLIF(TRIM(document_key), '') IS NOT NULL
            THEN CONCAT('DOC|', TRIM(document_key))
          WHEN NULLIF(TRIM(store_receipt_id), '') IS NOT NULL
            THEN CONCAT('RECEIPT|', CAST(sale_date AS STRING), '|', TRIM(store_receipt_id))
          ELSE CONCAT(
            'FALLBACK|', CAST(sale_date AS STRING), '|',
            COALESCE(CAST(store_id AS STRING), ''), '|',
            COALESCE(receipt_id, '')
          )
        END AS historical_document_id
      FROM history_raw
    ),

    history_dedup AS (
      SELECT * EXCEPT(rn)
      FROM (
        SELECT
          *,
          ROW_NUMBER() OVER (
            PARTITION BY customer_id, historical_document_id
            ORDER BY generated_at DESC
          ) AS rn
        FROM history_with_id
      )
      WHERE rn = 1
    ),

    history_value AS (
      SELECT
        customer_id,
        SUM(amount) AS historical_value
      FROM history_dedup
      GROUP BY customer_id
    ),

    sales_12m_filtered AS (
      SELECT
        s.customer_id,
        s.sale_date,
        CAST(s.amount AS FLOAT64) AS amount,
        s.generated_at,
        COALESCE(
          NULLIF(TRIM(s.document_key), ''),
          NULLIF(TRIM(s.store_receipt_id), ''),
          CONCAT(
            'FALLBACK|', CAST(s.customer_id AS STRING), '|',
            CAST(s.sale_date AS STRING), '|',
            COALESCE(CAST(s.store_id AS STRING), ''), '|',
            COALESCE(s.receipt_id, '')
          )
        ) AS document_id
      FROM `{SALES_TABLE}` s
      INNER JOIN customers c ON c.customer_id = s.customer_id
      WHERE s.sale_date BETWEEN DATE_SUB(@data_ref, INTERVAL 12 MONTH) AND @data_ref
        AND COALESCE(s.amount, 0) > 0
    ),

    sales_12m_dedup AS (
      SELECT * EXCEPT(rn)
      FROM (
        SELECT
          *,
          ROW_NUMBER() OVER (
            PARTITION BY document_id
            ORDER BY generated_at DESC
          ) AS rn
        FROM sales_12m_filtered
      )
      WHERE rn = 1
    ),

    purchase_days AS (
      SELECT
        customer_id,
        sale_date,
        SUM(amount) AS day_value
      FROM sales_12m_dedup
      GROUP BY customer_id, sale_date
    ),

    intervals AS (
      SELECT
        customer_id,
        sale_date,
        day_value,
        DATE_DIFF(
          sale_date,
          LAG(sale_date) OVER (PARTITION BY customer_id ORDER BY sale_date),
          DAY
        ) AS interval_days
      FROM purchase_days
    ),

    features_12m AS (
      SELECT
        customer_id,
        COUNT(*) AS purchase_day_frequency,
        SUM(day_value) AS monetary_value,
        SAFE_DIVIDE(SUM(day_value), COUNT(*)) AS average_ticket,
        AVG(interval_days) AS mean_days_between_purchases,
        STDDEV_SAMP(interval_days) AS stddev_days_between_purchases
      FROM intervals
      GROUP BY customer_id
    )

    SELECT
      c.customer_id,
      c.customer_document,
      h.first_purchase_date,
      h.last_purchase_date,
      COALESCE(f.purchase_day_frequency, 0) AS purchase_day_frequency,
      CASE
        WHEN h.last_purchase_date IS NOT NULL
        THEN DATE_DIFF(@data_ref, h.last_purchase_date, DAY)
      END AS absolute_recency_days,
      f.monetary_value,
      f.average_ticket,
      f.mean_days_between_purchases,
      f.stddev_days_between_purchases,
      SAFE_DIVIDE(
        DATE_DIFF(@data_ref, h.last_purchase_date, DAY),
        f.mean_days_between_purchases
      ) AS relative_recency,
      hv.historical_value,
      CASE
        WHEN h.customer_id IS NULL THEN 'NO_PURCHASE'
        WHEN DATE_DIFF(@data_ref, h.last_purchase_date, DAY) > @churn_days
             AND hv.historical_value >= @high_value_threshold
          THEN 'HIGH_VALUE_CHURN'
        WHEN DATE_DIFF(@data_ref, h.last_purchase_date, DAY) > @churn_days
          THEN 'CHURN'
        WHEN DATE_DIFF(@data_ref, h.first_purchase_date, DAY) <= @new_customer_days
          THEN 'NEW'
        WHEN COALESCE(f.purchase_day_frequency, 0) < @min_purchase_days
          THEN 'INTEGRATION'
        ELSE 'MODEL_CANDIDATE'
      END AS lifecycle_status
    FROM customers c
    LEFT JOIN history_dates h USING (customer_id)
    LEFT JOIN history_value hv USING (customer_id)
    LEFT JOIN features_12m f USING (customer_id)
    """

    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("data_ref", "DATE", data_ref),
            bigquery.ScalarQueryParameter("churn_days", "INT64", CHURN_DAYS),
            bigquery.ScalarQueryParameter(
                "high_value_threshold", "FLOAT64", HIGH_VALUE_CHURN_THRESHOLD
            ),
            bigquery.ScalarQueryParameter("new_customer_days", "INT64", NEW_CUSTOMER_DAYS),
            bigquery.ScalarQueryParameter(
                "min_purchase_days", "INT64", MIN_PURCHASE_DAYS_FOR_MODEL
            ),
        ]
    )

    return (
        bq.query(sql, job_config=cfg, location=BQ_LOCATION)
        .result()
        .to_dataframe(create_bqstorage_client=False)
    )


def download_model_artifact(filename: str) -> str:
    blob = gcs.bucket(RFV_BUCKET).blob(f"{RFV_MODEL_PREFIX}/{filename}")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}")
    tmp.close()
    blob.download_to_filename(tmp.name)
    return tmp.name


def load_models():
    preprocessor = joblib.load(download_model_artifact("preprocessor.joblib"))
    pca = joblib.load(download_model_artifact("pca.joblib"))
    kmeans = joblib.load(download_model_artifact("kmeans_k4.joblib"))
    return preprocessor, pca, kmeans


CLUSTER_MAPPING = {
    0: "OPPORTUNISTS",
    1: "RECURRING",
    2: "DORMANT",
    3: "TOP_CUSTOMERS",
}

MODEL_FEATURES = [
    "purchase_day_frequency",
    "absolute_recency_days",
    "monetary_value",
    "average_ticket",
    "mean_days_between_purchases",
    "stddev_days_between_purchases",
    "relative_recency",
]


def score(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["segment"] = out["lifecycle_status"]
    out["cluster_id"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    out["classification_type"] = "RULE"

    mask = out["lifecycle_status"].eq("MODEL_CANDIDATE")
    if not mask.any():
        return out

    preprocessor, pca, kmeans = load_models()
    X = out.loc[mask, MODEL_FEATURES]
    Xt = preprocessor.transform(X)
    Xp = pca.transform(Xt)
    clusters = kmeans.predict(Xp)

    out.loc[mask, "cluster_id"] = pd.Series(
        clusters, index=out.index[mask], dtype="Int64"
    )
    out.loc[mask, "segment"] = [CLUSTER_MAPPING[int(c)] for c in clusters]
    out.loc[mask, "classification_type"] = "MODEL"
    return out


def insert_run_start(run_id: str, data_ref: date, started_at: datetime):
    sql = f"""
    INSERT INTO `{RFV_RUNS_TABLE}` (
      RUN_ID, REFERENCE_DATE, STARTED_AT, STATUS, MODEL_VERSION,
      HIGH_VALUE_CHURN_THRESHOLD
    )
    VALUES (
      @run_id, @reference_date, @started_at, 'RUNNING', @model_version,
      @high_value_threshold
    )
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter("reference_date", "DATE", data_ref),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", started_at),
            bigquery.ScalarQueryParameter("model_version", "STRING", RFV_MODEL_VERSION),
            bigquery.ScalarQueryParameter(
                "high_value_threshold", "FLOAT64", HIGH_VALUE_CHURN_THRESHOLD
            ),
        ]
    )
    bq.query(sql, job_config=cfg, location=BQ_LOCATION).result()


def update_run_success(
    run_id: str,
    finished_at: datetime,
    total_customers: int,
    model_candidates: int,
    counts: dict,
):
    sql = f"""
    UPDATE `{RFV_RUNS_TABLE}`
    SET
      FINISHED_AT = @finished_at,
      STATUS = 'SUCCESS',
      TOTAL_CUSTOMERS = @total_customers,
      MODEL_CANDIDATES = @model_candidates,
      QTY_NO_PURCHASE = @qty_no_purchase,
      QTY_HIGH_VALUE_CHURN = @qty_high_value_churn,
      HIGH_VALUE_CHURN_THRESHOLD = @high_value_threshold,
      QTY_CHURN = @qty_churn,
      QTY_NEW = @qty_new,
      QTY_INTEGRATION = @qty_integration,
      QTY_OPPORTUNISTS = @qty_opportunists,
      QTY_RECURRING = @qty_recurring,
      QTY_DORMANT = @qty_dormant,
      QTY_TOP_CUSTOMERS = @qty_top_customers,
      ERROR_MESSAGE = NULL
    WHERE RUN_ID = @run_id
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter("finished_at", "TIMESTAMP", finished_at),
            bigquery.ScalarQueryParameter("total_customers", "INT64", total_customers),
            bigquery.ScalarQueryParameter("model_candidates", "INT64", model_candidates),
            bigquery.ScalarQueryParameter("qty_no_purchase", "INT64", counts.get("NO_PURCHASE", 0)),
            bigquery.ScalarQueryParameter("qty_high_value_churn", "INT64", counts.get("HIGH_VALUE_CHURN", 0)),
            bigquery.ScalarQueryParameter("high_value_threshold", "FLOAT64", HIGH_VALUE_CHURN_THRESHOLD),
            bigquery.ScalarQueryParameter("qty_churn", "INT64", counts.get("CHURN", 0)),
            bigquery.ScalarQueryParameter("qty_new", "INT64", counts.get("NEW", 0)),
            bigquery.ScalarQueryParameter("qty_integration", "INT64", counts.get("INTEGRATION", 0)),
            bigquery.ScalarQueryParameter("qty_opportunists", "INT64", counts.get("OPPORTUNISTS", 0)),
            bigquery.ScalarQueryParameter("qty_recurring", "INT64", counts.get("RECURRING", 0)),
            bigquery.ScalarQueryParameter("qty_dormant", "INT64", counts.get("DORMANT", 0)),
            bigquery.ScalarQueryParameter("qty_top_customers", "INT64", counts.get("TOP_CUSTOMERS", 0)),
        ]
    )
    bq.query(sql, job_config=cfg, location=BQ_LOCATION).result()


def update_run_failure(run_id: str, finished_at: datetime, message: str):
    sql = f"""
    UPDATE `{RFV_RUNS_TABLE}`
    SET
      FINISHED_AT = @finished_at,
      STATUS = 'ERROR',
      ERROR_MESSAGE = @message
    WHERE RUN_ID = @run_id
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter("finished_at", "TIMESTAMP", finished_at),
            bigquery.ScalarQueryParameter("message", "STRING", message[:5000]),
        ]
    )
    bq.query(sql, job_config=cfg, location=BQ_LOCATION).result()


def publish_snapshot(df: pd.DataFrame, data_ref: date):
    publish = pd.DataFrame(
        {
            "CUSTOMER_ID": df["customer_id"].astype("int64"),
            "SEGMENT": df["segment"],
            "CLUSTER_ID": df["cluster_id"],
            "CLASSIFICATION_TYPE": df["classification_type"],
            "REFERENCE_DATE": data_ref,
            "PROCESSED_AT": datetime.now(timezone.utc),
            "MODEL_VERSION": RFV_MODEL_VERSION,
        }
    )

    staging = f"{PROJECT_ID}.{RFV_DATASET}._rfv_staging_{uuid.uuid4().hex[:8]}"
    bq.load_table_from_dataframe(publish, staging).result()

    # Only record a transition when the previous snapshot represents an
    # earlier reference date. This prevents same-date reruns from generating
    # false lifecycle transitions.
    sql = f"""
    BEGIN TRANSACTION;

      INSERT INTO `{RFV_TRANSITIONS_TABLE}` (
        CUSTOMER_ID,
        SEGMENT_FROM,
        SEGMENT_TO,
        CLUSTER_ID_FROM,
        CLUSTER_ID_TO,
        REFERENCE_DATE,
        PROCESSED_AT,
        MODEL_VERSION
      )
      SELECT
        old.CUSTOMER_ID,
        old.SEGMENT,
        new.SEGMENT,
        old.CLUSTER_ID,
        new.CLUSTER_ID,
        new.REFERENCE_DATE,
        new.PROCESSED_AT,
        new.MODEL_VERSION
      FROM `{RFV_BASE_TABLE}` old
      INNER JOIN `{staging}` new USING (CUSTOMER_ID)
      WHERE old.REFERENCE_DATE < new.REFERENCE_DATE
        AND (
          old.SEGMENT IS DISTINCT FROM new.SEGMENT
          OR old.CLUSTER_ID IS DISTINCT FROM new.CLUSTER_ID
        );

      DELETE FROM `{RFV_BASE_TABLE}` WHERE TRUE;

      INSERT INTO `{RFV_BASE_TABLE}`
      SELECT * FROM `{staging}`;

      DROP TABLE `{staging}`;

    COMMIT TRANSACTION;
    """
    bq.query(sql, location=BQ_LOCATION).result()

def write_run_manifest(run_id: str, data_ref: date, counts: dict, status: str):
    manifest = {
        "run_id": run_id,
        "reference_date": str(data_ref),
        "status": status,
        "model_version": RFV_MODEL_VERSION,
        "high_value_churn_threshold": HIGH_VALUE_CHURN_THRESHOLD,
        "counts": counts,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    path = (
        f"runs/reference_date={data_ref:%Y%m%d}/"
        f"run={run_id}/audit/rfv_run_manifest.json"
    )
    gcs.bucket(RFV_BUCKET).blob(path).upload_from_string(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        content_type="application/json",
    )


@functions_framework.http
def rfv_pipeline(request):
    payload = request.get_json(silent=True) or {}
    data_ref = (
        date.fromisoformat(payload["reference_date"])
        if payload.get("reference_date")
        else _today_local()
    )

    run_id = _run_id()
    started_at = datetime.now(timezone.utc)
    logging.info("Starting RFV run %s for %s", run_id, data_ref)
    insert_run_start(run_id, data_ref, started_at)

    try:
        df = query_features(data_ref)
        scored = score(df)
        publish_snapshot(scored, data_ref)

        counts = scored["segment"].value_counts(dropna=False).to_dict()
        model_candidates = int((scored["classification_type"] == "MODEL").sum())
        finished_at = datetime.now(timezone.utc)

        update_run_success(
            run_id=run_id,
            finished_at=finished_at,
            total_customers=int(len(scored)),
            model_candidates=model_candidates,
            counts=counts,
        )
        write_run_manifest(run_id, data_ref, counts, "SUCCESS")

        return (
            {
                "status": "SUCCESS",
                "run_id": run_id,
                "reference_date": str(data_ref),
                "model_version": RFV_MODEL_VERSION,
                "total_customers": int(len(scored)),
                "model_candidates": model_candidates,
                "segments": {str(k): int(v) for k, v in counts.items()},
            },
            200,
        )

    except Exception as exc:
        logging.exception("RFV run failed")
        finished_at = datetime.now(timezone.utc)
        try:
            update_run_failure(run_id, finished_at, str(exc))
        finally:
            write_run_manifest(run_id, data_ref, {}, "ERROR")
        return {"status": "ERROR", "run_id": run_id, "message": str(exc)}, 500
