"""
Exemplar tracker for Prometheus histogram buckets.

Captures one example trace (request_id, timestamp, response_size_bytes) per
histogram bucket per (path, status) label combination using reservoir sampling
for probabilistic replacement.

Example:
    tracker = ExemplarTracker(
        histograms={"request_latency_seconds": request_latency_seconds},
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    )
    
    # Record an exemplar when observing a histogram value
    tracker.record(
        metric_name="request_latency_seconds",
        path="/rag/answer",
        status=200,
        value=0.15,
        request_id="abc123def456",
        response_size_bytes=1024
    )
    
    # Retrieve all exemplars
    exemplars = tracker.get_exemplars()
"""

import time
import random
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import threading


@dataclass
class Exemplar:
    """Single exemplar trace: request_id, timestamp, response_size_bytes."""
    request_id: str
    timestamp: str  # ISO 8601 format
    response_size_bytes: int
    bucket_index: int
    value: float  # actual observed value


class ExemplarTracker:
    """Thread-safe exemplar tracker using reservoir sampling per bucket."""
    
    def __init__(self, histograms: Dict, buckets: List[float]):
        """
        Initialize exemplar tracker.
        
        Args:
            histograms: Dict mapping histogram name to histogram object
            buckets: List of bucket boundaries (e.g., Prometheus default buckets)
        """
        self.histograms = histograms
        self.buckets = sorted(buckets)
        self._lock = threading.RLock()
        
        # Storage: {(metric, path, status, bucket_idx): (exemplar, count)}
        # count = number of values observed in this bucket (for reservoir sampling)
        self._exemplars: Dict[Tuple, Tuple[Exemplar, int]] = {}
    
    def record(
        self,
        metric_name: str,
        path: str,
        status: int,
        value: float,
        request_id: str,
        response_size_bytes: int,
    ) -> None:
        """
        Record an exemplar for a histogram observation.
        
        Uses reservoir sampling to probabilistically replace existing exemplar
        if a new observation falls in the same bucket.
        
        Args:
            metric_name: Name of the histogram metric
            path: Request path (label)
            status: HTTP status code (label)
            value: Observed histogram value (in seconds for request latency)
            request_id: Request ID for tracing
            response_size_bytes: Response size in bytes
        """
        # Find bucket index
        bucket_idx = None
        for idx, bucket_boundary in enumerate(self.buckets):
            if value <= bucket_boundary:
                bucket_idx = idx
                break
        
        # If value > all buckets, it goes to +Inf bucket (index = len(buckets))
        if bucket_idx is None:
            bucket_idx = len(self.buckets)
        
        key = (metric_name, path, status, bucket_idx)
        
        exemplar = Exemplar(
            request_id=request_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            response_size_bytes=response_size_bytes,
            bucket_index=bucket_idx,
            value=value,
        )
        
        with self._lock:
            if key not in self._exemplars:
                # First exemplar in this bucket
                self._exemplars[key] = (exemplar, 1)
            else:
                # Reservoir sampling: keep existing or replace with probability 1/n
                existing_exemplar, count = self._exemplars[key]
                new_count = count + 1
                
                # Probability of keeping the new exemplar: 1/new_count
                if random.random() < (1.0 / new_count):
                    self._exemplars[key] = (exemplar, new_count)
                else:
                    # Keep existing, just increment count
                    self._exemplars[key] = (existing_exemplar, new_count)
    
    def get_exemplars(self) -> Dict:
        """
        Retrieve all stored exemplars organized by metric, path, status.
        
        Returns:
            Dict like:
            {
                "request_latency_seconds": {
                    "/GET_/healthz_200": [
                        {
                            "request_id": "abc123",
                            "timestamp": "2024-01-01T12:34:56Z",
                            "response_size_bytes": 1024,
                            "bucket_index": 5,
                            "value": 0.045
                        },
                        ...
                    ],
                    ...
                }
            }
        """
        with self._lock:
            result = {}
            
            for (metric_name, path, status, bucket_idx), (exemplar, _) in self._exemplars.items():
                if metric_name not in result:
                    result[metric_name] = {}
                
                # Convert tuple to JSON-serializable string key
                key = f"{path}_{status}"
                if key not in result[metric_name]:
                    result[metric_name][key] = []
                
                result[metric_name][key].append(asdict(exemplar))
            
            return result
    
    def clear(self) -> None:
        """Clear all exemplars (useful for testing)."""
        with self._lock:
            self._exemplars.clear()
    
    def get_stats(self) -> Dict:
        """Get storage statistics."""
        with self._lock:
            return {
                "total_slots_occupied": len(self._exemplars),
                "max_slots": len(self.buckets) * 1000,  # rough estimate
                "buckets": len(self.buckets),
            }
