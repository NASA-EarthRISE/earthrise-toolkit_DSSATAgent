"""
A2A endpoint contract tests for data_agent.

These snapshot the public A2A HTTP behavior this endpoint must keep.
The response bytes may differ, but the observable JSON-RPC 2.0
envelope shape and skill enumeration must not.

Focused set:
  1. Agent card endpoint returns 200 with the documented skill list.
  2. Health endpoint returns 200.
  3. RPC endpoint rejects malformed JSON with a -32700 parse error.
  4. RPC endpoint rejects unknown methods with -32601.
  5. RPC endpoint rejects missing skill with -32602.
  6. RPC endpoint accepts ``list_sources`` and returns an artifacts-wrapped result.

The test uses Django's test client — no live server required, no
external network, no LLM. It runs against the SQLite fallback the
Django settings use when no ``DBHOST`` is set.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client


AGENT_CARD_URL = "/data_agent/.well-known/agent-card.json"
RPC_URL = "/data_agent/"
HEALTH_URL = "/data_agent/health"


@pytest.fixture
def client():
    return Client()


@pytest.mark.a2a_contract
class TestAgentCard:
    """The agent card is the machine-readable manifest — its shape is
    part of the A2A contract."""

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
        """These skill ids are the A2A contract. Renaming or removing
        one is a breaking change for every A2A consumer."""
        expected_skills = {
            "fetch_data",
            "check_availability",
            "query_time_series",
            "list_sources",
            "check_remote",
        }
        response = client.get(AGENT_CARD_URL)
        card = response.json()
        actual_skills = {s["id"] for s in card["skills"]}
        assert expected_skills.issubset(actual_skills), (
            f"Missing skills: {expected_skills - actual_skills}"
        )


@pytest.mark.a2a_contract
class TestHealth:
    def test_returns_200(self, client):
        response = client.get(HEALTH_URL)
        assert response.status_code == 200

    def test_returns_status_ok(self, client):
        response = client.get(HEALTH_URL)
        assert response.json().get("status") == "ok"


@pytest.mark.a2a_contract
class TestRPCErrors:
    """JSON-RPC 2.0 error codes are part of the A2A contract per the spec."""

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
class TestListSourcesRoundTrip:
    """Successful skill dispatch envelope shape.

    ``list_sources`` chosen because it's the safest DB-touching skill —
    no external services, no computation, just a SELECT from the
    registered-source table. In an empty test DB it returns [].
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
                            {"type": "data", "data": {"skill": "list_sources", "params": {}}}
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
        # Contract shape: result → artifacts[] → parts[] → {type: data, data: ...}
        assert "result" in body
        assert "artifacts" in body["result"]
        assert isinstance(body["result"]["artifacts"], list)
        # First artifact must have parts with a data part
        artifact = body["result"]["artifacts"][0]
        assert "parts" in artifact
        data_parts = [p for p in artifact["parts"] if p.get("type") == "data"]
        assert data_parts, "Expected at least one 'data' part in artifact"
