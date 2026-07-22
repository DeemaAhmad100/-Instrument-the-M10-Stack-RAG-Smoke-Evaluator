"""Tests for the exemplar tracker (Tier 2 challenge)."""
import pytest
from fastapi.testclient import TestClient
import os

from api.main import app
from api.exemplars import ExemplarTracker
from prometheus_client import Histogram


class TestExemplarTracker:
    """Unit tests for ExemplarTracker class."""
    
    def test_exemplar_recorded_on_first_observation(self):
        """Test that first observation in a bucket is recorded."""
        histogram = Histogram("test_hist_001", "Test histogram", ["path"])
        tracker = ExemplarTracker(
            histograms={"test_hist_001": histogram},
            buckets=[0.01, 0.1, 1.0, 10.0]
        )
        
        tracker.record(
            metric_name="test_hist_001",
            path="/test",
            status=200,
            value=0.05,
            request_id="req-001",
            response_size_bytes=512,
        )
        
        exemplars = tracker.get_exemplars()
        assert "test_hist_001" in exemplars
        assert "/test_200" in exemplars["test_hist_001"]
        assert len(exemplars["test_hist_001"]["/test_200"]) == 1
        
        exemplar = exemplars["test_hist_001"]["/test_200"][0]
        assert exemplar["request_id"] == "req-001"
        assert exemplar["response_size_bytes"] == 512
        assert exemplar["value"] == 0.05
    
    def test_exemplar_slot_per_bucket(self):
        """Test that different buckets store different exemplars."""
        histogram = Histogram("test_hist_002", "Test histogram", ["path"])
        tracker = ExemplarTracker(
            histograms={"test_hist_002": histogram},
            buckets=[0.01, 0.1, 1.0, 10.0]
        )
        
        # Record observations in different buckets
        tracker.record("test_hist_002", "/test", 200, 0.005, "req-001", 100)
        tracker.record("test_hist_002", "/test", 200, 0.05, "req-002", 200)
        tracker.record("test_hist_002", "/test", 200, 0.5, "req-003", 300)
        
        exemplars = tracker.get_exemplars()["test_hist_002"]["/test_200"]
        
        # Should have 3 exemplars (one per bucket)
        assert len(exemplars) == 3
        
        bucket_indices = {ex["bucket_index"] for ex in exemplars}
        assert bucket_indices == {0, 1, 2}  # first 3 buckets
    
    def test_reservoir_sampling_replacement(self):
        """Test that later observations in same bucket replace with probability 1/n."""
        histogram = Histogram("test_hist_003", "Test histogram", ["path"])
        tracker = ExemplarTracker(
            histograms={"test_hist_003": histogram},
            buckets=[0.1, 1.0]
        )
        
        # Record multiple observations in the same bucket
        tracker.record("test_hist_003", "/test", 200, 0.05, "req-001", 100)
        tracker.record("test_hist_003", "/test", 200, 0.06, "req-002", 200)
        tracker.record("test_hist_003", "/test", 200, 0.07, "req-003", 300)
        
        exemplars = tracker.get_exemplars()["test_hist_003"]["/test_200"]
        bucket_0_exemplars = [ex for ex in exemplars if ex["bucket_index"] == 0]
        
        # Should have exactly 1 exemplar in bucket 0 (due to reservoir sampling)
        assert len(bucket_0_exemplars) == 1
        
        # The exemplar should be one of the three recorded (could be any due to randomness)
        request_ids = {ex["request_id"] for ex in bucket_0_exemplars}
        assert request_ids.issubset({"req-001", "req-002", "req-003"})
    
    def test_exemplar_labels_separated(self):
        """Test that exemplars are stored separately for different (path, status)."""
        histogram = Histogram("test_hist_004", "Test histogram", ["path"])
        tracker = ExemplarTracker(
            histograms={"test_hist_004": histogram},
            buckets=[0.1]
        )
        
        tracker.record("test_hist_004", "/path1", 200, 0.05, "req-1", 100)
        tracker.record("test_hist_004", "/path2", 200, 0.05, "req-2", 100)
        tracker.record("test_hist_004", "/path1", 500, 0.05, "req-3", 100)
        
        exemplars_dict = tracker.get_exemplars()["test_hist_004"]
        
        assert "/path1_200" in exemplars_dict
        assert "/path2_200" in exemplars_dict
        assert "/path1_500" in exemplars_dict
        
        assert exemplars_dict["/path1_200"][0]["request_id"] == "req-1"
        assert exemplars_dict["/path2_200"][0]["request_id"] == "req-2"
        assert exemplars_dict["/path1_500"][0]["request_id"] == "req-3"
    
    def test_clear_exemplars(self):
        """Test that clear() removes all exemplars."""
        histogram = Histogram("test_hist_005", "Test histogram", ["path"])
        tracker = ExemplarTracker(
            histograms={"test_hist_005": histogram},
            buckets=[0.1]
        )
        
        tracker.record("test_hist_005", "/test", 200, 0.05, "req-1", 100)
        assert len(tracker.get_exemplars()) > 0
        
        tracker.clear()
        assert len(tracker.get_exemplars()) == 0


class TestExemplarsEndpoint:
    """Integration tests for /debug/exemplars endpoint."""
    
    def test_exemplars_endpoint_disabled_by_default(self):
        """Test that /debug/exemplars returns 403 when DEBUG not set."""
        # Ensure DEBUG is not set
        if "DEBUG" in os.environ:
            del os.environ["DEBUG"]
        
        client = TestClient(app)
        response = client.get("/debug/exemplars")
        
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()
    
    def test_exemplars_endpoint_enabled_with_debug(self):
        """Test that /debug/exemplars returns 200 when DEBUG=1."""
        os.environ["DEBUG"] = "1"
        try:
            client = TestClient(app)
            response = client.get("/debug/exemplars")
            
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, dict)
        finally:
            del os.environ["DEBUG"]
    
    def test_exemplars_endpoint_response_format(self):
        """Test that /debug/exemplars returns properly formatted exemplar data."""
        os.environ["DEBUG"] = "1"
        try:
            client = TestClient(app)
            
            # Make a request to generate some exemplars
            client.get("/healthz")
            
            response = client.get("/debug/exemplars")
            assert response.status_code == 200
            
            data = response.json()
            # Response should be dict with metric names as keys
            assert isinstance(data, dict)
            
            # If exemplars exist, check format
            for metric_name, metric_exemplars in data.items():
                assert isinstance(metric_exemplars, dict)
                # Keys are now strings like "/path_200"
                for key, exemplar_list in metric_exemplars.items():
                    assert isinstance(key, str)
                    assert "_" in key  # Should have path_status format
                    assert isinstance(exemplar_list, list)
                    for exemplar in exemplar_list:
                        assert "request_id" in exemplar
                        assert "timestamp" in exemplar
                        assert "response_size_bytes" in exemplar
                        assert "bucket_index" in exemplar
                        assert "value" in exemplar
        finally:
            del os.environ["DEBUG"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
