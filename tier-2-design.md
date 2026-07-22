# Tier 2: Exemplar Tracker with Reservoir Sampling

## Executive Summary

This design document describes an **exemplar tracker** that captures one trace per Prometheus histogram bucket per (path, status) label combination using **reservoir sampling** for probabilistic exemplar retention. Exemplars link metrics to logs/traces, enabling operators to drill down from aggregated histogram data to individual requests.

## Problem Statement

Histograms answer "how many requests in this latency range?" but not "which request?" Without exemplars, operators investigating tail latency (p99) must either:

1. **Search logs manually** by timestamp window (slow, imprecise)
2. **Add tracing** per-request (expensive, breaks request isolation)
3. **Sample requests** at collection time (loses rare events)

Exemplars solve this: Prometheus histograms can link bucket observations to concrete traces (request_id, timestamp, response size), enabling one-click drill-down from `request_latency_seconds_bucket` to the actual request in logs/APM.

## Solution: ExemplarTracker

**ExemplarTracker** is a thread-safe collector that:

1. **Captures** (request_id, timestamp, response_size_bytes) for every histogram observation
2. **Stores** one exemplar per bucket per (path, status) using **reservoir sampling**
3. **Exposes** exemplars via `GET /debug/exemplars` endpoint (gated by DEBUG env var)
4. **Integrates** transparently into `MetricsMiddleware` without overhead

### Core Design

#### 1. **Bucket Mapping**

Each histogram observation is mapped to a bucket based on its value:

```
value = 0.045 seconds
buckets = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]
         └─ No match with ≤0.025
         └─ No match with ≤0.05 ✓ Match here (bucket_index=3)
```

Values exceeding all buckets map to `+Inf` (index = len(buckets)).

#### 2. **Reservoir Sampling for Retention**

**Problem**: We can't store all observations; memory explodes.  
**Solution**: Keep exactly one exemplar per bucket per (path, status) using **Algorithm R**:

```python
# On the nth observation in a bucket:
# Replace existing exemplar with probability 1/n

new_count = count + 1
if random.random() < (1.0 / new_count):
    exemplar = new_exemplar  # Keep new one
else:
    # Keep existing exemplar, just increment count
    pass
```

**Guarantees**:
- Each observation has equal probability of being selected
- Memory is O(buckets × paths × statuses) — typically 14 × 5 × 3 = 210 slots
- Handles streaming indefinitely without buffer overflow

**Example**:
```
Bucket 3 (value ≤ 0.05):
  1st obs (req-001):  prob 1/1 = 1.0 → select req-001 ✓
  2nd obs (req-002):  prob 1/2 = 0.5 → maybe replace
  3rd obs (req-003):  prob 1/3 ≈ 0.33 → maybe replace
  ...
  After 1000 obs:     prob 1/1000 ≈ 0.001 → rarely replace
```

Result: Each of the 1000 requests had a fair chance; one survived uniformly at random.

#### 3. **Label Organization**

Exemplars are stored and returned organized by:
- **Metric name** → `{"request_latency_seconds": {...}}`
- **Label tuple** → `{("/rag/answer", 200): [...]}`  
- **Exemplar list** → Array of exemplar objects with request_id, timestamp, etc.

```json
{
  "request_latency_seconds": {
    ("GET /healthz", 200): [
      {
        "request_id": "abc123",
        "timestamp": "2024-01-01T12:34:56Z",
        "response_size_bytes": 52,
        "bucket_index": 0,
        "value": 0.002
      }
    ],
    ("POST /rag/answer", 200): [
      {
        "request_id": "def456",
        "timestamp": "2024-01-01T12:34:57Z",
        "response_size_bytes": 4096,
        "bucket_index": 11,
        "value": 2.5
      }
    ]
  }
}
```

## Implementation

### ExemplarTracker Class

**File**: `api/exemplars.py`

**Key methods**:

1. **`__init__(histograms, buckets)`**  
   - Store histogram objects and bucket boundaries  
   - Initialize thread-safe storage

2. **`record(metric_name, path, status, value, request_id, response_size_bytes)`**  
   - Map value to bucket_index  
   - Apply reservoir sampling  
   - Thread-safe via RLock

3. **`get_exemplars() → dict`**  
   - Return nested dict of exemplars organized by metric/labels

4. **`clear()`**  
   - Wipe all exemplars (testing)

### Integration into Lab

**Files**:
- `api/exemplars.py`: ExemplarTracker class
- `api/observability.py`: 
  - Import ExemplarTracker
  - Instantiate `exemplar_tracker` module-scoped
  - Update `MetricsMiddleware` to call `exemplar_tracker.record(...)`
- `api/main.py`:
  - Import `get_exemplars()`
  - Add `GET /debug/exemplars` endpoint

### MetricsMiddleware Changes

**Original**:
```python
request_latency_seconds.labels(path=path).observe(elapsed)
```

**Updated**:
```python
request_latency_seconds.labels(path=path).observe(elapsed)

# Tier 2: Record exemplar (one per bucket per path/status)
exemplar_tracker.record(
    metric_name="request_latency_seconds",
    path=path,
    status=status,
    value=elapsed,
    request_id=request_id,
    response_size_bytes=response_size_bytes,  # Captured from ASGI response
)
```

### Debug Endpoint

**File**: `api/main.py`  
**Endpoint**: `GET /debug/exemplars`

Gated by `DEBUG=1` environment variable:

```json
{
  "request_latency_seconds": {
    ["GET /healthz", 200]: [
      {
        "request_id": "abc123def456",
        "timestamp": "2024-01-01T12:34:56.789Z",
        "response_size_bytes": 52,
        "bucket_index": 0,
        "value": 0.002
      }
    ]
  }
}
```

