"""YOUR tests for the observability layer.

Strategy: use FastAPI's TestClient (in-process ASGI calls, no live Docker
stack needed) to exercise the three middlewares end to end, and pytest's
`caplog` fixture to capture the structured-logging middleware's JSON line.
"""

import json

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.observability import requests_total


client = TestClient(app)


def test_request_id_header_present_and_nonempty():
    """After one request, X-Request-ID is set on the response and is
    at least 8 characters long (per the autograder's own check)."""
    response = client.get("/healthz")

    request_id = response.headers.get("x-request-id")

    assert request_id is not None
    assert len(request_id) >= 8


def test_requests_total_counter_increments():
    """After one request, the requests_total counter for that
    (path, status) label pair has incremented by exactly 1."""
    path = "/healthz"
    status = "200"

    before = requests_total.labels(path=path, status=status)._value.get()

    response = client.get("/healthz")
    assert response.status_code == 200

    after = requests_total.labels(path=path, status=status)._value.get()

    assert after == before + 1


def test_structured_log_request_id_matches_response_header(caplog):
    """The structured log line emitted during the request carries the
    same request_id that appears in the response's X-Request-ID header."""
    with caplog.at_level("INFO", logger="m11.api"):
        response = client.get("/healthz")

    header_request_id = response.headers.get("x-request-id")

    matching_records = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "m11.api"
    ]

    assert len(matching_records) >= 1

    log_line = matching_records[-1]
    assert log_line["request_id"] == header_request_id
    assert log_line["path"] == "/healthz"
    assert log_line["status"] == 200
    assert "latency_ms" in log_line