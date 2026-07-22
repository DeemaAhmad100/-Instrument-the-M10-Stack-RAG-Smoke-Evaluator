"""
Cardinality-safe counter wrapper for preventing unbounded label growth.

Tier 3 Challenge: Implements SafeCounter to protect against cardinality explosions
when label values are derived from high-cardinality sources (e.g., user IDs, request paths).
"""

import threading
from typing import Dict, Tuple
from prometheus_client import Counter


class SafeCounter:
    """
    Wraps prometheus_client.Counter to enforce cardinality limits.
    
    When total unique label combinations exceed max_cardinality,
    excess combinations are bucketed into an "OTHER" category.
    """
    
    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list,
        max_cardinality: int = 100,
    ):
        """
        Initialize SafeCounter.
        
        Args:
            name: Prometheus metric name
            documentation: Metric description
            labelnames: Label names (e.g., ["path", "status"])
            max_cardinality: Maximum unique label combinations allowed
        """
        self.name = name
        self.max_cardinality = max_cardinality
        self.labelnames = labelnames
        
        # Inner prometheus counter
        self._counter = Counter(
            name,
            documentation,
            labelnames=labelnames,
        )
        
        # Track cardinality: lock-protected dict of label_values -> count
        self._cardinality_lock = threading.Lock()
        self._seen_labels: Dict[Tuple, int] = {}
        
    def labels(self, **kwargs):
        """
        Thread-safe labels() that enforces cardinality limits.
        
        Returns a prometheus LabelWrapper that can be incremented.
        If cardinality would exceed max, returns labels(**{'label': 'OTHER'}).
        """
        label_values = tuple(kwargs.get(name, '') for name in self.labelnames)
        
        with self._cardinality_lock:
            if label_values not in self._seen_labels:
                if len(self._seen_labels) >= self.max_cardinality:
                    # Cardinality limit reached - bucket into OTHER
                    other_labels = {name: 'OTHER' for name in self.labelnames}
                    return self._counter.labels(**other_labels)
                else:
                    # Record this as a new label combination
                    self._seen_labels[label_values] = 0
        
        return self._counter.labels(**kwargs)
    
    def inc(self, amount=1, **kwargs):
        """Increment counter with cardinality protection."""
        self.labels(**kwargs).inc(amount)
    
    def get_cardinality_stats(self):
        """
        Return cardinality statistics.
        
        Returns:
            dict with keys:
                - unique_combinations: Number of unique label sets seen
                - max_cardinality: Configured limit
                - cardinality_used_percent: Utilization percentage
                - cardinality_exceeded: Boolean indicating if OTHER bucketing was triggered
        """
        with self._cardinality_lock:
            unique = len(self._seen_labels)
            exceeded = unique >= self.max_cardinality
            percent = (unique / self.max_cardinality * 100) if self.max_cardinality > 0 else 0
        
        return {
            "unique_combinations": unique,
            "max_cardinality": self.max_cardinality,
            "cardinality_used_percent": round(percent, 2),
            "cardinality_exceeded": exceeded,
        }