## Operational Usage

### 1. **Debugging Slow Requests**

Operator sees histogram showing p99 latency is 0.8s:

```promql
histogram_quantile(0.99, rate(request_latency_seconds_bucket[5m])) = 0.8 seconds
```

Operator queries `/debug/exemplars`:

```bash
curl -s http://localhost:8000/debug/exemplars?DEBUG=1 | jq '.request_latency_seconds'
```

Finds exemplar in 0.75s bucket:
```json
{
  "request_id": "xyz789abc",
  "timestamp": "2024-01-01T12:34:56Z"
}
```

Operator searches logs for `request_id=xyz789abc` and finds:
```
2024-01-01T12:34:56.100Z [xyz789abc] POST /rag/answer
2024-01-01T12:34:56.200Z [xyz789abc] Retrieving chunks from Weaviate (50ms)
2024-01-01T12:34:56.750Z [xyz789abc] Generating response with LLM (550ms) ← Bottleneck!
2024-01-01T12:34:56.900Z [xyz789abc] 200 OK (4096 bytes)
```

Root cause identified: LLM generation is slow.

### 2. **Monitoring Exemplar Freshness**

Check `/debug/exemplars` periodically to see if timestamps are recent:
- Stale timestamps → No traffic in that bucket → Normal
- Fresh timestamps → Recent traffic in that bucket → Expected
- Missing exemplar → Bucket never used (code path not exercised)

## Testing

**File**: `tests/test_exemplars.py`

Comprehensive test suite covering:

1. **Exemplar recording**: First observation is captured
2. **Bucket mapping**: Different values map to correct buckets
3. **Reservoir sampling**: Multiple observations in same bucket respect sampling probability
4. **Label separation**: Different (path, status) pairs store independently
5. **Endpoint gating**: `/debug/exemplars` respects DEBUG env var
6. **Response format**: Returns properly structured JSON
7. **Thread safety**: Concurrent access is safe (implicitly tested by FastAPI TestClient)

**Key test: `test_reservoir_sampling_replacement`**

```python
tracker.record(..., value=0.05, request_id="req-001", ...)
tracker.record(..., value=0.06, request_id="req-002", ...)
tracker.record(..., value=0.07, request_id="req-003", ...)

exemplars = tracker.get_exemplars()
# Exactly 1 exemplar in the bucket (reservoir constraint)
# The request_id is one of {req-001, req-002, req-003} with equal probability
```

## Design Decisions

### 1. **One Exemplar per Bucket (Not All)**

**Decision**: Store exactly one exemplar per bucket per (path, status).  
**Rationale**: 
- More exemplars = more storage, more endpoints to query
- One exemplar is sufficient to drill down to logs
- Bucket assignment implicitly conveys latency range

### 2. **Reservoir Sampling (Not FIFO/LRU)**

**Decision**: Use Algorithm R for probabilistic replacement.  
**Rationale**:
- Fair: Every observation has equal selection probability
- Unbiased: No preference for old/new observations
- Streaming-safe: Works indefinitely without pre-sizing
- Alternative (FIFO): Would over-represent old requests
- Alternative (LRU): Would over-represent cached requests

### 3. **Endpoint Gating with DEBUG**

**Decision**: Only expose `/debug/exemplars` when `DEBUG=1`.  
**Rationale**:
- Production deployments should not expose internal request IDs to untrusted clients
- Enables safe debugging in dev/staging
- Operators can enable on-demand in production for troubleshooting

### 4. **Thread Safety via RLock**

**Decision**: Protect all access to exemplar storage with `threading.RLock()`.  
**Rationale**:
- FastAPI uses async and thread pools for different request types
- `record()` called from ASGI middleware (async context)
- `get_exemplars()` called from endpoint (could be concurrent)
- RLock allows nested acquisition (e.g., if `record()` calls another guarded method)

## Production Considerations

### 1. **Memory Overhead**

```
14 buckets × 100 paths × 5 statuses = 7,000 exemplar slots
Per exemplar: ~200 bytes (request_id, timestamp, size, indices)
Total: 7,000 × 200 bytes ≈ 1.4 MB
```

Negligible for most systems; scales linearly with cardinality.

### 2. **Sampling Semantics**

**Question**: Can I trust that an exemplar represents the distribution?  
**Answer**: Yes, with caveats:
- Each observation has equal probability of selection
- For rare events (low-frequency buckets), exemplar is likely old (didn't happen recently)
- For frequent events (high-frequency buckets), exemplar reflects recent activity
- **Limitation**: If a specific request ID matters (e.g., "I saw a 503 earlier"), exemplar might not capture it

### 3. **Integration with APM**

Exemplars are designed to link to distributed tracing systems:

```python
exemplar = {
    "request_id": "trace-id-from-apm",  # Can copy to span ID
    "timestamp": "...",
    "response_size_bytes": ...
}

# Operator can then:
# 1. Copy request_id
# 2. Paste into APM (Jaeger, Datadog, etc.)
# 3. See full distributed trace for that request
```

## Future Extensions

1. **Span ID linking**: Store span ID alongside request_id for direct APM integration
2. **Bucket ranges**: Return bucket min/max boundaries with exemplar
3. **Exemplar staleness**: Return age of exemplar (useful for low-traffic buckets)
4. **Prometheus native exemplars**: Format for Prometheus native exemplar protocol

## Conclusion

**ExemplarTracker** provides a simple, memory-efficient way to link aggregated histogram data to individual requests via reservoir sampling. By storing one exemplar per bucket per label combination, operators can drill down from high-level latency insights to concrete request traces in logs/APM.

**Key takeaway**: Exemplars close the gap between metrics and traces, enabling faster MTTR for latency issues.
