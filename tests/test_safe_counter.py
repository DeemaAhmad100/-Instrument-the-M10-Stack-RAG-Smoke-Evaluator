"""Tests for the SafeCounter (Tier 3 challenge)."""
import pytest
import os
from fastapi.testclient import TestClient

from api.main import app
from api.safe_counter import SafeCounter


class TestSafeCounter:
    """Unit tests for SafeCounter class."""
    
    def test_safe_counter_creation(self):
        """Test basic SafeCounter instantiation."""
        counter = SafeCounter(
            name="test_counter",
            documentation="Test counter",
            labelnames=["path", "status"],
            budget=10,
        )
        assert counter.budget == 10
        assert counter.name == "test_counter"
    
    def test_labels_within_budget(self):
        """Test labeling within the cardinality budget."""
        counter = SafeCounter(
            name="test_counter",
            documentation="Test counter",
            labelnames=["path", "status"],
            budget=5,
        )
        
        # Create 3 label combinations (within budget of 5)
        counter.labels(path="/api/1", status="200").inc()
        counter.labels(path="/api/2", status="200").inc()
        counter.labels(path="/api/1", status="500").inc()
        
        usage = counter.get_cardinality_usage()
        assert usage["active_combinations"] == 3
        assert usage["budget"] == 5
        assert not usage["is_at_capacity"]
    
    def test_overflow_at_budget_boundary(self):
        """Test that new labels overflow when budget is reached."""
        counter = SafeCounter(
            name="test_counter",
            documentation="Test counter",
            labelnames=["path", "status"],
            budget=2,
        )
        
        # Add 2 label combinations (at budget)
        counter.labels(path="/api/1", status="200").inc()
        counter.labels(path="/api/2", status="200").inc()
        
        # 3rd combination should overflow
        counter.labels(path="/api/3", status="200").inc()
        
        usage = counter.get_cardinality_usage()
        assert usage["active_combinations"] == 2
        assert usage["is_at_capacity"]
        assert usage["has_overflowed"]
    
    def test_label_validation(self):
        """Test that label validation catches missing/extra labels."""
        counter = SafeCounter(
            name="test_counter",
            documentation="Test counter",
            labelnames=["path", "status"],
            budget=10,
        )
        
        # Valid call
        counter.labels(path="/test", status="200").inc()
        
        # Missing label
        with pytest.raises(ValueError, match="Missing"):
            counter.labels(path="/test").inc()
        
        # Extra label
        with pytest.raises(ValueError, match="Extra"):
            counter.labels(path="/test", status="200", extra="field").inc()
    
    def test_cardinality_usage_stats(self):
        """Test cardinality usage statistics."""
        counter = SafeCounter(
            name="test_counter",
            documentation="Test counter",
            labelnames=["path"],
            budget=100,
        )
        
        counter.labels(path="/api/1").inc()
        counter.labels(path="/api/2").inc()
        counter.labels(path="/api/3").inc()
        
        usage = counter.get_cardinality_usage()
        assert usage["active_combinations"] == 3
        assert usage["budget"] == 100
        assert usage["used_percentage"] == 3.0
        assert not usage["is_at_capacity"]
        assert not usage["has_overflowed"]
    
    def test_repeated_labels_dont_overflow(self):
        """Test that using the same label combo multiple times doesn't trigger overflow."""
        counter = SafeCounter(
            name="test_counter",
            documentation="Test counter",
            labelnames=["path", "status"],
            budget=1,
        )
        
        # Use same label multiple times
        counter.labels(path="/api", status="200").inc()
        counter.labels(path="/api", status="200").inc()
        counter.labels(path="/api", status="200").inc()
        
        usage = counter.get_cardinality_usage()
        assert usage["active_combinations"] == 1
        assert not usage["has_overflowed"]


class TestCardinalityEndpoint:
    """Integration tests for /debug/cardinality endpoint."""
    
    def test_cardinality_endpoint_disabled_by_default(self):
        """Test that /debug/cardinality returns 403 when DEBUG not set."""
        if "DEBUG" in os.environ:
            del os.environ["DEBUG"]
        
        client = TestClient(app)
        response = client.get("/debug/cardinality")
        
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()
    
    def test_cardinality_endpoint_enabled_with_debug(self):
        """Test that /debug/cardinality returns 200 when DEBUG=1."""
        os.environ["DEBUG"] = "1"
        try:
            client = TestClient(app)
            response = client.get("/debug/cardinality")
            
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, dict)
            assert "requests_total" in data
        finally:
            del os.environ["DEBUG"]
    
    def test_cardinality_endpoint_response_format(self):
        """Test that /debug/cardinality returns properly formatted data."""
        os.environ["DEBUG"] = "1"
        try:
            client = TestClient(app)
            response = client.get("/debug/cardinality")
            
            assert response.status_code == 200
            data = response.json()
            
            # Check requests_total metric
            assert "requests_total" in data
            rt = data["requests_total"]
            
            assert "active_combinations" in rt
            assert "budget" in rt
            assert "used_percentage" in rt
            assert "is_at_capacity" in rt
            assert "has_overflowed" in rt
            
            # Verify types
            assert isinstance(rt["active_combinations"], int)
            assert isinstance(rt["budget"], int)
            assert isinstance(rt["used_percentage"], (int, float))
            assert isinstance(rt["is_at_capacity"], bool)
            assert isinstance(rt["has_overflowed"], bool)
        finally:
            del os.environ["DEBUG"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
