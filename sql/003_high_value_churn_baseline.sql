-- Example baseline query for calibrating a fixed HIGH_VALUE_CHURN threshold.
-- Keep this logic aligned with production document deduplication.

DECLARE REFERENCE_DATE DATE DEFAULT DATE 'YYYY-MM-DD';
DECLARE CHURN_DAYS INT64 DEFAULT 180;

WITH customers AS (
  SELECT customer_id
  FROM `${CUSTOMER_MASTER_TABLE}`
  WHERE customer_id IS NOT NULL
    AND registration_date <= REFERENCE_DATE
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
  FROM `${SALES_TABLE}` s
  INNER JOIN customers c USING (customer_id)
  WHERE s.sale_date IS NOT NULL
    AND s.sale_date <= REFERENCE_DATE
    AND COALESCE(s.amount, 0) > 0
),

with_document_id AS (
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
    END AS document_id
  FROM history_raw
),

dedup AS (
  SELECT * EXCEPT(rn)
  FROM (
    SELECT
      *,
      ROW_NUMBER() OVER (
        PARTITION BY customer_id, document_id
        ORDER BY generated_at DESC
      ) AS rn
    FROM with_document_id
  )
  WHERE rn = 1
),

customer_history AS (
  SELECT
    customer_id,
    MAX(sale_date) AS last_purchase_date,
    SUM(amount) AS historical_value
  FROM dedup
  GROUP BY customer_id
),

churn AS (
  SELECT *
  FROM customer_history
  WHERE DATE_DIFF(REFERENCE_DATE, last_purchase_date, DAY) > CHURN_DAYS
),

with_percentile AS (
  SELECT
    *,
    PERCENTILE_CONT(historical_value, 0.90) OVER () AS p90
  FROM churn
)

SELECT
  COUNT(*) AS total_churn,
  ROUND(ANY_VALUE(p90), 2) AS exact_p90,
  COUNTIF(historical_value >= p90) AS customers_above_p90,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(historical_value >= p90), COUNT(*)), 2) AS pct_customers,
  ROUND(SUM(historical_value), 2) AS total_churn_value,
  ROUND(SUM(IF(historical_value >= p90, historical_value, 0)), 2) AS high_value_churn_value,
  ROUND(
    100 * SAFE_DIVIDE(
      SUM(IF(historical_value >= p90, historical_value, 0)),
      SUM(historical_value)
    ),
    2
  ) AS pct_value_concentrated
FROM with_percentile;
