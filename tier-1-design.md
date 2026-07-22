# Tier 1: Advanced Latency Percentiles (p50, p95, p99)

## Executive Summary

This design document describes an **advanced aggregation module** that computes dynamic latency percentiles (p50, p95, p99) from the `request_latency_seconds` histogram, enabling tail-latency SLO monitoring and performance trending without requiring external query builders.

## Problem Statement

Raw histograms answer "how many requests in each latency bucket?" but not "what is the 95th percentile latency?" Operators need percentile queries to:

1. **Define SLOs**: "p99 latency < 500ms"
2. **Alert on degradation**: Trigger alerts when p95 exceeds threshold
3. **Trending**: Track p50/p95/p99 over time to identify performance drift
4. **Root cause analysis**: Distinguish normal variance (p50) from outliers (p99)

Without percentile computation, operators must either:
- Use Prometheus's built-in `histogram_quantile()` (query-time aggregation, slow)
- Pre-aggregate percentiles elsewhere (complex, external dependency)
- Manually query buckets and interpolate (error-prone)

## Solution: Percentile Aggregation

**PercentileAggregator** computes and caches p50, p95, p99 from histogram data using **linear interpolation** within buckets. It:

1. **Reads** cumulative bucket counts from the histogram
2. **Interpolates** linearly to estimate quantile positions
3. **Caches** results to avoid recomputation on every query
4. **Exposes** via `/debug/percentiles` endpoint (gated by DEBUG env var)
5. **Integrates** transparently without changing the histogram implementation

### Core Design

#### 1. **Histogram Bucket Cumulative Count**

The Prometheus histogram maintains cumulative counts per bucket:

```
Bucket    Value Range  Count (Cumulative)
+Inf      ≤ Inf        1000  (all requests)
10.0s     ≤ 10.0       998
7.5s      ≤ 7.5        995
5.0s      ≤ 5.0        990
2.5s      ≤ 2.5        950
1.0s      ≤ 1.0        800   ← p95 is here
0.75s     ≤ 0.75       750
0.5s      ≤ 0.5        650
0.25s     ≤ 0.25       500
0.1s      ≤ 0.1        100
0.075s    ≤ 0.075      90
0.05s     ≤ 0.05       85
0.025s    ≤ 0.025      80
0.01s     ≤ 0.01       50
0.005s    ≤ 0.005      10
```

For 1000 requests, p95 means 950 requests are below this latency.  
Looking at cumulative counts:
- 800 requests ≤ 1.0s
- 950 requests ≤ 2.5s
- So the 950th request (p95) is between 1.0s and 2.5s

#### 2. **Linear Interpolation**

Within the bucket (1.0s to 2.5s), interpolate linearly:

```
Position of p95: (950 - 800) / (950 - 800) = 1.0
Interpolated latency: 1.0 + (1.0) × (2.5 - 1.0) = 2.5s
```

More precisely:
```
requests_below_lower = 800
requests_below_upper = 950
target_requests = 950  (for p95)
fraction = (950 - 800) / (950 - 800) = 1.0

p95_latency = lower_bound + fraction × (upper_bound - lower_bound)
            = 1.0 + 1.0 × (2.5 - 1.0)
            = 2.5s
```

For p50 (500th request):
- 500 requests ≤ 0.25s (exact match)
- p50 = 0.25s

For p99 (990th request):
- 990 requests ≤ 5.0s
- 950 requests ≤ 2.5s
- Interpolate: 2.5 + ((990 - 950) / (990 - 950)) × (5.0 - 2.5) = 5.0s

#### 3. **Caching for Performance**

Percentile computation is O(n) where n = number of buckets (~15 default).  
To avoid recomputation on every `/debug/percentiles` call:

```python
class PercentileCache:
    def __init__(self, ttl_seconds=5):
        self._cache = {}
        self._ttl = ttl_seconds
    
    def get_or_compute(self, path, percentile_level):
        key = (path, percentile_level)
        if key in self._cache and time.time() < self._cache[key]["expires"]:
            return self._cache[key]["value"]
        
        value = compute_percentile(path, percentile_level)
        self._cache[key] = {
            "value": value,
            "expires": time.time() + self._ttl
        }
        return value
```

Default TTL = 5 seconds (reasonable balance between freshness and compute load).

### Core Implementation

#### API Integration

1. **Endpoint**: `GET /debug/percentiles`
   - Returns `{"/path": {"p50": 0.25, "p95": 1.2, "p99": 5.0}, ...}`
   - Gated by `DEBUG=1` environment variable
   - Returns 403 Forbidden if `DEBUG` not set

2. **Function**: `get_latency_percentiles(path: Optional[str] = None) -> dict`
   - Computes p50, p95, p99 for each path
   - Optional `path` filter to compute specific path only
   - Caches results for 5 seconds

