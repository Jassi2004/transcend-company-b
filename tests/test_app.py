import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(
        str(tmp_path / "test.sqlite"), demo_faults=True, sandbox_token="connector-test"
    )
    with TestClient(app) as c:
        yield c


def ticket():
    return {
        "subject": "Service is unavailable",
        "description": "Customers cannot sign in right now.",
        "requester": "Demo Customer",
        "email": "demo@example.com",
        "severity": "critical",
    }


def test_critical_routing_and_idempotency(client):
    first = client.post("/api/tickets", json=ticket(), headers={"Idempotency-Key": "request-one"})
    assert first.status_code == 201
    assert first.json()["priority"] == "critical"
    retry = client.post("/api/tickets", json=ticket(), headers={"Idempotency-Key": "request-one"})
    assert retry.status_code == 200
    assert first.json()["id"] == retry.json()["id"]
    conflict = client.post(
        "/api/tickets",
        json={**ticket(), "severity": "low"},
        headers={"Idempotency-Key": "request-one"},
    )
    assert conflict.status_code == 409


def test_resolving_is_idempotent(client):
    created = client.post(
        "/api/tickets", json=ticket(), headers={"Idempotency-Key": "request-two"}
    ).json()
    for _ in range(2):
        assert client.post(f"/api/tickets/{created['id']}/resolve", json={}).status_code == 200
    overview = client.get("/api/overview").json()
    assert overview["metrics"]["resolved"] == 1
    assert len([e for e in overview["events"] if e["type"] == "ticket.resolved"]) == 1


def test_fault_is_authenticated_and_actual_behavior_recovers(client):
    assert client.post("/api/demo/fault", json={"kind": "priority-inversion"}).status_code == 401
    headers = {"Authorization": "Bearer connector-test"}
    assert (
        client.post(
            "/api/demo/fault", json={"kind": "priority-inversion"}, headers=headers
        ).status_code
        == 200
    )
    assert client.get("/api/health").status_code == 503
    assert client.get("/api/classify?severity=critical").json()["priority"] == "low"
    created = client.post(
        "/api/tickets", json=ticket(), headers={"Idempotency-Key": "request-three"}
    ).json()
    assert created["priority"] == "low"
    assert (
        client.post(
            "/api/demo/fault", json={"kind": "none", "expectedFault": "other"}, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/demo/fault",
            json={"kind": "none", "expectedFault": "priority-inversion"},
            headers=headers,
        ).status_code
        == 200
    )
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/classify?severity=critical").json()["priority"] == "critical"
    assert client.get("/api/overview").json()["tickets"][0]["priority"] == "critical"


def test_invalid_input_and_unknown_fields_rejected(client):
    for changes in [
        {"severity": "admin"},
        {"subject": "X"},
        {"email": "invalid"},
        {"token": "must-not-be-stored"},
    ]:
        assert (
            client.post(
                "/api/tickets",
                json={**ticket(), **changes},
                headers={"Idempotency-Key": "request-invalid"},
            ).status_code
            == 422
        )


def test_cross_origin_mutations_rejected(client):
    assert (
        client.post(
            "/api/tickets",
            json=ticket(),
            headers={"Idempotency-Key": "request-bad-origin", "Origin": "http://["},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/tickets",
            json=ticket(),
            headers={"Idempotency-Key": "request-four", "Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )


def test_default_controls_disabled(tmp_path):
    with TestClient(create_app(str(tmp_path / "disabled.sqlite"), demo_faults=False)) as client:
        assert (
            client.post("/api/demo/fault", json={"kind": "priority-inversion"}).status_code == 404
        )
        assert client.get("/").status_code == 200


def test_persistence_across_app_restarts(tmp_path):
    path = str(tmp_path / "persistent.sqlite")
    with TestClient(create_app(path)) as client:
        client.post("/api/tickets", json=ticket(), headers={"Idempotency-Key": "request-persist"})
    with TestClient(create_app(path)) as client:
        assert len(client.get("/api/overview").json()["tickets"]) == 1


def test_same_kind_replacement_fault_rejects_stale_recovery(client):
    headers = {"Authorization": "Bearer connector-test"}
    first = client.post(
        "/api/demo/fault", json={"kind": "priority-inversion"}, headers=headers
    ).json()
    second = client.post(
        "/api/demo/fault", json={"kind": "priority-inversion"}, headers=headers
    ).json()
    assert first["faultId"] != second["faultId"]
    stale = client.post(
        "/api/demo/fault",
        json={
            "kind": "none",
            "expectedFault": "priority-inversion",
            "expectedFaultId": first["faultId"],
        },
        headers=headers,
    )
    assert stale.status_code == 409
    assert client.get("/api/health").json()["faultId"] == second["faultId"]
    fresh = client.post(
        "/api/demo/fault",
        json={
            "kind": "none",
            "expectedFault": "priority-inversion",
            "expectedFaultId": second["faultId"],
        },
        headers=headers,
    )
    assert fresh.status_code == 200
    assert client.get("/api/health").json()["fault"] == "none"
