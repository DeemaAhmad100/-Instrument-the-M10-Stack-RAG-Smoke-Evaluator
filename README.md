# Module 11 — Applied Lab: Service Monitoring

Add a full observability layer to the M10 backend — three Prometheus metric
families (counter, histogram, gauge), three middleware layers (request-id,
structured logging, metrics), and a `/metrics` endpoint mounted via
`prometheus_client.make_asgi_app()`. Verify end-to-end with a 3-question RAG
smoke evaluator.

The published Applied Lab guide is the canonical task list. See
TalentLMS → Module 11 → Applied Lab for the link, or check your cohort's
Slack pinned message.

## What ships here

```
.
├── api/
│   ├── main.py                      vendored M10 surface; TODO: middleware wires + /metrics mount
│   ├── observability.py             TODO: metric declarations + middlewares
│   ├── models.py                    vendored M10 Pydantic models (reference)
│   ├── rag.py                       vendored M10 RAG composer (reference)
│   ├── kg.py                        vendored M10 KG mapper wrapper (reference)
│   ├── ner.py                       vendored M10 NER wrapper (reference)
│   ├── Dockerfile                   vendored M10 backend Dockerfile
│   └── __init__.py
├── web/                             vendored M10 Next.js client (not graded this module)
├── eval_rag_smoke.py                TODO: 3-question smoke evaluator
├── data/
│   └── rag_smoke.json               3 pre-shipped questions
├── tests/
│   ├── test_metrics_endpoint.py     autograder
│   ├── test_middlewares.py          autograder
│   ├── test_smoke_evaluator.py      autograder
│   ├── test_learner_test_complete.py autograder (AST check)
│   ├── test_observability.py        YOUR tests go here
│   └── conftest.py
├── docker-compose.yml               M10 four-service stack
├── seed_neo4j.sh                    vendored M10 seed (idempotent)
├── seed_weaviate.sh                 vendored M10 seed (idempotent)
├── requirements.txt
├── .env.example
├── .gitignore
├── LICENSE
└── README.md
```

## Setup

Use **Python 3.11** for this template (the pinned `pydantic==2.6.0` does not build on Python 3.13).

```bash
git checkout -b lab-11-service-monitoring
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
cp .env.example .env
```

The `spacy download` step is needed only if you plan to run `uvicorn` directly on your host (for example, to debug). The Docker image installs the model from a pinned wheel, so `docker compose up -d` does not need this step. Without the model, `/extract` silently returns a stub.

The `lab-11-service-monitoring` branch is what the autograder workflow runs against and what you push your PR from.

Edit `.env` to set your Neo4j password and Weaviate URL (the values from
your Module 10 deliverable).

## Bring up the M10 stack

```bash
docker compose up -d
curl http://localhost:8000/readyz
```

A 200 means `api` is up and connected to Neo4j + Weaviate; if you see
anything else, give the stack 60 seconds for cold starts and try again
before debugging.

Seed the stores (idempotent):

```bash
bash seed_neo4j.sh
bash seed_weaviate.sh
```

## Run the autograder locally

```bash
pytest tests/ -v
```

On the unmodified starter, the autograder will FAIL (by design — your TODOs
are unimplemented). Implement `api/observability.py`, wire the three
middlewares + mount `/metrics` in `api/main.py`, and implement
`eval_rag_smoke.py`; then re-run.

## Tear down

```bash
docker compose down -v
```

## Observability

The M11 backend exports three Prometheus metric families via the `/metrics` endpoint. `requests_total` is a counter that tracks the total number of HTTP requests by path and status code, providing visibility into traffic patterns and error rates. `request_latency_seconds` is a histogram that captures request latency in seconds across all paths using Prometheus's default latency buckets (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0), chosen because they appropriately capture the typical sub-second and low-second latency profile of the M10 stack. `inflight_requests` is a gauge that tracks the number of requests currently being processed, enabling monitoring of concurrency. To read the metrics, issue `curl -L http://localhost:8000/metrics` and parse the output—metrics are emitted in OpenMetrics text format with `# HELP` and `# TYPE` lines followed by metric samples.

## Submission

Open a PR within your fork. The PR description must include:

1. Confirmation that `docker compose up -d` brings up the stack and `/readyz` returns 200.
2. Confirmation that `python eval_rag_smoke.py` exits 0.
3. A short paragraph (~100 words) describing one design decision you made.
4. Paste your PR URL into TalentLMS → Module 11 → Lab 11 to submit this assignment.

---

## License

This repository is provided for educational use only. See [LICENSE](LICENSE) for terms.

You may clone and modify this repository for personal learning and practice, and reference code you wrote here in your professional portfolio. Redistribution outside this course is not permitted.
