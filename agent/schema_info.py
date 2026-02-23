"""Schema introspection from aind-data-schema Pydantic models.

At import time, this module introspects the aind-data-schema package to build:
- VALID_MODALITIES: frozenset of valid modality abbreviations
- VALID_SPECIES: frozenset of valid species names
- VALID_SEX: frozenset of valid sex values
- KNOWN_FIELDS: dict of record_type -> frozenset of known field names
- SCHEMA_MODELS: dict of record_type -> Pydantic model class

If aind-data-schema is not installed, all exports are empty/False.
"""

from __future__ import annotations

SCHEMA_AVAILABLE: bool = False
SCHEMA_MODELS: dict[str, type] = {}
KNOWN_FIELDS: dict[str, frozenset[str]] = {}
VALID_MODALITIES: frozenset[str] = frozenset()
VALID_SPECIES: frozenset[str] = frozenset()
VALID_SEX: frozenset[str] = frozenset()
SPECIES_REGISTRY: dict[str, dict[str, str]] = {}

# Meta fields to exclude from KNOWN_FIELDS
_META_FIELDS = {"object_type", "describedBy", "schema_version"}

try:
    from aind_data_schema.core.subject import Subject
    from aind_data_schema.core.procedures import Procedures
    from aind_data_schema.core.data_description import DataDescription
    from aind_data_schema.core.instrument import Instrument
    from aind_data_schema.core.acquisition import Acquisition
    from aind_data_schema.core.processing import Processing
    from aind_data_schema.core.quality_control import QualityControl
    from aind_data_schema.components.subjects import (
        MouseSubject,
        HumanSubject,
        CalibrationObject,
        Sex,
    )
    from aind_data_schema_models.modalities import Modality
    from aind_data_schema_models.species import Species

    # -----------------------------------------------------------------------
    # Model mapping: record_type -> Pydantic model class
    # In aind-data-schema v2.0+, Session was merged into Acquisition and
    # Rig was merged into Instrument. Our app keeps them as separate record
    # types in the DB/UI, so we map accordingly.
    # -----------------------------------------------------------------------
    SCHEMA_MODELS = {
        "subject": Subject,
        "procedures": Procedures,
        "data_description": DataDescription,
        "instrument": Instrument,
        "acquisition": Acquisition,
        "processing": Processing,
        "quality_control": QualityControl,
        "session": Acquisition,  # session maps to Acquisition
        "rig": Instrument,       # rig maps to Instrument
    }

    # -----------------------------------------------------------------------
    # Known fields per record type
    # -----------------------------------------------------------------------
    def _get_fields(model: type) -> set[str]:
        """Extract field names from a Pydantic model, excluding meta fields."""
        return set(model.model_fields.keys()) - _META_FIELDS

    KNOWN_FIELDS = {
        # Subject includes fields from Subject + MouseSubject + HumanSubject + CalibrationObject
        "subject": frozenset(
            _get_fields(Subject)
            | _get_fields(MouseSubject)
            | _get_fields(HumanSubject)
            | _get_fields(CalibrationObject)
        ),
        "procedures": frozenset(_get_fields(Procedures)),
        # data_description: schema uses "modalities" (plural), our app uses "modality" (singular)
        "data_description": frozenset(
            _get_fields(DataDescription)
            | {"modality"}
        ),
        "instrument": frozenset(_get_fields(Instrument)),
        "acquisition": frozenset(_get_fields(Acquisition)),
        "processing": frozenset(_get_fields(Processing)),
        "quality_control": frozenset(_get_fields(QualityControl)),
        # session: Acquisition fields + our app-specific session field names
        "session": frozenset(
            _get_fields(Acquisition)
            | {"session_start_time", "session_end_time", "session_type", "rig_id"}
        ),
        # rig: Instrument fields + our app-specific rig field names
        "rig": frozenset(
            _get_fields(Instrument)
            | {"rig_id"}
        ),
    }

    # -----------------------------------------------------------------------
    # Enum sets
    # -----------------------------------------------------------------------
    # Modality.ALL and Species.ALL are tuples of model *classes* (not instances),
    # so we read the default values from their model_fields.
    VALID_MODALITIES = frozenset(
        m.model_fields["abbreviation"].default for m in Modality.ALL
    )
    VALID_SPECIES = frozenset(
        s.model_fields["name"].default for s in Species.ALL
    )
    VALID_SEX = frozenset(s.value for s in Sex)

    # Species → NCBI taxonomy registry mapping
    # Maps species name to full schema-compliant species object with registry info.
    SPECIES_REGISTRY = {}
    for _sp in Species.ALL:
        _fields = {k: v.default for k, v in _sp.model_fields.items()}
        _name = _fields.get("name", "")
        _registry = _fields.get("registry")
        _reg_id = _fields.get("registry_identifier", "")
        if _name and _reg_id:
            SPECIES_REGISTRY[_name] = {
                "name": _name,
                "registry": _registry.value if hasattr(_registry, "value") else str(_registry),
                "registry_identifier": _reg_id,
            }

    SCHEMA_AVAILABLE = True

except ImportError:
    pass
