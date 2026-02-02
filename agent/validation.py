"""Schema validation for AIND metadata drafts.

Validates extracted metadata against AIND schema rules:
- Required field checks
- Enum validation (modality, sex, species)
- Format validation (subject IDs, timestamps, coordinates)
- Cross-field consistency checks
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

PHYSIOLOGY_MODALITIES = frozenset({
    "ecephys", "pophys", "fib", "icephys", "slap",
})

REQUIRED_FIELDS = [
    "subject.subject_id",
    "data_description.modality",
    "data_description.project_name",
]


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
    """Aggregated validation result for a metadata draft."""

    def __init__(self) -> None:
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
        total = len(REQUIRED_FIELDS)
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
            "errors": [i.to_dict() for i in self.issues if i.severity == "error"],
            "warnings": [i.to_dict() for i in self.issues if i.severity == "warning"],
            "missing_required": self.missing_required,
            "valid_fields": self.valid_fields,
        }


# ---------------------------------------------------------------------------
# Helper to reach into nested dicts
# ---------------------------------------------------------------------------

def _get_nested(data: dict, path: str) -> Any:
    """Get a value from a nested dict using dot notation.

    'subject.subject_id' -> data['subject']['subject_id']
    """
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------

def _check_required_fields(metadata: dict, result: ValidationResult) -> None:
    """Check that all required fields are present."""
    for field_path in REQUIRED_FIELDS:
        value = _get_nested(metadata, field_path)
        if value is None or value == "" or value == []:
            result.missing_required.append(field_path)
        else:
            result.add_valid(field_path)


def _validate_subject(metadata: dict, result: ValidationResult) -> None:
    """Validate subject fields."""
    subject = metadata.get("subject")
    if not subject or not isinstance(subject, dict):
        return

    # subject_id: should be numeric, 4+ digits
    sid = subject.get("subject_id")
    if sid is not None:
        if not re.match(r"^\d{4,}$", str(sid)):
            result.add_warning(
                "subject.subject_id",
                f"Subject ID '{sid}' should be a numeric string with 4+ digits",
            )
        else:
            result.add_valid("subject.subject_id")

    # sex
    sex = subject.get("sex")
    if sex is not None:
        if sex not in VALID_SEX:
            result.add_error(
                "subject.sex",
                f"Invalid sex '{sex}'. Must be one of: {', '.join(sorted(VALID_SEX))}",
            )
        else:
            result.add_valid("subject.sex")

    # species
    species = subject.get("species")
    if isinstance(species, dict):
        name = species.get("name")
        if name is not None and name not in VALID_SPECIES:
            result.add_warning(
                "subject.species.name",
                f"Unrecognized species '{name}'. Expected one of: {', '.join(sorted(VALID_SPECIES))}",
            )
        elif name is not None:
            result.add_valid("subject.species.name")


def _validate_data_description(metadata: dict, result: ValidationResult) -> None:
    """Validate data_description fields."""
    dd = metadata.get("data_description")
    if not dd or not isinstance(dd, dict):
        return

    # modality
    modality = dd.get("modality")
    if isinstance(modality, list):
        for i, mod in enumerate(modality):
            if isinstance(mod, dict):
                abbr = mod.get("abbreviation")
                if abbr is not None and abbr not in VALID_MODALITIES:
                    result.add_error(
                        f"data_description.modality[{i}].abbreviation",
                        f"Invalid modality '{abbr}'. Must be one of: {', '.join(sorted(VALID_MODALITIES))}",
                    )
                elif abbr is not None:
                    result.add_valid(f"data_description.modality[{i}].abbreviation")

    # project_name
    pn = dd.get("project_name")
    if pn is not None:
        if len(pn.strip()) < 2:
            result.add_warning(
                "data_description.project_name",
                "Project name is too short",
            )
        else:
            result.add_valid("data_description.project_name")


def _validate_session(metadata: dict, result: ValidationResult) -> None:
    """Validate session fields."""
    session = metadata.get("session")
    if not session:
        # If modality is physiology-based, session times are expected
        dd = metadata.get("data_description") or {}
        if not isinstance(dd, dict):
            return
        modality = dd.get("modality", [])
        if isinstance(modality, list):
            for mod in modality:
                if isinstance(mod, dict) and mod.get("abbreviation") in PHYSIOLOGY_MODALITIES:
                    result.add_warning(
                        "session",
                        f"Session information expected for physiology modality '{mod.get('abbreviation')}'",
                    )
        return

    if not isinstance(session, dict):
        return

    start = session.get("session_start_time")
    end = session.get("session_end_time")

    if start is not None:
        result.add_valid("session.session_start_time")
    if end is not None:
        result.add_valid("session.session_end_time")

    # Check end > start if both are full datetime strings
    if start and end:
        try:
            # Try parsing ISO 8601
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
                    "session.session_end_time",
                    "Session end time must be after start time",
                )
        except Exception:
            pass  # Can't parse, skip comparison

    # rig_id format check
    rig_id = session.get("rig_id")
    if rig_id is not None:
        result.add_valid("session.rig_id")


def _validate_procedures(metadata: dict, result: ValidationResult) -> None:
    """Validate procedures fields."""
    procedures = metadata.get("procedures")
    if not procedures or not isinstance(procedures, dict):
        return

    # protocol_id
    pid = procedures.get("protocol_id")
    if pid is not None:
        result.add_valid("procedures.protocol_id")

    # coordinates
    coords = procedures.get("coordinates")
    if isinstance(coords, dict):
        x, y = coords.get("x"), coords.get("y")
        if x is not None and y is not None:
            try:
                float(x)
                float(y)
                result.add_valid("procedures.coordinates")
            except (ValueError, TypeError):
                result.add_error(
                    "procedures.coordinates",
                    f"Coordinates must be numeric, got x={x}, y={y}",
                )

    # section_thickness_um
    thickness = procedures.get("section_thickness_um")
    if thickness is not None:
        try:
            val = float(thickness)
            if val <= 0:
                result.add_error(
                    "procedures.section_thickness_um",
                    "Section thickness must be positive",
                )
            else:
                result.add_valid("procedures.section_thickness_um")
        except (ValueError, TypeError):
            result.add_error(
                "procedures.section_thickness_um",
                f"Section thickness must be numeric, got '{thickness}'",
            )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_metadata(metadata: dict[str, Any]) -> ValidationResult:
    """Run all validation rules against extracted metadata.

    Parameters
    ----------
    metadata : dict
        Extracted metadata with top-level keys like 'subject', 'session',
        'data_description', 'procedures', etc.

    Returns
    -------
    ValidationResult
        Aggregated validation result with status, issues, and completeness score.
    """
    result = ValidationResult()

    _check_required_fields(metadata, result)
    _validate_subject(metadata, result)
    _validate_data_description(metadata, result)
    _validate_session(metadata, result)
    _validate_procedures(metadata, result)

    return result
