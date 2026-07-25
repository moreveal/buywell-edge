from buywell_edge.supervisor import job_idempotency_key


def test_action_job_uses_declared_idempotency_key() -> None:
    assert job_idempotency_key({
        "jobId": "job-1",
        "requestId": "request-1",
        "idempotencyKey": "action-1",
    }) == "action-1"


def test_binding_catalog_job_uses_request_id() -> None:
    assert job_idempotency_key({
        "jobId": "job-1",
        "requestId": "catalog-request-1",
        "jobKind": "binding-catalog",
    }) == "catalog-request-1"


def test_legacy_job_falls_back_to_durable_job_id() -> None:
    assert job_idempotency_key({"jobId": "job-1"}) == "job-1"
