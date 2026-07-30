from uuid import UUID

from buywell_edge.supervisor import extension_request_id, job_idempotency_key


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


def test_input_resolver_idempotency_is_scoped_to_delivery_attempt() -> None:
    job = {
        "jobId": "job-1",
        "idempotencyKey": "execution:url",
        "jobKind": "input-resolver",
    }

    assert job_idempotency_key({**job, "deliveryAttempt": 1}) == "execution:url:delivery:1"
    assert job_idempotency_key({**job, "deliveryAttempt": 2}) == "execution:url:delivery:2"


def test_execution_outcome_idempotency_is_scoped_to_delivery_attempt() -> None:
    job = {
        "jobId": "job-1",
        "idempotencyKey": "execution-outcome:one",
        "jobKind": "execution-outcome",
    }

    assert job_idempotency_key({**job, "deliveryAttempt": 1}) == "execution-outcome:one:delivery:1"
    assert job_idempotency_key({**job, "deliveryAttempt": 2}) == "execution-outcome:one:delivery:2"


def test_legacy_job_falls_back_to_durable_job_id() -> None:
    assert job_idempotency_key({"jobId": "job-1"}) == "job-1"


def test_extension_request_preserves_contract_uuid() -> None:
    request_id = "438974d6-1282-422c-a998-4a3dda4e69f1"
    assert extension_request_id({"requestId": request_id}) == request_id


def test_extension_request_replaces_internal_non_uuid_identifier() -> None:
    assert str(UUID(extension_request_id({"requestId": "instance:1"})))
