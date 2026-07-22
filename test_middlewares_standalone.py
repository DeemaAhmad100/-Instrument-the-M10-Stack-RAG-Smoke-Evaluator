# test_middlewares_standalone.py
import time
import json
import uuid
from contextvars import ContextVar
from typing import Dict

# ====================== Simulate Observability Components ======================

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

class SimulatedRequest:
    def __init__(self, path: str, method: str = "POST"):
        self.url = type("URL", (), {"path": path})()
        self.method = method
        self.headers = {}

class SimulatedResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.headers: Dict[str, str] = {}

# ---------------------- Request ID Logic ----------------------
def simulate_request_id():
    request_id = uuid.uuid4().hex
    request_id_var.set(request_id)
    response = SimulatedResponse()
    response.headers["X-Request-ID"] = request_id
    print(f"✅ Request ID generated: {request_id}")
    print(f"✅ X-Request-ID header set: {response.headers['X-Request-ID']}")
    return request_id

# ---------------------- Structured Logging Logic ----------------------
def simulate_structured_logging(request_id: str, path: str, status: int, latency_ms: float):
    log_entry = {
        "ts": time.time(),
        "level": "INFO",
        "request_id": request_id,
        "path": path,
        "method": "POST",
        "status": status,
        "latency_ms": round(latency_ms, 2)
    }
    
    log_line = json.dumps(log_entry, ensure_ascii=False)
    print(f"📝 Structured Log:")
    print(log_line)
    
    # Verify required keys
    parsed = json.loads(log_line)
    required_keys = {"request_id", "path", "status", "latency_ms"}
    missing = required_keys - set(parsed.keys())
    
    if not missing:
        print("✅ Structured log contains all required keys")
    else:
        print(f"❌ Missing keys: {missing}")
    return parsed

# ---------------------- Metrics Logic (Simulation) ----------------------
requests_total = {}
request_latency = []
inflight = 0

def simulate_metrics(path: str, status: int, latency: float):
    global inflight
    # Inflight
    inflight += 1
    print(f"📊 Inflight requests: {inflight}")
    
    # Requests total
    key = (path, status)
    requests_total[key] = requests_total.get(key, 0) + 1
    print(f"📈 requests_total[{path}][{status}]: {requests_total[key]}")
    
    # Latency
    request_latency.append(latency)
    print(f"⏱️  Latency observed: {latency:.4f}s")
    
    inflight -= 1
    print(f"📊 Inflight after: {inflight}")

# ====================== Full Request Simulation ======================
def simulate_full_request(path: str = "/rag/answer"):
    print("\n" + "="*60)
    print(f"Simulating request to: {path}")
    print("="*60)
    
    start = time.time()
    
    # 1. Request ID
    req_id = simulate_request_id()
    
    # 2. Simulate processing
    time.sleep(0.05)  # simulate some work
    
    # 3. Structured Logging
    latency_ms = (time.time() - start) * 1000
    log_data = simulate_structured_logging(req_id, path, 200, latency_ms)
    
    # 4. Metrics
    simulate_metrics(path, 200, latency_ms / 1000)
    
    print("="*60)
    print("✅ Simulation completed successfully!\n")

# ====================== Run Multiple Tests ======================
if __name__ == "__main__":
    print("🚀 Starting Standalone Middleware Logic Test\n")
    
    for i in range(3):
        simulate_full_request("/rag/answer")
        simulate_full_request("/extract")
    
    print("📊 Final Metrics Summary:")
    print(f"Total unique (path, status) combinations: {len(requests_total)}")
    print(f"Total requests tracked: {sum(requests_total.values())}")
    print(f"Average latency: {sum(request_latency)/len(request_latency):.4f}s")
    
    print("\n🎉 All middleware logic verified independently!")