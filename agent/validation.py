"""Schema validation for AIND metadata records.

Validates extracted metadata against AIND schema rules on a per-record-type basis:
- Required field checks
- Enum validation (modality, sex, species)
- Format validation (subject IDs, timestamps, coordinates)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Controlled vocabularies from AIND schema
# ---------------------------------------------------------------------------

VALID_MODALITIES = frozenset({
    "behavior",
    "behavior-videos",
    "confocal",
    "EMG",
    "ecephys",
    "fib",
    "fMOST",
    "icephys",
    "ISI",
    "MRI",
    "merfish",
    "pophys",
    "slap",
    "SPIM",
})

VALID_SEX = frozenset({"Male", "Female", "Unknown"})

VALID_SPECIES = frozenset({
    "Mus musculus",
    "Homo sapiens",
    "Rattus norvegicus",
    "Macaca mulatta",
    "Drosophila melanogaster",
    "Danio rerio",
})

# Required fields per record type (dot paths within the record's data_json)
REQUIRED_FIELDS_BY_TYPE: dict[str, list[str]] = {
    "subject": ["subject_id"],
    "data_description": ["modality", "project_name"],
    "session": ["session_start_time"],
    "procedures": [],
    "instrument": [],
    "acquisition": [],
    "processing": [],
    "quality_control": [],
    "rig": [],
}


# ---------------------------------------------------------------------------
# Validation result types
# ---------------------------------------------------------------------------

class ValidationIssue:
    """A single validation error or warning."""

    __slots__ = ("field", "message", "severity")

    def __init__(self, field: str, message: str, severity: str = "error") -> None:
        self.field = field
        self.message = message
        self.severity = severity  # "error" or "warning"

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message, "severity": self.severity}


class ValidationResult:
    """Aggregated validation result for a metadata record."""

    def __init__(self, record_type: str = "unknown") -> None:
        self.record_type = record_type
        self.issues: list[ValidationIssue] = []
        self.missing_required: list[str] = []
        self.valid_fields: list[str] = []

    @property
    def status(self) -> str:
        if any(i.severity == "error" for i in self.issues):
            return "errors"
        if self.issues or self.missing_required:
            return "warnings"
        return "valid"

    @property
    def completeness_score(self) -> float:
        required = REQUIRED_FIELDS_BY_TYPE.get(self.record_type, [])
        total = len(required)
        if total == 0:
            return 1.0
        present = total - len(self.missing_required)
        return round(present / total, 2)

    def add_error(self, field: str, message: str) -> None:
        self.issues.append(ValidationIssue(field, message, "error"))

    def add_warning(self, field: str, message: str) -> None:
        self.issues.append(ValidationIssue(field, message, "warning"))

    def add_valid(self, field: str) -> None:
        self.valid_fields.append(field)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "completeness_score": self.completeness_score,
            "record_type": self.record_type,
            "errors": [i.to_dict() for i in self.issues if i.severity == "error"],
            "warnings": [i.to_dict() for i in self.issues if i.severity == "warning"],
            "missing_required": self.missing_required,
            "valid_fields": self.valid_fields,
        }


# ---------------------------------------------------------------------------
# Helper to reach into nested dicts
# ---------------------------------------------------------------------------

def _get_nested(data: dict, path: str) -> Any:
    """Get a value from a nested dict using dot notation."""
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


# ---------------------------------------------------------------------------
# Per-type validators
# ---------------------------------------------------------------------------

def _check_required_fields(record_type: str, data: dict, result: ValidationResult) -> None:
    """Check that all required fields for this record type are present."""
    for field_path in REQUIRED_FIELDS_BY_TYPE.get(record_type, []):
        value = _get_nested(data, field_path)
        if value is None or value == "" or value == []:
            result.missing_required.append(field_path)
        else:
            result.add_valid(field_path)


def _validate_subject(data: dict, result: ValidationResult) -> None:
    """Validate subject fields."""
    sid = data.get("subject_id")
    if sid is not None:
        if not re.match(r"^\d{4,}$", str(sid)):
            result.add_warning(
                "subject_id",
                f"Subject ID '{sid}' should be a numeric string with 4+ digits",
            )
        else:
            result.add_valid("subject_id")

    sex = data.get("sex")
    if sex is not None:
        if sex not in VALID_SEX:
            result.add_error(
                "sex",
                f"Invalid sex '{sex}'. Must be one of: {', '.join(sorted(VALID_SEX))}",
            )
        else:
            result.add_valid("sex")

    species = data.get("species")
    if isinstance(species, dict):
        name = species.get("name")
        if name is not None and name not in VALID_SPECIES:
            result.add_warning(
                "species.name",
                f"Unrecognized species '{name}'. Expected one of: {', '.join(sorted(VALID_SPECIES))}",
            )
        elif name is not None:
            result.add_valid("species.name")


def _validate_data_description(data: dict, result: ValidationResult) -> None:
    """Validate data_description fields."""
    modality = data.get("modality")
    if isinstance(modality, list):
        for i, mod in enumerate(modality):
            if isinstance(mod, dict):
                abbr = mod.get("abbreviation")
                if abbr is not None and abbr not in VALID_MODALITIES:
                    result.add_error(
                        f"modality[{i}].abbreviation",
                        f"Invalid modality '{abbr}'. Must be one of: {', '.join(sorted(VALID_MODALITIES))}",
                    )
                elif abbr is not None:
                    result.add_valid(f"modality[{i}].abbreviation")

    pn = data.get("project_name")
    if pn is not None:
        if len(pn.strip()) < 2:
            result.add_warning("project_name", "Project name is too short")
        else:
            result.add_valid("project_name")


def _validate_session(data: dict, result: ValidationResult) -> None:
    """Validate session fields."""
    start = data.get("session_start_time")
    end = data.get("session_end_time")

    if start is not None:
        result.add_valid("session_start_time")
    if end is not None:
        result.add_valid("session_end_time")

    if start and end:
        try:
            fmt_patterns = [
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%H:%M %p",
                "%H:%M",
                "%I:%M %p",
            ]
            start_dt = end_dt = None
            for fmt in fmt_patterns:
                try:
                    start_dt = datetime.strptime(str(start).strip(), fmt)
                    break
                except ValueError:
                    continue
            for fmt in fmt_patterns:
                try:
                    end_dt = datetime.strptime(str(end).strip(), fmt)
                    break
                except ValueError:
                    continue

            if start_dt and end_dt and end_dt <= start_dt:
                result.add_error(
                    "session_end_time",
                    "Session end time must be after start time",
                )
        except Exception:
            pass

    rig_id = data.get("rig_id")
    if rig_id is not None:
        result.add_valid("rig_id")


def _validate_procedures(data: dict, result: ValidationResult) -> None:
    """Validate procedures fields."""
    pid = data.get("protocol_id")
    if pid is not None:
        result.add_valid("protocol_id")

    coords = data.get("coordinates")
    if isinstance(coords, dict):
        x, y = coords.get("x"), coords.get("y")
        if x is not None and y is not None:
            try:
                float(x)
                float(y)
                result.add_valid("coordinates")
            except (ValueError, TypeError):
                result.add_error(
                    "coordinates",
                    f"Coordinates must be numeric, got x={x}, y={y}",
                )

    thickness = data.get("section_thickness_um")
    if thickness is not None:
        try:
            val = float(thickness)
            if val <= 0:
                result.add_error("section_thickness_um", "Section thickness must be positive")
            else:
                result.add_valid("section_thickness_um")
        except (ValueError, TypeError):
            result.add_error(
                "section_thickness_um",
                f"Section thickness must be numeric, got '{thickness}'",
            )


# Type -> validator function mapping
_VALIDATORS: dict[str, Any] = {
    "subject": _validate_subject,
    "data_description": _validate_data_description,
    "session": _validate_session,
    "procedures": _validate_procedures,
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_record(record_type: str, data: dict[str, Any]) -> ValidationResult:
    """Run validation rules for a single metadata record.

    Parameters
    ----------
    record_type : str
        The type of record (e.g., 'subject', 'session').
    data : dict
        The record's data_json content.

    Returns
    -------
    ValidationResult
        Validation result with status, issues, and completeness score.
    """
    result = ValidationResult(record_type)
    _check_required_fields(record_type, data, result)

    validator = _VALIDATORS.get(record_type)
    if validator:
        validator(data, result)

    return result


# Keep backward compat alias for any callers using the old name
def validate_metadata(metadata: dict[str, Any]) -> ValidationResult:
    """Legacy wrapper — validates fields across multiple record types."""
    result = ValidationResult("subject")
    for record_type, data in metadata.items():
        if isinstance(data, dict) and record_type in _VALIDATORS:
            _check_required_fields(record_type, data, result)
            _VALIDATORS[record_type](data, result)
    return result
