"""End-to-end tests for metadata-capture HTTP endpoints and tool handlers.

Every test uses a fresh temporary SQLite database.  No real network calls are
made — the FastAPI app is exercised through httpx's ASGITransport.

Run from the repository root (metadata-capture/):
    python -m pytest evals/tasks/end_to_end/test_new_features.py -v
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_loop = asyncio.new_event_loop()


def _run(coro):
    """Drive a coroutine to completion on a persistent event loop."""
    return _loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def setup_db(tmp_path):
    """Reset the global DB connection and point it at a throwaway directory."""

    async def _setup():
        os.environ["METADATA_DB_DIR"] = str(tmp_path)
        import agent.db.database as db_mod

        db_mod._db_connection = None
        db_mod.DB_DIR = tmp_path
        db_mod.DB_PATH = tmp_path / "metadata.db"

        from agent.db.database import init_db
        await init_db()

    _run(_setup())
    yield

    async def _teardown():
        from agent.db.database import close_db
        await close_db()

    _run(_teardown())


@pytest.fixture()
def client(setup_db):
    """httpx AsyncClient bound to the FastAPI app via ASGI transport."""
    from agent.server import app

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://testserver")
    yield c
    _run(c.aclose())


# ---------------------------------------------------------------------------
# Test 1: Health endpoint
# ---------------------------------------------------------------------------


def test_health_returns_200_ok(client):
    resp = _run(client.get("/health"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Test 2: Sessions endpoint — empty database
# ---------------------------------------------------------------------------


def test_sessions_empty_when_no_conversations(client):
    resp = _run(client.get("/sessions"))
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Test 3: Session messages — nonexistent session returns empty list
# ---------------------------------------------------------------------------


def test_session_messages_returns_empty_for_unknown_session(client):
    resp = _run(client.get("/sessions/nonexistent-session-id/messages"))
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Test 4: Create record via capture_metadata_handler, then list via /records
# ---------------------------------------------------------------------------


def test_capture_creates_record_visible_in_list(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    session_id = "test-session-crud"
    result = _run(capture_metadata_handler({
        "session_id": session_id,
        "record_type": "subject",
        "data": {"subject_id": "12345", "sex": "Male"},
    }))
    text = result["content"][0]["text"]
    parsed = json.loads(text)
    assert parsed.get("action") == "created"

    resp = _run(client.get("/records"))
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    assert records[0]["record_type"] == "subject"
    assert records[0]["category"] == "shared"
    assert records[0]["data_json"]["subject_id"] == "12345"


# ---------------------------------------------------------------------------
# Test 5: PUT /records/{id} — update record data
# ---------------------------------------------------------------------------


def test_put_record_updates_data(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    session_id = "test-put-record"
    result = _run(capture_metadata_handler({
        "session_id": session_id,
        "record_type": "session",
        "data": {"session_start_time": "9:00 AM"},
    }))
    record_id = json.loads(result["content"][0]["text"])["record_id"]

    resp = _run(client.put(f"/records/{record_id}", json={"data": {"session_end_time": "5:00 PM"}}))
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["data_json"]["session_start_time"] == "9:00 AM"  # merged
    assert updated["data_json"]["session_end_time"] == "5:00 PM"


# ---------------------------------------------------------------------------
# Test 6: PUT /records/{id} — 404 for missing record
# ---------------------------------------------------------------------------


def test_put_record_returns_404_for_missing(client):
    resp = _run(client.put("/records/nonexistent-id", json={"data": {"foo": "bar"}}))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 7: Confirm record
# ---------------------------------------------------------------------------


def test_confirm_record_changes_status(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    session_id = "test-confirm"
    result = _run(capture_metadata_handler({
        "session_id": session_id,
        "record_type": "subject",
        "data": {"subject_id": "55555"},
    }))
    record_id = json.loads(result["content"][0]["text"])["record_id"]

    resp = _run(client.post(f"/records/{record_id}/confirm"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


# ---------------------------------------------------------------------------
# Test 8: Confirm — 404 for missing record
# ---------------------------------------------------------------------------


def test_confirm_returns_404_for_missing(client):
    resp = _run(client.post("/records/nonexistent/confirm"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 9: capture_metadata_handler — missing session_id
# ---------------------------------------------------------------------------


def test_capture_errors_without_session_id(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler

    result = _run(capture_metadata_handler({}))
    text = result["content"][0]["text"]
    assert "session_id" in text


# ---------------------------------------------------------------------------
# Test 10: capture_metadata_handler — missing record_type
# ---------------------------------------------------------------------------


def test_capture_errors_without_record_type(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler

    result = _run(capture_metadata_handler({"session_id": "s1"}))
    text = result["content"][0]["text"]
    assert "record_type" in text


# ---------------------------------------------------------------------------
# Test 11: capture_metadata_handler — missing data
# ---------------------------------------------------------------------------


def test_capture_errors_without_data(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler

    result = _run(capture_metadata_handler({"session_id": "s1", "record_type": "subject"}))
    text = result["content"][0]["text"]
    assert "data" in text.lower()


# ---------------------------------------------------------------------------
# Test 12: capture_metadata_handler — update existing record
# ---------------------------------------------------------------------------


def test_capture_updates_existing_record(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler
    from agent.tools.metadata_store import get_record

    session_id = "test-update"
    result1 = _run(capture_metadata_handler({
        "session_id": session_id,
        "record_type": "subject",
        "data": {"subject_id": "100"},
    }))
    record_id = json.loads(result1["content"][0]["text"])["record_id"]

    result2 = _run(capture_metadata_handler({
        "session_id": session_id,
        "record_type": "subject",
        "data": {"sex": "Female"},
        "record_id": record_id,
    }))
    parsed = json.loads(result2["content"][0]["text"])
    assert parsed["action"] == "updated"

    record = _run(get_record(record_id))
    assert record["data_json"]["subject_id"] == "100"
    assert record["data_json"]["sex"] == "Female"


# ---------------------------------------------------------------------------
# Test 13: Record linking via capture_metadata
# ---------------------------------------------------------------------------


def test_capture_with_link_to(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler
    from agent.tools.metadata_store import get_linked_records

    session_id = "test-link"

    # Create subject
    r1 = _run(capture_metadata_handler({
        "session_id": session_id,
        "record_type": "subject",
        "data": {"subject_id": "4528"},
    }))
    subject_id = json.loads(r1["content"][0]["text"])["record_id"]

    # Create session linked to subject
    r2 = _run(capture_metadata_handler({
        "session_id": session_id,
        "record_type": "session",
        "data": {"session_start_time": "2025-01-15T09:00:00"},
        "link_to": subject_id,
    }))
    session_record_id = json.loads(r2["content"][0]["text"])["record_id"]

    # Verify link
    links = _run(get_linked_records(session_record_id))
    assert len(links) == 1
    assert links[0]["id"] == subject_id


# ---------------------------------------------------------------------------
# Test 14: find_records tool
# ---------------------------------------------------------------------------


def test_find_records_by_type(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler, find_records_handler

    _run(capture_metadata_handler({
        "session_id": "s1",
        "record_type": "subject",
        "data": {"subject_id": "4528"},
    }))
    _run(capture_metadata_handler({
        "session_id": "s1",
        "record_type": "instrument",
        "data": {"instrument_id": "scope-1"},
    }))

    result = _run(find_records_handler({"record_type": "subject"}))
    parsed = json.loads(result["content"][0]["text"])
    assert parsed["count"] == 1
    assert parsed["records"][0]["record_type"] == "subject"


# ---------------------------------------------------------------------------
# Test 15: find_records with text query
# ---------------------------------------------------------------------------


def test_find_records_by_query(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler, find_records_handler

    _run(capture_metadata_handler({
        "session_id": "s1",
        "record_type": "subject",
        "data": {"subject_id": "4528"},
        "name": "Mouse 4528",
    }))

    result = _run(find_records_handler({"query": "4528"}))
    parsed = json.loads(result["content"][0]["text"])
    assert parsed["count"] >= 1


# ---------------------------------------------------------------------------
# Test 16: link_records tool
# ---------------------------------------------------------------------------


def test_link_records_tool(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler, link_records_handler
    from agent.tools.metadata_store import get_linked_records

    r1 = _run(capture_metadata_handler({
        "session_id": "s1",
        "record_type": "subject",
        "data": {"subject_id": "100"},
    }))
    r2 = _run(capture_metadata_handler({
        "session_id": "s1",
        "record_type": "rig",
        "data": {"rig_id": "rig-A"},
    }))
    id1 = json.loads(r1["content"][0]["text"])["record_id"]
    id2 = json.loads(r2["content"][0]["text"])["record_id"]

    result = _run(link_records_handler({"source_id": id1, "target_id": id2}))
    parsed = json.loads(result["content"][0]["text"])
    assert "Linked" in parsed["message"]

    links = _run(get_linked_records(id1))
    assert any(l["id"] == id2 for l in links)


# ---------------------------------------------------------------------------
# Test 17: GET /records with type filter
# ---------------------------------------------------------------------------


def test_records_filter_by_type(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    _run(capture_metadata_handler({"session_id": "s1", "record_type": "subject", "data": {"subject_id": "111"}}))
    _run(capture_metadata_handler({"session_id": "s1", "record_type": "instrument", "data": {"instrument_id": "i1"}}))

    resp = _run(client.get("/records?type=subject"))
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    assert records[0]["record_type"] == "subject"


# ---------------------------------------------------------------------------
# Test 18: GET /records with category filter
# ---------------------------------------------------------------------------


def test_records_filter_by_category(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    _run(capture_metadata_handler({"session_id": "s1", "record_type": "subject", "data": {"subject_id": "111"}}))
    _run(capture_metadata_handler({"session_id": "s1", "record_type": "data_description", "data": {"project_name": "P1"}}))

    resp = _run(client.get("/records?category=shared"))
    assert resp.status_code == 200
    assert all(r["category"] == "shared" for r in resp.json())

    resp = _run(client.get("/records?category=asset"))
    assert resp.status_code == 200
    assert all(r["category"] == "asset" for r in resp.json())


# ---------------------------------------------------------------------------
# Test 19: GET /sessions/{id}/records
# ---------------------------------------------------------------------------


def test_session_records_endpoint(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    _run(capture_metadata_handler({"session_id": "s1", "record_type": "subject", "data": {"subject_id": "111"}}))
    _run(capture_metadata_handler({"session_id": "s2", "record_type": "subject", "data": {"subject_id": "222"}}))

    resp = _run(client.get("/sessions/s1/records"))
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    assert records[0]["session_id"] == "s1"


# ---------------------------------------------------------------------------
# Test 20: GET /records/{id} includes links
# ---------------------------------------------------------------------------


def test_get_record_includes_links(client):
    from agent.tools.capture_mcp import capture_metadata_handler
    from agent.tools.metadata_store import link_records

    r1 = _run(capture_metadata_handler({"session_id": "s1", "record_type": "subject", "data": {"subject_id": "100"}}))
    r2 = _run(capture_metadata_handler({"session_id": "s1", "record_type": "session", "data": {"session_start_time": "9AM"}}))
    id1 = json.loads(r1["content"][0]["text"])["record_id"]
    id2 = json.loads(r2["content"][0]["text"])["record_id"]
    _run(link_records(id1, id2))

    resp = _run(client.get(f"/records/{id1}"))
    assert resp.status_code == 200
    record = resp.json()
    assert "links" in record
    assert len(record["links"]) == 1
    assert record["links"][0]["id"] == id2


# ---------------------------------------------------------------------------
# Test 21: DELETE /sessions/{id} removes records and conversations
# ---------------------------------------------------------------------------


def test_delete_session_removes_all_data(client):
    from agent.tools.capture_mcp import capture_metadata_handler
    from agent.tools.metadata_store import save_conversation_turn

    session_id = "test-delete"
    _run(capture_metadata_handler({"session_id": session_id, "record_type": "subject", "data": {"subject_id": "777"}}))
    _run(save_conversation_turn(session_id, "user", "hello"))

    resp = _run(client.delete(f"/sessions/{session_id}"))
    assert resp.status_code == 200

    resp = _run(client.get(f"/sessions/{session_id}/records"))
    assert resp.json() == []
    resp = _run(client.get(f"/sessions/{session_id}/messages"))
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Test 22: DELETE /sessions/{id} — 404 for missing
# ---------------------------------------------------------------------------


def test_delete_session_returns_404_for_missing(client):
    resp = _run(client.delete("/sessions/nonexistent-session"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 23: Validation auto-stored after capture
# ---------------------------------------------------------------------------


def test_capture_stores_validation_results(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler
    from agent.tools.metadata_store import get_record

    result = _run(capture_metadata_handler({
        "session_id": "s1",
        "record_type": "subject",
        "data": {"subject_id": "12345"},
    }))
    record_id = json.loads(result["content"][0]["text"])["record_id"]

    record = _run(get_record(record_id))
    val = record.get("validation_json")
    assert val is not None
    assert isinstance(val, dict)
    assert "status" in val
    assert "completeness_score" in val
    assert val["record_type"] == "subject"


# ---------------------------------------------------------------------------
# Test 24: Records sorted by created_at DESC
# ---------------------------------------------------------------------------


def test_records_sorted_by_created_at_desc(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    _run(capture_metadata_handler({"session_id": "s1", "record_type": "subject", "data": {"subject_id": "001"}}))
    time.sleep(0.05)
    _run(capture_metadata_handler({"session_id": "s2", "record_type": "subject", "data": {"subject_id": "002"}}))

    resp = _run(client.get("/records"))
    ids = [r["data_json"]["subject_id"] for r in resp.json() if r["record_type"] == "subject"]
    assert ids == ["002", "001"]


# ---------------------------------------------------------------------------
# Test 25: Auto-naming from record data
# ---------------------------------------------------------------------------


def test_record_auto_naming(setup_db):
    from agent.tools.capture_mcp import capture_metadata_handler
    from agent.tools.metadata_store import get_record

    result = _run(capture_metadata_handler({
        "session_id": "s1",
        "record_type": "subject",
        "data": {"subject_id": "4528", "species": {"name": "Mus musculus"}},
    }))
    record_id = json.loads(result["content"][0]["text"])["record_id"]
    record = _run(get_record(record_id))
    assert record["name"] == "Mus musculus 4528"


# ---------------------------------------------------------------------------
# Test 26: GET /sessions — first_message populated
# ---------------------------------------------------------------------------


def test_sessions_first_message(client):
    from agent.tools.metadata_store import save_conversation_turn

    session_id = "test-session-titles"
    _run(save_conversation_turn(session_id, "user", "I want to log a mouse experiment"))
    _run(save_conversation_turn(session_id, "assistant", "Sure, let's start."))

    resp = _run(client.get("/sessions"))
    sessions = resp.json()
    assert len(sessions) == 1
    assert sessions[0]["first_message"] == "I want to log a mouse experiment"
    assert sessions[0]["message_count"] == 2


# ---------------------------------------------------------------------------
# Test 27: Models endpoint
# ---------------------------------------------------------------------------


def test_models_endpoint(client):
    resp = _run(client.get("/models"))
    assert resp.status_code == 200
    body = resp.json()
    assert "models" in body
    assert "default" in body
    assert "claude-opus-4-6" in body["models"]


# ---------------------------------------------------------------------------
# Test 28: DELETE /records/{id}
# ---------------------------------------------------------------------------


def test_delete_record(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    result = _run(capture_metadata_handler({"session_id": "s1", "record_type": "rig", "data": {"rig_id": "r1"}}))
    record_id = json.loads(result["content"][0]["text"])["record_id"]

    resp = _run(client.delete(f"/records/{record_id}"))
    assert resp.status_code == 200

    resp = _run(client.get(f"/records/{record_id}"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 29: POST /records/link via HTTP
# ---------------------------------------------------------------------------


def test_link_records_http(client):
    from agent.tools.capture_mcp import capture_metadata_handler

    r1 = _run(capture_metadata_handler({"session_id": "s1", "record_type": "subject", "data": {"subject_id": "100"}}))
    r2 = _run(capture_metadata_handler({"session_id": "s1", "record_type": "session", "data": {"session_start_time": "9AM"}}))
    id1 = json.loads(r1["content"][0]["text"])["record_id"]
    id2 = json.loads(r2["content"][0]["text"])["record_id"]

    resp = _run(client.post("/records/link", json={"source_id": id1, "target_id": id2}))
    assert resp.status_code == 200

    resp = _run(client.get(f"/records/{id1}/links"))
    assert resp.status_code == 200
    assert any(l["id"] == id2 for l in resp.json())
