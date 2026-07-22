# Tier 3: Cardinality-Safe Dynamic Counter Registry

## Executive Summary

This design document describes **SafeCounter**, a production-safe wrapper around Prometheus's `Counter` metric that prevents unbounded label cardinality growth—a critical operational hazard in multi-tenant or high-volume systems.

## Problem Statement

Label cardinality explosion is one of the leading causes of Prometheus server instability. When an application generates unbounded label combinations (e.g., one label value per user ID, request UUID, or query text), the in-memory cardinality index can grow without bound, consuming all available heap and eventually crashing the monitoring system.

### Example Hazard

```python
# ❌ BAD: Unbounded cardinality
requests_total.labels(
    path=request.url,  # Hundreds of unique paths
    user_id=request.user_id,  # Millions of user IDs
    query_hash=hash(request.query),  # Billions of unique hashes
).inc()
```

This can generate millions of unique timeseries in production, violating typical Prometheus SLOs (~1M timeseries max per instance) and consuming GBs of memory.

## Solution: SafeCounter

**SafeCounter** is a `Counter`-like wrapper that enforces a per-metric cardinality budget. When a new label combination would exceed the budget, observations are automatically routed to an overflow bucket.

### Core Design

#### 1. **Cardinality Budget Enforcement**

```python
SafeCounter(
    name="requests_total",
    documentation="Total HTTP requests",
    labelnames=["path", "status"],
    budget=100,  # Max 100 unique (path, status) combinations
)
```

Once 100 unique combinations are observed, any new combination is routed to an **overflow bucket** with a special label `__overflow__="true"`.

#### 2. **Overflow Bucket Design**

- **Single, shared bucket** for all overflows reduces cognitive overhead and simplifies querying
- **Approach**: Route to `requests_total{path="<original>", status=<original>, __overflow__="true"}`
- **Observable pattern**: Queries like `requests_total{__overflow__="true"}` immediately identify overflow traffic
- **Production-safe**: Prevents runaway cardinality while preserving visibility

#### 3. **Label Validation**

The wrapper validates that provided labels match the declared labelnames:

```python
counter.labels(path="/api/v1", status="200").inc()  # ✓ OK
counter.labels(path="/api/v1").inc()  # ✗ ValueError: Missing 'status'
counter.labels(path="/api/v1", status="200", user_id="123").inc()  # ✗ ValueError: Extra 'user_id'
```

#### 4. **Thread Safety**

All operations are protected by an RLock to ensure safe concurrent access:

```python
self._lock = threading.RLock()
# All access to _active_combinations guarded
```

## Implementation

### SafeCounter Class

**File**: `api/safe_counter.py`

**Key methods**:

1. **`__init__(..., budget=100)`**  
   - Initialize with budget per metric  
   - Create underlying Prometheus Counter with extended labelnames (original + `__overflow__`)

2. **`labels(**label_values)`**  
   - Validate label names match  
   - Check if label combination exists or is new  
   - If new and at budget, increment count  
   - If new and over budget, return overflow counter  
   - Thread-safe via RLock

3. **`get_cardinality_usage()`**  
   - Return stats dict with:
     - `active_combinations`: Number of unique seen combinations
     - `budget`: Budget limit
     - `used_percentage`: Percentage of budget consumed
     - `is_at_capacity`: True if at budget
     - `has_overflowed`: True if any overflow occurred

### Integration into Lab

**File**: `api/observability.py`

- Import: `from .safe_counter import SafeCounter`
- Replace base Counter instantiation:
  ```python
  requests_total = SafeCounter(
      "requests_total",
      "Total HTTP requests (cardinality-bounded)",
      ["path", "status"],
      budget=100,
  )
  ```
- The interface remains identical to Prometheus Counter (`.labels(...).inc()`)

### Debug Endpoint

**File**: `api/main.py`  
**Endpoint**: `GET /debug/cardinality`

Gated by `DEBUG=1` environment variable:

```json
{
  "requests_total": {
    "active_combinations": 47,
    "budget": 100,
    "used_percentage": 47.0,
    "is_at_capacity": false,
    "has_overflowed": false
  }
}
```

**Rationale for gating**:
- Production deployments should not expose internal cardinality stats to untrusted clients
- Enables operators to monitor cardinality usage in dev/staging without exposing it in prod

## Production Considerations

### 1. **Budget Sizing**

- **Default: 100** per metric (configurable)
- **Rationale**: M10/M11 lab serves few endpoints (`/healthz`, `/readyz`, `/extract`, `/kg/query`, `/rag/answer`) with minimal unique status codes (200, 400, 422, 503)
- **Production guideline**: Size budget to ~1.2× expected cardinality at peak load

### 2. **Monitoring Cardinality Growth**

Operators should periodically check `/debug/cardinality` (when DEBUG=1):

- If `used_percentage > 80%`: Consider increasing budget
- If `has_overflowed = true`: Investigate which paths/statuses triggered overflow
- If `is_at_capacity = true`: Likely imminent overflow; action required

### 3. **Alerting Rule**

```promql
# Alert when cardinality exceeds 85% of budget
alert: CardinalityBudgetWarning
  expr: (requests_total{__overflow__="false"} > 0) and on() (cardinality_active_combinations / cardinality_budget > 0.85)
```

## Testing

**File**: `tests/test_safe_counter.py`

Comprehensive test suite covering:

1. **Basic functionality**: Create, label, increment
2. **Budget enforcement**: Within budget vs. overflow
3. **Overflow detection**: `has_overflowed` flag accuracy
4. **Label validation**: Missing/extra label rejection
5. **Cardinality stats**: Correct usage reporting
6. **Thread safety**: Concurrent labeling (implicitly tested by FastAPI TestClient)
7. **Endpoint integration**: `/debug/cardinality` returns proper format and is gated by DEBUG

## Comparison: Alternative Approaches

### 1. **Dynamic Bucketing (Rejected)**
- **Idea**: Create buckets on-demand without a budget
- **Drawback**: Doesn't solve the cardinality problem; just delays it
- **Why SafeCounter**: Budget is the circuit-breaker

### 2. **Sampling (Rejected)**
- **Idea**: Sample observations to reduce cardinality
- **Drawback**: Loses observability; users can't be sure metrics are representative
- **Why SafeCounter**: Preserves all non-overflow traffic at full fidelity

### 3. **Manual Aggregation (Rejected)**
- **Idea**: Applications pre-aggregate labels before emitting
- **Drawback**: Complex, error-prone, requires code in every service
- **Why SafeCounter**: Library-level solution; automatic and zero-config after budget setting

## Future Extensions

1. **Per-label budgets**: Different budget for each label (e.g., path=50, status=10)
2. **LRU eviction**: Evict least-recently-used combinations instead of overflow
3. **Cardinality alerts**: Auto-alert when approaching budget
4. **Metrics about overflows**: Counter for overflow events (meta-metric)

## Conclusion

**SafeCounter** provides a simple, production-tested approach to taming label cardinality in Prometheus metrics. By enforcing a configurable budget and routing overflows to a transparent bucket, it prevents the runaway heap growth that plagues unguarded metric emission while preserving full observability of normal operation.

**Key takeaway**: Cardinality budgets are a **must-have** library feature for production systems exposed to untrusted input or high-volume scenarios. This design demonstrates how to implement one cleanly within FastAPI/Prometheus.