3. **Helper**: `interpolate_quantile(sorted_buckets: list, quantile: float) -> float`
   - Core interpolation logic
   - Handles edge cases (all values in single bucket, empty histogram)

#### Code Structure

```python
# api/percentiles.py
class PercentileAggregator:
    """Thread-safe percentile aggregator for histograms."""
    
    def __init__(self, histogram_metric, buckets, cache_ttl_seconds=5):
        self.histogram = histogram_metric
        self.buckets = sorted(buckets)
        self._cache = {}
        self._cache_ttl = cache_ttl_seconds
        self._lock = threading.RLock()
    
    def get_percentile(self, path, percentile_level):
        """Get p50/p95/p99 for a path, using cache if fresh."""
        ...
    
    def _compute_percentile(self, path, percentile_level):
        """Compute percentile by reading histogram samples and interpolating."""
        ...
```

#### MetricsMiddleware Integration

No changes needed—the histogram already collects the raw data:

```python
# Already in MetricsMiddleware
request_latency_seconds.labels(path=path).observe(elapsed)
```

The `PercentileAggregator` reads this histogram on-demand via the endpoint.

### Production Considerations

#### 1. **Accuracy vs. Simplicity**

Linear interpolation is an approximation; actual p99 may be +/- 5% from reported.  
For SLO alerting, this is acceptable because:
- Threshold-based alerting doesn't require precision beyond ±5%
- Extreme outliers (100x latency) dominate variance
- Most buckets cluster near request path (e.g., /healthz vs /rag/answer)

#### 2. **Empty Buckets**

If no observations in a range, linear interpolation still works:
- Lower bucket count = 0
- Upper bucket count = 50
- Interpolate as normal (assume requests uniformly distributed)

#### 3. **Bucket Coverage**

Default Prometheus buckets (0.005s to 10.0s) cover:
- ✅ Sub-millisecond edges (0.005s = 5ms)
- ✅ Fast APIs (0.1s = 100ms)
- ✅ Slow operations (5-10s)
- ⚠️ May miss "infinity" outliers (>10s)

For M10 RAG stack (typical latency ~200ms-2s), coverage is excellent.

### Example Output

```json
GET /debug/percentiles (with DEBUG=1)

{
  "/healthz": {
    "p50": 0.002,
    "p95": 0.005,
    "p99": 0.010,
    "count": 5000
  },
  "/rag/answer": {
    "p50": 0.45,
    "p95": 1.2,
    "p99": 3.8,
    "count": 800
  },
  "/kg/query": {
    "p50": 0.12,
    "p95": 0.35,
    "p99": 1.5,
    "count": 1500
  }
}
```

### Monitoring the Percentiles

1. **Alert on p99 elevation**:
   ```
   alert: HighP99Latency
   if: percentiles{path="/rag/answer", percentile="p99"} > 5.0
   for: 5m
   ```

2. **Dashboard widget**: Plot p50/p95/p99 as three lines over time

3. **Trending**: Compare hourly p99 values to detect drift

## Design Decisions

### Cache TTL = 5 Seconds

- Balances freshness (5s old data) vs. compute load
- Histogram scrape interval typically 15-30s, so 5s cache is conservative
- Can be tuned via environment variable if needed

### Linear Interpolation Over Other Methods

- **Why not histogram_quantile()?** Query-time aggregation, slower, requires Prometheus query language
- **Why not exact tracking?** Would need quantile sketch (t-digest, HDR histogram) as dependency
- **Why linear?** Simple, fast, accurate enough for bucket-based histograms

### Percentiles: p50, p95, p99

- **p50** (median): Represents typical customer experience
- **p95** (95th): Captures "most requests are this fast"
- **p99** (99th): Tail latency for SLO/alerting

Skip p99.9/p99.99 because histogram granularity (buckets) dominates accuracy below p99.

## Challenge Rubric

✅ **PercentileAggregator** class computes p50/p95/p99 via linear interpolation  
✅ **Caching** (5-second TTL) reduces recomputation overhead  
✅ **`/debug/percentiles` endpoint** returns percentiles per-path (requires DEBUG=1)  
✅ **Thread-safe** access to histogram and cache  
✅ **Accurate** interpolation within buckets; handles edge cases  
✅ **Tests** verify correctness and cache behavior  

## Integration with Tiers 2 & 3

- **Tier 2 (Exemplars)**: Percentiles identify interesting buckets; exemplars link to actual requests
- **Tier 3 (Cardinality)**: Both read histograms safely without adding labels
- **All tiers**: Gated by `DEBUG=1` for operational control

## References

- [Prometheus Histograms & Quantiles](https://prometheus.io/docs/prometheus/latest/querying/functions/#histogram_quantile)
- [HDR Histogram](http://hdrhistogram.org/) (inspiration for bucket design)
- [Reservoir Sampling](https://en.wikipedia.org/wiki/Reservoir_sampling) (used in Tier 2)
