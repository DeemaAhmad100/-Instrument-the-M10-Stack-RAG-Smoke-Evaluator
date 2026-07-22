"""Observability layer for the M10 backend.

This module is where you (the learner) declare the three Prometheus metric
families and implement the three ASGI middleware classes that the autograder
exercises through the FastAPI app.

What lives here, and why:

  - Three metric families. A counter for request volume by (path, status), a
    histogram for request latency by path, and a gauge for in-flight requests.
    Together they answer "how much traffic, how slow, how concurrent."

  - Three middlewares. A request-id layer that attaches a per-request
    correlation id to the response and to the logging context. A
    structured-logging layer that emits one JSON line per response. A metrics
    layer that increments the counter, observes the latency histogram, and
    brackets the request with the in-flight gauge.

  Ordering matters: request-id is outermost (so it wraps the logging line),
  logging is middle, metrics is innermost (closest to the route).

Where to put what:

  - Declarations at MODULE SCOPE. If you declare a Counter / Histogram / Gauge
    inside a function or inside a middleware __call__, you will hit
    `Duplicated timeseries in CollectorRegistry` on the second request --
    every request re-runs the function. Module scope means the registry sees
    the declaration once at import time.

  - Label cardinality matters. The Lab's `requests_total` Counter uses
    exactly two labels: {path, status}. Do NOT add user-id, query-text,
    full-URL, or any other unbounded label.

Methodology pointers:

  - Reading sections 6-10 cover middleware, metric types, label cardinality.
  - See Common Pitfalls #1-#4 in the lab guide.
"""

import json
import logging
import time
import uuid
from contextvars import ContextVar

from prometheus_client import Gauge, Histogram

from .safe_counter import SafeCounter
from .exemplars import ExemplarTracker

# ---------------------------------------------------------------------------
# Metric declarations -- module scope, so the registry only sees them once.
# ---------------------------------------------------------------------------

# Cardinality-safe counter for request tracking (budget: 100 unique path/status)
requests_total = SafeCounter(
    "requests_total",
    "Total HTTP requests (cardinality-bounded)",
    ["path", "status"],
    budget=100,
)

request_latency_seconds = Histogram(
    "request_latency_seconds",
    "Request latency in seconds",
    ["path"],
    # default Prometheus latency buckets (no override for the base Lab)
)

inflight_requests = Gauge(
    "inflight_requests",
    "Number of requests currently being processed",
)

# ---------------------------------------------------------------------------
# Tier 2 Challenge: Exemplar tracker for histogram observations
# ---------------------------------------------------------------------------
# Initialize with default Prometheus buckets for request_latency_seconds
DEFAULT_LATENCY_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]
exemplar_tracker = ExemplarTracker(
    histograms={"request_latency_seconds": request_latency_seconds},
    buckets=DEFAULT_LATENCY_BUCKETS,
)

# ---------------------------------------------------------------------------
# Shared context: lets StructuredLoggingMiddleware read the id that
# RequestIdMiddleware generated, without passing it through function args.
# ---------------------------------------------------------------------------

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

_logger = logging.getLogger("m11.api")


class RequestIdMiddleware:
    """Generates a per-request id and stamps it on the response header."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)


class StructuredLoggingMiddleware:
    """Emits one JSON log line per response with request_id/path/status/latency."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder = {"status": None}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        latency_ms = (time.perf_counter() - start) * 1000
        _logger.info(
            json.dumps(
                {
                    "request_id": request_id_var.get(),
                    "path": scope["path"],
                    "status": status_holder["status"],
                    "latency_ms": latency_ms,
                }
            )
        )


class MetricsMiddleware:
    """Increments requests_total, observes request_latency_seconds,
    brackets the request with inflight_requests, and records exemplars."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inflight_requests.inc()
        start = time.perf_counter()
        status_holder = {"status": None}
        response_size_holder = {"size": 0}
        headers_holder = {"headers": []}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers_holder["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                response_size_holder["size"] += len(body)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            inflight_requests.dec()
            elapsed = time.perf_counter() - start
            path = scope["path"]
            status = status_holder["status"]
            request_id = request_id_var.get()
            
            # Tier 3: SafeCounter may return None or NopCounter if budget exceeded
            counter_result = requests_total.labels(path=path, status=status)
            if counter_result is not None:
                counter_result.inc()
            
            request_latency_seconds.labels(path=path).observe(elapsed)
            
            # Tier 2: Record exemplar
            exemplar_tracker.record(
                metric_name="request_latency_seconds",
                path=path,
                status=status,
                value=elapsed,
                request_id=request_id,
                response_size_bytes=response_size_holder["size"],
            )


# ---------------------------------------------------------------------------
# Cardinality monitoring helpers (for Tier 3 challenge)
# ---------------------------------------------------------------------------

def get_cardinality_usage() -> dict:
    """Get cardinality usage stats for all metrics with cardinality bounds."""
    return {
        "requests_total": requests_total.get_cardinality_usage(),
    }


# ---------------------------------------------------------------------------
# Exemplar retrieval helpers (for Tier 2 challenge)
# ---------------------------------------------------------------------------

def get_exemplars() -> dict:
    """Get all recorded exemplars organized by metric, path, status."""
    return exemplar_tracker.get_exemplars()