"""RAG smoke evaluator -- 3 pre-shipped questions, binary PASS/FAIL per question.

The Lab smoke evaluator proves the grounding-check logic that the Integration's
RAG grounding-rate harness scales up. It is binary by design (PASS/FAIL per
question, exit 0 iff all PASS); it does not aggregate a rate, and it does NOT
apply decline-exclusion -- the three smoke questions are all answerable
against the seeded fixtures, so a decline at the Lab tier is a defect.

Grounding-check methodology (Lab smoke):

  A response is grounded iff (a) response.citations has length >= 1 AND
  (b) every chunk_id in response.citations is present in the candidate set
  returned by the retrieval call for the same question. The Lab smoke does
  NOT apply decline-exclusion (the Lab's 3 questions are all answerable
  against the seeded Weaviate; decline is not in scope at the Lab tier).

The same paragraph appears in the published Applied Lab page so the
documented methodology and the code that scores against it stay in sync.
"""

import json
import os
import sys

import httpx

API_URL = os.environ.get("API_URL", "http://localhost:8000")


def score_grounding(response: dict, candidate_ids) -> bool:
    """Return True iff `response` is grounded per the Lab smoke methodology.

    `response` is the JSON body returned by POST /rag/answer.
    `candidate_ids` is the set of chunk_ids returned for the same question.
    
    Citations are objects with "chunk_id" field; we extract the chunk_id
    from each citation and check if it exists in the candidate set.
    """
    citations = response.get("citations", [])
    if len(citations) < 1:
        return False
    # Each citation is a dict with "chunk_id" key; extract and check membership
    return all(citation.get("chunk_id") in candidate_ids for citation in citations)


def evaluate_question(question: dict) -> bool:
    """Issue one POST /rag/answer; return True iff the response is grounded."""
    response = httpx.post(
        f"{API_URL}/rag/answer",
        json={"question": question["question"], "k": question.get("k", 4)},
        timeout=60.0,
    )
    response.raise_for_status()
    response_body = response.json()

    candidate_ids = {chunk["chunk_id"] for chunk in response_body["retrieved"]}

    return score_grounding(response_body, candidate_ids)


def main() -> int:
    """Iterate the three smoke questions, print PASS/FAIL, return 0 iff all PASS."""
    fixture_path = os.path.join(os.path.dirname(__file__), "data", "rag_smoke.json")
    with open(fixture_path) as fh:
        questions = json.load(fh)

    all_passed = True
    for question in questions:
        grounded = evaluate_question(question)
        status = "PASS" if grounded else "FAIL"
        print(f"{question['question_id']}: {status}")
        if not grounded:
            all_passed = False

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())