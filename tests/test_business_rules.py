def classify_customer(
    has_purchase,
    recency_days,
    historical_value,
    first_purchase_age_days,
    purchase_day_frequency,
    high_value_threshold,
    churn_days=180,
    new_customer_days=30,
    min_purchase_days=3,
):
    if not has_purchase:
        return "NO_PURCHASE"
    if recency_days > churn_days and historical_value >= high_value_threshold:
        return "HIGH_VALUE_CHURN"
    if recency_days > churn_days:
        return "CHURN"
    if first_purchase_age_days <= new_customer_days:
        return "NEW"
    if purchase_day_frequency < min_purchase_days:
        return "INTEGRATION"
    return "MODEL_CANDIDATE"


def test_no_purchase_has_highest_priority():
    assert classify_customer(False, None, None, None, 0, 5000) == "NO_PURCHASE"


def test_high_value_churn():
    assert classify_customer(True, 250, 8000, 800, 20, 5000) == "HIGH_VALUE_CHURN"


def test_regular_churn():
    assert classify_customer(True, 250, 2000, 800, 20, 5000) == "CHURN"


def test_new_customer():
    assert classify_customer(True, 5, 200, 10, 1, 5000) == "NEW"


def test_integration_customer():
    assert classify_customer(True, 50, 3000, 200, 2, 5000) == "INTEGRATION"


def test_model_candidate():
    assert classify_customer(True, 50, 3000, 200, 5, 5000) == "MODEL_CANDIDATE"
