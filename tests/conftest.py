"""Autograder conftest. Resolves imports from the repo root.

In the deployed template repo, `starter/` contents are at the repo root,
so `..` is where `api/` and `eval_rag_smoke.py` live.
"""
import os
import sys
import pytest
from prometheus_client import REGISTRY, CollectorRegistry

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def isolated_prometheus_registry():
    """
    Isolate Prometheus registry for each test to avoid duplicate metric errors.
    
    Creates a temporary registry for the test, then restores the original.
    This prevents "Duplicated timeseries" errors when tests create metrics
    with the same name across multiple test functions.
    """
    # Store original registry contents
    original_collectors = list(REGISTRY._collector_to_names.keys())
    
    yield
    
    # Unregister all collectors added during the test
    current_collectors = list(REGISTRY._collector_to_names.keys())
    for collector in current_collectors:
        if collector not in original_collectors:
            try:
                REGISTRY.unregister(collector)
            except Exception:
                # Ignore unregister errors
                pass
