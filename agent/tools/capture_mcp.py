"""MCP server for metadata capture tools.

Provides three tools that Claude can call during conversation:
- capture_metadata: Save/update a single typed metadata record
- find_records: Search for existing records to link or reuse
- link_records: Create explicit links between records
"""

import asyncio
import json
import logging
import re
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..shared import validation_events
from .registry_lookup import lookup_addgene, lookup_mgi, lookup_ncbi_gene
from .metadata_store import (
    create_record,
    find_records as store_find_records,
    get_record,
    link_records as store_link_records,
    update_record,
    update_record_validation,
)
from ..db.models import CATEGORY_MAP, VALID_RECORD_TYPES
from ..validation import validate_record

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_validation_summary(validation: dict[str, Any]) -> str:
    """Format validation results as clear text for the agent to relay to the user."""
    status = validation.get("status", "valid")
    errors = validation.get("errors", [])
    warnings = validation.get("warnings", [])
    missing = validation.get("missing_required", [])

    if status == "valid" and not missing:
        return "VALIDATION PASSED: All fields are valid."

    lines: list[str] = []

    if errors:
        lines.append("VALIDATION ERRORS (must be fixed):")
        for e in errors:
            lines.append(f"  - {e['field']}: {e['message']}")

    if missing:
        lines.append(f"MISSING REQUIRED FIELDS: {', '.join(missing)}")

    if warnings:
        lines.append("WARNINGS:")
        for w in warnings:
            lines.append(f"  - {w['field']}: {w['message']}")

    lines.append("")
    lines.append("You MUST report these issues to the user and suggest how to fix them.")

    return "\n".join(lines)


def _success(data: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data)}]}


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps({"status": "error", "error": message})}]}


# ---------------------------------------------------------------------------
# Registry lookup helpers
# ---------------------------------------------------------------------------

# Patterns that look like plasmid/vector names (must have additional chars after prefix)
_PLASMID_PATTERN = re.compile(
    r"(?:pAAV|AAV|pCAG|pEF|pCMV)[-\w]+",
    re.IGNORECASE,
)
_ADDGENE_CATALOG_PATTERN = re.compile(r"\b\d{4,6}\b")


def _extract_registry_queries(record_type: str, data: dict[str, Any]) -> dict[str, list[str]]:
    """Identify fields that should trigger external registry lookups.

    Returns a dict mapping registry name -> list of query strings.
    """
    queries: dict[str, list[str]] = {}

    if record_type == "subject":
        # Genotype → MGI + NCBI
        genotype = data.get("genotype")
        if genotype and isinstance(genotype, str) and len(genotype) > 2:
            # Split composite genotypes like "Ai14;Slc17a7-Cre" or "Emx1-Cre/Ai94"
            parts = re.split(r"[;/×]\s*", genotype)
            for part in parts:
                part = part.strip()
                if part and len(part) > 2:
                    queries.setdefault("mgi", []).append(part)
                    queries.setdefault("ncbi_gene", []).append(part)

        # Alleles → MGI
        alleles = data.get("alleles")
        if isinstance(alleles, list):
            for a in alleles:
                name = a.get("name") if isinstance(a, dict) else str(a) if a else None
                if name and len(name) > 2:
                    queries.setdefault("mgi", []).append(name)

    elif record_type == "procedures":
        # Serialize the entire procedures data to catch plasmid names
        # regardless of nesting (e.g., inside subject_procedures[].injection_materials[])
        full_str = json.dumps(data, default=str)

        # Plasmid / vector names → Addgene
        for match in _PLASMID_PATTERN.findall(full_str):
            queries.setdefault("addgene", []).append(match)

        # Addgene catalog numbers (4-6 digit numbers)
        for match in _ADDGENE_CATALOG_PATTERN.findall(full_str):
            if int(match) > 1000:
                queries.setdefault("addgene", []).append(match)

    # Deduplicate
    for key in queries:
        queries[key] = list(dict.fromkeys(queries[key]))

    return queries


