"""
SafeCounter: Production-safe cardinality-bounded counter wrapper.

Prevents unbounded label growth in Prometheus counters by enforcing
a per-metric cardinality budget. When a new label combination would
exceed the budget, excess observations are silently dropped (or grouped
into a default combination).

Example:
    counter = SafeCounter(
        "requests_total",
        "Total requests",
        ["path", "status"],
        budget=100
    )
    
    # Normal use - tracks each path/status
    counter.labels(path="/api/v1", status="200").inc()
    
    # After 100 unique combinations, new ones are silently dropped
    counter.labels(path="/api/v2", status="502").inc()  # → silently dropped
    
    # Check cardinality usage
    usage = counter.get_cardinality_usage()
    # {
    #     "active_combinations": 100,
    #     "budget": 100,
    #     "used_percentage": 100.0,
    #     "is_at_capacity": True,
    #     "has_overflowed": True
    # }
"""

from prometheus_client import Counter as PrometheusCounter
from typing import Dict, Tuple, List, Optional
import threading


class SafeCounter:
    """Thread-safe counter wrapper with cardinality budget enforcement."""
    
    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: List[str],
        budget: int = 100,
        namespace: str = "",
        subsystem: str = "",
    ):
        """
        Initialize a cardinality-safe counter.
        
        Args:
            name: Counter name
            documentation: Counter documentation
            labelnames: List of label names
            budget: Maximum number of unique label combinations (default 100)
            namespace: Prometheus namespace prefix
            subsystem: Prometheus subsystem prefix
        """
        self.name = name
        self.budget = budget
        self.labelnames = labelnames
        self._lock = threading.RLock()
        self._active_combinations: Dict[Tuple, "LabeledSafeCounter"] = {}
        self._overflow_count = 0  # Track how many observations exceeded budget
        
        # Create underlying Prometheus counter with original labels only
        self._counter = PrometheusCounter(
            name=name,
            documentation=documentation,
            labelnames=labelnames,
            namespace=namespace,
            subsystem=subsystem,
        )
    
    def labels(self, **label_values) -> Optional["LabeledSafeCounter"]:
        """
        Get or create a labeled counter instance.
        
        If adding this label combination would exceed the budget,
        returns None and silently ignores the operation.
        
        Args:
            **label_values: Label key=value pairs
            
        Returns:
            LabeledSafeCounter that can be incremented, or None if budget exceeded
        """
        # Validate label names
        provided_keys = set(label_values.keys())
        expected_keys = set(self.labelnames)
        if provided_keys != expected_keys:
            missing = expected_keys - provided_keys
            extra = provided_keys - expected_keys
            msg = f"Label mismatch. "
            if missing:
                msg += f"Missing: {missing}. "
            if extra:
                msg += f"Extra: {extra}."
            raise ValueError(msg)
        
        label_tuple = tuple(label_values[name] for name in self.labelnames)
        
        with self._lock:
            # Check if we've already seen this combination
            if label_tuple in self._active_combinations:
                return self._active_combinations[label_tuple]
            
            # Check if we have budget for a new combination
            if len(self._active_combinations) >= self.budget:
                # Budget exceeded - track overflow but return a no-op wrapper
                self._overflow_count += 1
                return NopCounter()
            
            # Create new combination
            prometheus_labeled = self._counter.labels(**label_values)
            labeled_counter = LabeledSafeCounter(prometheus_labeled, is_overflow=False)
            self._active_combinations[label_tuple] = labeled_counter
            return labeled_counter
    
    def get_cardinality_usage(self) -> Dict:
        """
        Get cardinality usage statistics.
        
        Returns:
            Dict with keys:
                - active_combinations: Number of active label combinations
                - budget: Total budget
                - used_percentage: Percentage of budget used
                - is_at_capacity: True if at budget
                - overflow_observations: Count of observations dropped due to budget
        """
        with self._lock:
            active = len(self._active_combinations)
            return {
                "active_combinations": active,
                "budget": self.budget,
                "used_percentage": round((active / self.budget) * 100, 2),
                "is_at_capacity": active >= self.budget,
                "has_overflowed": self._overflow_count > 0,
                "overflow_observations": self._overflow_count,
            }


class NopCounter:
    """No-op counter that silently ignores increments (for budget overflow)."""
    
    def inc(self, amount: float = 1) -> None:
        """Silently ignore increment."""
        pass
    
    @property
    def is_overflow(self) -> bool:
        """This is an overflow counter."""
        return True


class LabeledSafeCounter:
    """Represents a labeled counter instance."""
    
    def __init__(self, prometheus_labeled_counter, is_overflow: bool = False):
        self._counter = prometheus_labeled_counter
        self._is_overflow = is_overflow
    
    def inc(self, amount: float = 1) -> None:
        """Increment the counter."""
        self._counter.inc(amount)
    
    @property
    def is_overflow(self) -> bool:
        """Whether this counter represents the overflow bucket."""
        return self._is_overflow
    
    @property
    def _value(self):
        """Expose the underlying prometheus counter's _value for testing."""
        return self._counter._value
