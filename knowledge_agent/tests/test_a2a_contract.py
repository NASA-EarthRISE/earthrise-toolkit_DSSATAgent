"""
A2A endpoint contract tests for knowledge_agent.

Same rationale as data_agent/tests/test_a2a_contract.py — snapshots
the public A2A HTTP behavior (the observable JSON-RPC 2.0 envelope
shape and skill enumeration) that this endpoint must keep.

Focused set:
  1. Agent card endpoint returns 200 with the documented skill list.
  2. Health endpoint returns 200 or 503 (both are valid per the code);
     just verify it responds with a status field.
  3. RPC endpoint rejects malformed JSON with a -32700 parse error.
  4. RPC endpoint rejects unknown methods with -32601.
  5. RPC endpoint rejects missing skill with -32602.
  6. RPC endpoint accepts ``list_strategies`` and returns an
     artifacts-wrapped result.

``list_strategies`` is the safest skill to hit — it enumerates
registered retrieval strategies, requires no live corpus, and touches
only in-memory state.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client


AGENT_CARD_URL = "/knowledge_agent/.well-known/agent-card.json"
RPC_URL = "/knowledge_agent/"
HEALTH_URL = "/knowledge_agent/health"


@pytest.fixture
def client():
    return Client()


@pytest.mark.a2a_contract
class TestAgentCard:

    def test_returns_200(self, client):
        response = client.get(AGENT_CARD_URL)
        assert response.status_code == 200

    def test_contains_documented_top_level_fields(self, client):
        response = client.get(AGENT_CARD_URL)
        card = response.json()
        assert "name" in card
        assert "description" in card
        assert "skills" in card
        assert isinstance(card["skills"], list)

    def test_advertises_all_documented_skills(self, client):
        expected_skills = {
            "retrieve_knowledge",
            "list_strategies",
            "check_readiness",
        }
        response = client.get(AGENT_CARD_URL)
        card = response.json()
        actual_skills = {s["id"] for s in card["skills"]}
        assert expected_skills.issubset(actual_skills), (
            f"Missing skills: {expected_skills - actual_skills}"
        )


@pytest.mark.a2a_contract
class TestHealth:
    def test_responds_with_status_field(self, client):
        response = client.get(HEALTH_URL)
        # 200 (healthy) or 503 (store unreachable) — both valid;
        # what matters is the shape.
        assert response.status_code in (200, 503)
        assert "status" in response.json()


@pytest.mark.a2a_contract
class TestRPCErrors:

    def _post_json(self, client, body):
        return client.post(
            RPC_URL,
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_malformed_body_returns_parse_error(self, client):
        response = client.post(
            RPC_URL,
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 200
        err = response.json()["error"]
        assert err["code"] == -32700

    def test_unknown_method_returns_method_not_found(self, client):
        response = self._post_json(client, {
            "jsonrpc": "2.0",
            "id": "test-1",
            "method": "not/a/real/method",
            "params": {},
        })
        assert response.status_code == 200
        err = response.json()["error"]
        assert err["code"] == -32601

    def test_missing_skill_returns_invalid_params(self, client):
        response = self._post_json(client, {
            "jsonrpc": "2.0",
            "id": "test-2",
            "method": "message/send",
            "params": {"message": {"parts": []}},
        })
        assert response.status_code == 200
        err = response.json()["error"]
        assert err["code"] == -32602


@pytest.mark.a2a_contract
@pytest.mark.django_db
class TestListStrategiesRoundTrip:
    """Successful skill dispatch envelope shape.

    ``list_strategies`` enumerates the registered retrieval strategies
    without touching the vector store — safe for a fresh test DB.
    """

    def test_returns_jsonrpc_result_envelope(self, client):
        response = client.post(
            RPC_URL,
            data=json.dumps({
                "jsonrpc": "2.0",
                "id": "rt-1",
                "method": "message/send",
                "params": {
                    "message": {
                        "parts": [
                            {"type": "data", "data": {"skill": "list_strategies", "params": {}}}
                        ]
                    }
                },
            }),
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body.get("jsonrpc") == "2.0"
        assert body.get("id") == "rt-1"
        assert "result" in body
        assert "artifacts" in body["result"]
        assert isinstance(body["result"]["artifacts"], list)
        artifact = body["result"]["artifacts"][0]
        assert "parts" in artifact
        data_parts = [p for p in artifact["parts"] if p.get("type") == "data"]
        assert data_parts, "Expected at least one 'data' part in artifact"