async def _run_registry_lookups(
    record_type: str, data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Run external registry lookups for relevant fields. Returns a list of results."""
    queries = _extract_registry_queries(record_type, data)
    if not queries:
        return []

    lookup_fns = {
        "addgene": lookup_addgene,
        "ncbi_gene": lookup_ncbi_gene,
        "mgi": lookup_mgi,
    }

    tasks: list[tuple[str, str, Any]] = []
    for registry, terms in queries.items():
        fn = lookup_fns.get(registry)
        if fn:
            for term in terms:
                tasks.append((registry, term, fn(term)))

    if not tasks:
        return []

    # Run all lookups concurrently with a short timeout
    results: list[dict[str, Any]] = []
    try:
        coros = [t[2] for t in tasks]
        outcomes = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=20.0,
        )
        for (registry, term, _), outcome in zip(tasks, outcomes):
            if isinstance(outcome, Exception):
                logger.warning("Registry lookup failed for %s/%s: %s", registry, term, outcome)
                continue
            result_entry: dict[str, Any] = {"registry": registry, "query": term}
            if isinstance(outcome, dict):
                result_entry.update(outcome)
            results.append(result_entry)
    except asyncio.TimeoutError:
        logger.warning("Registry lookups timed out")

    return results


def _format_registry_summary(results: list[dict[str, Any]]) -> str:
    """Format registry lookup results as text for the agent."""
    if not results:
        return ""

    lines = ["REGISTRY LOOKUPS:"]
    for r in results:
        registry = r.get("registry", "unknown").upper().replace("_", " ")
        query = r.get("query", "")
        found = r.get("found", False)
        error = r.get("error")

        if error:
            lines.append(f"  - {registry} '{query}': lookup failed ({error})")
        elif found:
            # Include key details
            if r.get("results"):
                for entry in r["results"][:4]:
                    if entry.get("symbol"):
                        # NCBI gene results
                        symbol = entry.get("symbol", "")
                        desc = entry.get("description", "")
                        url = entry.get("url", "")
                        lines.append(f"  - {registry} '{query}': FOUND — {symbol} ({desc}) {url}")
                    elif entry.get("catalog_number"):
                        # Addgene plasmid results
                        cat = entry["catalog_number"]
                        name = entry.get("name", "")
                        desc = entry.get("description", "")
                        url = entry.get("url", f"https://www.addgene.org/{cat}/")
                        desc_part = f" — {desc}" if desc else ""
                        lines.append(f"  - {registry} '{query}': FOUND — #{cat} {name}{desc_part} {url}")
            elif r.get("url"):
                lines.append(f"  - {registry} '{query}': FOUND — {r['url']}")
            else:
                lines.append(f"  - {registry} '{query}': FOUND")
        else:
            lines.append(f"  - {registry} '{query}': NOT FOUND — could not verify in external registry")

    lines.append("")
    lines.append("Share these registry results with the user to confirm the identifiers are correct.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# capture_metadata tool
# ---------------------------------------------------------------------------

async def capture_metadata_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Core logic for saving a single metadata record."""
    session_id = args.get("session_id")
    if not session_id:
        return _error("session_id is required")

    record_type = args.get("record_type")
    if not record_type or record_type not in VALID_RECORD_TYPES:
        return _error(f"record_type must be one of: {', '.join(sorted(VALID_RECORD_TYPES))}")

    data = args.get("data")
    if not data or not isinstance(data, dict):
        # Try parsing if it's a JSON string
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                return _error("data must be a JSON object")
        else:
            return _error("data is required and must be a JSON object")

    record_id = args.get("record_id")
    name = args.get("name")
    link_to = args.get("link_to")

    try:
        if record_id:
            # Update existing record
            existing = await get_record(record_id)
            if existing is None:
                return _error(f"Record {record_id} not found")
            record = await update_record(record_id, data=data, name=name)
            if record is None:
                return _error(f"Failed to update record {record_id}")
            action = "updated"
        else:
            # Create new record
            record = await create_record(session_id, record_type, data, name=name)
            record_id = record["id"]
            action = "created"

        # Link if requested
        if link_to:
            target = await get_record(link_to)
            if target is None:
                logger.warning("Link target %s not found", link_to)
            else:
                await store_link_records(record_id, link_to)
                logger.info("Linked %s -> %s", record_id, link_to)

        # Validate
        record_data = record.get("data_json") or {}
        validation_result = validate_record(record_type, record_data)
        await update_record_validation(record_id, validation_result.to_dict())

        validation_dict = validation_result.to_dict()

        # Push validation to the streaming queue so the frontend can
        # display errors/warnings inline in the tool dropdown.
        queue = validation_events.get(None)
        if queue is not None:
            queue.put_nowait(validation_dict)

        # Build a human-readable validation summary so the agent
        # reliably flags issues to the user in its response.
        validation_summary = _format_validation_summary(validation_dict)

        # Run external registry lookups for relevant fields (non-blocking).
        registry_results = await _run_registry_lookups(record_type, record_data)
        registry_summary = _format_registry_summary(registry_results)

        response: dict[str, Any] = {
            "action": action,
            "record_id": record_id,
            "record_type": record_type,
            "category": CATEGORY_MAP[record_type],
            "name": record.get("name"),
            "message": f"Successfully {action} {record_type} record",
            "validation": validation_dict,
            "validation_summary": validation_summary,
        }

        if registry_results:
            response["registry_lookups"] = registry_results
            response["registry_summary"] = registry_summary

        return _success(response)

    except Exception as e:
        logger.exception("Failed to save record for session %s", session_id)
        return _error(str(e))


@tool(
    "capture_metadata",
    """Save or update a single metadata record from the scientist's input.

Call this tool whenever you identify metadata from the conversation. Each call captures
one record type (e.g., just the subject, or just the procedure). You can call it multiple
times as more information is provided.

Record types and their categories:
- SHARED (reusable across experiments): subject, procedures, instrument, rig
- ASSET-SPECIFIC (tied to a data asset): data_description, acquisition, session, processing, quality_control

To update an existing record, pass its record_id. To link a new record to an existing one
(e.g., link a session to a subject), pass the link_to parameter with the target record's ID.

Example calls:
    capture_metadata(session_id="abc", record_type="subject", data={"subject_id": "4528", "species": {"name": "Mus musculus"}})
    capture_metadata(session_id="abc", record_type="procedures", data={"procedure_type": "Injection", "coordinates": {"x": -1.5, "y": 2.0, "z": -3.5}})
    capture_metadata(session_id="abc", record_type="session", data={"session_start_time": "2025-01-15T09:00:00"}, link_to="<subject_record_id>")
""",
    {
        "session_id": str,
        "record_type": str,
        "data": dict,
        "name": str,
        "record_id": str,
        "link_to": str,
    },
)
async def capture_metadata(args: dict[str, Any]) -> dict[str, Any]:
    """MCP tool wrapper for capture_metadata_handler."""
    return await capture_metadata_handler(args)


# ---------------------------------------------------------------------------
# find_records tool
# ---------------------------------------------------------------------------

async def find_records_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Search for existing metadata records."""
    record_type = args.get("record_type")
    query = args.get("query")
    category = args.get("category")

    if not record_type and not query and not category:
        return _error("At least one of record_type, query, or category is required")

    try:
        records = await store_find_records(
            record_type=record_type,
            query=query,
            category=category,
        )

        summaries = []
        for r in records:
            summaries.append({
                "id": r["id"],
                "record_type": r["record_type"],
                "category": r["category"],
                "name": r.get("name"),
                "status": r["status"],
                "data": r.get("data_json"),
                "session_id": r["session_id"],
            })

        return _success({
            "count": len(summaries),
            "records": summaries,
        })

    except Exception as e:
        logger.exception("Failed to search records")
        return _error(str(e))


@tool(
    "find_records",
    """Search for existing metadata records in the database.

Use this to find shared records (subjects, instruments, rigs, procedures) that can be
linked to new data assets. This avoids creating duplicate records.

Example calls:
    find_records(record_type="subject", query="4528")
    find_records(category="shared")
    find_records(record_type="instrument")
""",
    {
        "record_type": str,
        "query": str,
        "category": str,
    },
)
async def find_records_tool(args: dict[str, Any]) -> dict[str, Any]:
    """MCP tool wrapper for find_records_handler."""
    return await find_records_handler(args)


# ---------------------------------------------------------------------------
# link_records tool
# ---------------------------------------------------------------------------

async def link_records_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Link two metadata records together."""
    source_id = args.get("source_id")
    target_id = args.get("target_id")

    if not source_id or not target_id:
        return _error("Both source_id and target_id are required")

    try:
        source = await get_record(source_id)
        target = await get_record(target_id)

        if source is None:
            return _error(f"Source record {source_id} not found")
        if target is None:
            return _error(f"Target record {target_id} not found")

        await store_link_records(source_id, target_id)

        return _success({
            "message": f"Linked {source['record_type']} '{source.get('name', source_id)}' to {target['record_type']} '{target.get('name', target_id)}'",
            "source_id": source_id,
            "target_id": target_id,
        })

    except Exception as e:
        logger.exception("Failed to link records")
        return _error(str(e))


@tool(
    "link_records",
    """Create a link between two metadata records.

Use this to associate related records, e.g., link a session to a subject, or an
acquisition to an instrument. Links are bidirectional.

Example: link_records(source_id="<session_record_id>", target_id="<subject_record_id>")
""",
    {
        "source_id": str,
        "target_id": str,
    },
)
async def link_records_tool(args: dict[str, Any]) -> dict[str, Any]:
    """MCP tool wrapper for link_records_handler."""
    return await link_records_handler(args)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

capture_server = create_sdk_mcp_server(
    name="capture",
    version="2.0.0",
    tools=[capture_metadata, find_records_tool, link_records_tool],
)
