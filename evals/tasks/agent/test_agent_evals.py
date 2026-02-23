"""End-to-end agent evals: send prompts to the live agent, grade DB outcome + transcript.

These tests hit the real /chat SSE endpoint (requires ANTHROPIC_API_KEY) and then
check both the database state (deterministic) and the response text (LLM-graded).

Run:
    python -m pytest evals/tasks/agent/ -v -m llm
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from evals.graders.llm_judge import grade_conversation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_loop = asyncio.new_event_loop()


def _run(coro):
    """Drive a coroutine to completion on a persistent event loop."""
    return _loop.run_until_complete(coro)


async def _send_chat(
    client: AsyncClient,
    message: str,
    session_id: str = "eval-session",
) -> str:
    """POST to /chat, consume the SSE stream, return the full response text."""
    resp = await client.post(
        "/chat",
        json={"message": message, "session_id": session_id},
        timeout=120.0,
    )
    assert resp.status_code == 200

    full_text = ""
    async for line in resp.aiter_lines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        # SSE chunks are {"content": "..."} for text tokens
        if "content" in chunk:
            full_text += chunk["content"]

    return full_text


async def _get_records(
    client: AsyncClient,
    record_type: str | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """GET /records with optional filters."""
    params: dict[str, str] = {}
    if record_type:
        params["type"] = record_type
    if session_id:
        params["session_id"] = session_id
    resp = await client.get("/records", params=params)
    assert resp.status_code == 200
    return resp.json()


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
def client(setup_db):  # noqa: ARG001 — setup_db side-effects needed
    """httpx AsyncClient bound to the FastAPI app via ASGI transport."""
    from agent.server import app

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://testserver")
    yield c
    _run(c.aclose())


# ---------------------------------------------------------------------------
# Shared grading helper
# ---------------------------------------------------------------------------

PASS_THRESHOLD = 3.5


def _grade(response_text: str, user_msg: str, rubric: dict[str, str]) -> None:
    """Build a transcript and grade it; assert the result passes."""
    transcript = f"USER: {user_msg}\nASSISTANT: {response_text}"
    result = grade_conversation(
        transcript=transcript,
        rubric=rubric,
        pass_threshold=PASS_THRESHOLD,
    )
    for dim, score in result["scores"].items():
        reasoning = result["reasoning"].get(dim, "")
        print(f"  {dim}: {score}/5 — {reasoning}")
    assert result["passed"], (
        f"Agent eval failed with avg score {result['avg_score']:.2f} "
        f"(threshold {PASS_THRESHOLD}). Scores: {result['scores']}"
    )


# ===========================================================================
# Schema validation feedback tests
# ===========================================================================


@pytest.mark.llm
@pytest.mark.network
def test_sex_unknown_flagged(client):
    """Agent should create a subject record and flag that 'unknown' is not a valid sex value."""
    prompt = "Subject 887766, sex is unknown"
    response = _run(_send_chat(client, prompt))

    # Deterministic: a subject record should exist
    records = _run(_get_records(client, record_type="subject"))
    assert len(records) >= 1, f"Expected at least 1 subject record, got {len(records)}"
    subject = records[0]
    data = subject.get("data_json") or {}
    if isinstance(data, str):
        data = json.loads(data)
    assert data.get("subject_id") == "887766", f"Subject ID mismatch: {data}"

    # LLM: agent should flag the validation issue and suggest valid options
    _grade(response, prompt, {
        "validation_flagging": (
            "Did the agent flag that 'unknown' is not a valid sex value? "
            "Did it suggest valid options like Male or Female?"
        ),
        "record_creation": (
            "Did the agent confirm it created/captured metadata for subject 887766?"
        ),
    })


@pytest.mark.llm
@pytest.mark.network
def test_stale_modality_mapped(client):
    """Agent should map 'SLAP imaging' to the correct modality abbreviation."""
    prompt = "We did a SLAP imaging session on mouse 654321"
    response = _run(_send_chat(client, prompt))

    # Deterministic: records should exist (subject and/or data_description)
    all_records = _run(_get_records(client))
    assert len(all_records) >= 1, f"Expected at least 1 record, got {len(all_records)}"

    # LLM: agent should use the correct modality
    _grade(response, prompt, {
        "modality_mapping": (
            "Did the agent correctly identify or map the SLAP imaging modality? "
            "SLAP2 or 'slap2' is the correct abbreviation for SLAP imaging."
        ),
        "metadata_capture": (
            "Did the agent capture the subject ID (654321) and session information?"
        ),
    })


@pytest.mark.llm
@pytest.mark.network
def test_invalid_modality_flagged(client):
    """Agent should flag 'xray' as an invalid modality."""
    prompt = "Data description, modality is xray, project TestProject"
    response = _run(_send_chat(client, prompt))

    # Deterministic: a data_description record should exist
    records = _run(_get_records(client, record_type="data_description"))
    assert len(records) >= 1, f"Expected at least 1 data_description, got {len(records)}"

    # LLM: agent should flag the invalid modality
    _grade(response, prompt, {
        "validation_flagging": (
            "Did the agent flag that 'xray' is not a valid modality? "
            "Did it suggest valid modality options from the AIND schema?"
        ),
        "record_creation": (
            "Did the agent confirm it captured the project name (TestProject) "
            "and data description metadata?"
        ),
    })


@pytest.mark.llm
@pytest.mark.network
def test_clean_validation_no_mention(client):
    """Agent should NOT mention validation issues when everything is valid."""
    prompt = "Subject 445566 is a female Mus musculus"
    response = _run(_send_chat(client, prompt))

    # Deterministic: a subject record should exist with correct data
    records = _run(_get_records(client, record_type="subject"))
    assert len(records) >= 1, f"Expected at least 1 subject record, got {len(records)}"
    subject = records[0]
    data = subject.get("data_json") or {}
    if isinstance(data, str):
        data = json.loads(data)
    assert data.get("subject_id") == "445566", f"Subject ID mismatch: {data}"

    # LLM: agent should NOT draw attention to validation issues
    _grade(response, prompt, {
        "no_false_alarms": (
            "The agent should NOT mention validation errors or warnings, because "
            "all fields are valid (Female is a valid sex, Mus musculus is a valid species). "
            "Did the agent avoid raising unnecessary validation concerns?"
        ),
        "confirmation": (
            "Did the agent confirm it captured subject 445566 as a female Mus musculus?"
        ),
    })


# ===========================================================================
# Registry lookup tests
# ===========================================================================


@pytest.mark.llm
@pytest.mark.network
def test_genotype_registry_lookup(client):
    """Agent should perform MGI/NCBI lookups for genotype alleles."""
    prompt = "Subject 556677, female mouse, genotype Ai14;Slc17a7-Cre"
    response = _run(_send_chat(client, prompt))

    # Deterministic: subject record should exist
    records = _run(_get_records(client, record_type="subject"))
    assert len(records) >= 1, f"Expected at least 1 subject record, got {len(records)}"

    # LLM: agent should mention registry lookup results
    _grade(response, prompt, {
        "registry_results": (
            "Did the agent mention results from external registry lookups "
            "(MGI or NCBI Gene) for the genotype alleles Ai14 and Slc17a7-Cre? "
            "The agent should share registry information about these alleles."
        ),
        "metadata_capture": (
            "Did the agent capture subject 556677 as a female mouse with the genotype?"
        ),
    })


@pytest.mark.llm
@pytest.mark.network
def test_plasmid_registry_lookup(client):
    """Agent should perform Addgene lookup for injection plasmids."""
    prompt = "Injected pAAV-EF1a-DIO-hChR2 into VISp of mouse 100001"
    response = _run(_send_chat(client, prompt))

    # Deterministic: at least one record should exist (procedures or subject)
    all_records = _run(_get_records(client))
    assert len(all_records) >= 1, f"Expected at least 1 record, got {len(all_records)}"

    # LLM: agent should mention Addgene lookup results
    _grade(response, prompt, {
        "registry_results": (
            "Did the agent mention results from Addgene for the plasmid "
            "pAAV-EF1a-DIO-hChR2? The agent should share registry information "
            "such as catalog number, description, or a link."
        ),
        "metadata_capture": (
            "Did the agent capture the injection procedure details including "
            "the target area (VISp) and subject (100001)?"
        ),
    })


@pytest.mark.llm
@pytest.mark.network
def test_no_registry_for_session(client):
    """Agent should NOT perform registry lookups for plain session metadata."""
    prompt = "Session started at 9am, ended at 11am, rig EPHYS-01"
    response = _run(_send_chat(client, prompt))

    # Deterministic: at least one record should exist
    all_records = _run(_get_records(client))
    assert len(all_records) >= 1, f"Expected at least 1 record, got {len(all_records)}"

    # LLM: agent should NOT mention registries
    _grade(response, prompt, {
        "no_registry_mentions": (
            "The agent should NOT mention Addgene, MGI, NCBI, or any external "
            "registry lookups because there are no genotypes or plasmids in this "
            "message. Did the agent avoid irrelevant registry mentions?"
        ),
        "session_capture": (
            "Did the agent capture the session times (9am start, 11am end) "
            "and rig ID (EPHYS-01)?"
        ),
    })


# ===========================================================================
# Context-aware behavior tests
# ===========================================================================


@pytest.mark.llm
@pytest.mark.network
def test_modality_creates_data_description(client):
    """A modality mention should create a data_description record, not just a session."""
    prompt = "We collected ecephys data on mouse 123. Project BrainMap"
    response = _run(_send_chat(client, prompt))

    # Deterministic: a data_description record should exist
    records = _run(_get_records(client, record_type="data_description"))
    assert len(records) >= 1, (
        f"Expected at least 1 data_description record, got {len(records)}. "
        f"All records: {_run(_get_records(client))}"
    )

    # LLM: agent should confirm data description capture
    _grade(response, prompt, {
        "correct_record_type": (
            "Did the agent create a data_description record (not just a session "
            "or subject) that includes the modality (ecephys) and project (BrainMap)?"
        ),
        "completeness": (
            "Did the agent capture the subject ID (123) and ask about any "
            "missing metadata fields?"
        ),
    })


@pytest.mark.llm
@pytest.mark.network
def test_reuses_existing_subject(client):
    """Second message about same subject should reuse the existing record, not duplicate."""
    sid = "eval-reuse-session"

    # First message: create a subject
    _run(_send_chat(client, "Subject 998877, female mouse", session_id=sid))
    records_after_first = _run(_get_records(client, record_type="subject"))
    assert len(records_after_first) >= 1, "First message should create a subject"

    # Second message: mention the same subject with additional info
    resp2 = _run(_send_chat(
        client,
        "Actually, subject 998877 is male, not female. Also it's a C57BL/6J strain.",
        session_id=sid,
    ))
    records_after_second = _run(_get_records(client, record_type="subject"))

    # Should still be 1 subject, not 2 (reused, not duplicated)
    subject_ids = [
        (r.get("data_json") or {}).get("subject_id")
        if isinstance(r.get("data_json"), dict)
        else json.loads(r.get("data_json") or "{}").get("subject_id")
        for r in records_after_second
    ]
    count_998877 = sum(1 for sid_val in subject_ids if sid_val == "998877")
    assert count_998877 == 1, (
        f"Expected exactly 1 record for subject 998877, got {count_998877}. "
        f"All subject records: {records_after_second}"
    )

    # LLM: agent should confirm it updated the existing record
    _grade(resp2, "Subject 998877 is male, not female. Also it's a C57BL/6J strain.", {
        "record_reuse": (
            "Did the agent update the existing subject record for 998877 rather "
            "than creating a new duplicate? It should indicate it found/updated "
            "the existing record."
        ),
        "data_update": (
            "Did the agent update the sex to Male and add the strain C57BL/6J?"
        ),
    })


@pytest.mark.llm
@pytest.mark.network
def test_unknown_fields_warned(client):
    """Agent should flag fields not in the AIND schema."""
    prompt = "Subject 887766, favorite color is blue, nickname is Squeaky"
    response = _run(_send_chat(client, prompt))

    # Deterministic: subject record should exist
    records = _run(_get_records(client, record_type="subject"))
    assert len(records) >= 1, f"Expected at least 1 subject record, got {len(records)}"

    # LLM: agent should flag the unknown fields
    _grade(response, prompt, {
        "unknown_field_flagging": (
            "Did the agent flag that 'favorite_color' and 'nickname' are not "
            "standard fields in the AIND subject schema? The agent should warn "
            "about unknown or non-standard fields."
        ),
        "subject_capture": (
            "Did the agent still capture the subject ID (887766) despite the "
            "unknown fields?"
        ),
    })


# ===========================================================================
# Species NCBI taxonomy enrichment tests
# ===========================================================================


@pytest.mark.llm
@pytest.mark.network
def test_species_ncbi_enrichment(client):
    """Subject record should include NCBI taxonomy ID for known species."""
    prompt = "I have a new mouse, subject ID 334455"
    response = _run(_send_chat(client, prompt))

    # Deterministic: subject record should exist with species + NCBI taxonomy ID
    records = _run(_get_records(client, record_type="subject"))
    assert len(records) >= 1, f"Expected at least 1 subject record, got {len(records)}"
    subject = records[0]
    data = subject.get("data_json") or {}
    if isinstance(data, str):
        data = json.loads(data)
    assert data.get("subject_id") == "334455", f"Subject ID mismatch: {data}"

    species = data.get("species")
    assert isinstance(species, dict), f"Species should be a dict, got: {species}"
    assert species.get("name") == "Mus musculus", f"Species name mismatch: {species}"
    assert species.get("registry_identifier") == "NCBI:txid10090", (
        f"Missing or wrong NCBI taxonomy ID. Species data: {species}"
    )

    # LLM: agent should mention the species with its taxonomy info
    _grade(response, prompt, {
        "species_identification": (
            "Did the agent correctly identify the species as Mus musculus for a mouse?"
        ),
        "ncbi_taxonomy": (
            "Did the agent mention or include the NCBI taxonomy identifier "
            "(NCBI:txid10090) for Mus musculus? The species record should include "
            "the NCBI registry information."
        ),
    })


# ===========================================================================
# Instrument capture from text description
# ===========================================================================


@pytest.mark.llm
@pytest.mark.network
def test_instrument_capture_from_description(client):
    """Agent should create an instrument record when given device details."""
    prompt = (
        "We're using a Neuropixels 2.0 probe manufactured by IMEC, "
        "serial number NP2-20240315-001"
    )
    response = _run(_send_chat(client, prompt))

    # Deterministic: instrument record should exist with instrument_id
    records = _run(_get_records(client, record_type="instrument"))
    assert len(records) >= 1, f"Expected at least 1 instrument record, got {len(records)}"
    instr = records[0]
    data = instr.get("data_json") or {}
    if isinstance(data, str):
        data = json.loads(data)
    assert data.get("instrument_id"), f"instrument_id should be set, got: {data}"

    # LLM: agent should acknowledge the instrument details
    _grade(response, prompt, {
        "instrument_identification": (
            "Did the agent identify and acknowledge the Neuropixels 2.0 probe "
            "with its serial number and manufacturer?"
        ),
        "record_creation": (
            "Did the agent create or indicate creation of an instrument record "
            "for the device?"
        ),
    })


@pytest.mark.llm
@pytest.mark.network
def test_multimodal_text_only_fallback(client):
    """Plain text message without attachments should still work normally."""
    prompt = "I performed a surgery on mouse 998877 today"
    response = _run(_send_chat(client, prompt))

    # Deterministic: subject record should be created
    records = _run(_get_records(client, record_type="subject"))
    assert len(records) >= 1, f"Expected at least 1 subject record, got {len(records)}"

    # LLM: agent should respond normally
    _grade(response, prompt, {
        "normal_response": (
            "Did the agent respond appropriately to the surgery description, "
            "acknowledging the subject and procedure?"
        ),
    })
