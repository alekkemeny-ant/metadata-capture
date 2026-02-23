"""Validation test suite: registry lookup correctness tests.

Each test case calls an external registry API and verifies the response
structure and key fields. Requires network access.

Run:
    pytest evals/tasks/validation/ -v -m network
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent.tools.registry_lookup import lookup_addgene, lookup_mgi, lookup_ncbi_gene

CASES_PATH = Path(__file__).parent / "cases.yaml"

REGISTRY_FN = {
    "addgene": lookup_addgene,
    "ncbi_gene": lookup_ncbi_gene,
    "mgi": lookup_mgi,
}


def _load_cases() -> list[dict[str, Any]]:
    with open(CASES_PATH) as f:
        return yaml.safe_load(f)


_CASES = _load_cases()


def _case_id(case: dict[str, Any]) -> str:
    return case["id"]


@pytest.mark.network
@pytest.mark.parametrize("case", _CASES, ids=_case_id)
def test_registry_lookup(case: dict[str, Any]) -> None:
    """Validate that a registry lookup returns the expected result."""
    registry = case["registry"]
    query = case["query"]
    expected = case["expected"]

    fn = REGISTRY_FN[registry]
    result = asyncio.run(fn(query))

    # Must not have an error
    assert "error" not in result, f"Lookup returned error: {result['error']}"

    # Check expected top-level fields
    for key, value in expected.items():
        assert key in result, f"Missing key '{key}' in result: {result}"
        assert result[key] == value, (
            f"Expected {key}={value!r}, got {result[key]!r}"
        )

    # Check deeper expected_fields if specified
    expected_fields = case.get("expected_fields", {})
    if "results" in expected_fields:
        results_spec = expected_fields["results"]
        results = result.get("results", [])

        if "min_length" in results_spec:
            assert len(results) >= results_spec["min_length"], (
                f"Expected at least {results_spec['min_length']} results, got {len(results)}"
            )

        if "first_result" in results_spec and results:
            first = results[0]
            fr_spec = results_spec["first_result"]
            if "symbol_contains" in fr_spec:
                assert fr_spec["symbol_contains"].lower() in first.get("symbol", "").lower(), (
                    f"First result symbol '{first.get('symbol')}' does not contain "
                    f"'{fr_spec['symbol_contains']}'"
                )
            if fr_spec.get("has_catalog_number"):
                assert first.get("catalog_number"), (
                    f"First result missing catalog_number: {first}"
                )
            if fr_spec.get("has_name"):
                assert first.get("name"), (
                    f"First result missing name: {first}"
                )
