"""Tests for schema validation logic."""

from agent.schema_info import SCHEMA_AVAILABLE, SCHEMA_MODELS, KNOWN_FIELDS, VALID_MODALITIES as SCHEMA_MODALITIES, VALID_SPECIES as SCHEMA_SPECIES, VALID_SEX as SCHEMA_SEX
from agent.validation import validate_record, validate_metadata, VALID_SEX, VALID_MODALITIES


class TestRequiredFields:
    """Test required field detection."""

    def test_all_required_present_subject(self):
        result = validate_record("subject", {"subject_id": "553429"})
        assert len(result.missing_required) == 0

    def test_all_required_present_data_description(self):
        result = validate_record("data_description", {
            "modality": [{"name": "Planar optical physiology", "abbreviation": "pophys"}],
            "project_name": "BrainMap",
        })
        assert len(result.missing_required) == 0

    def test_missing_required_subject(self):
        result = validate_record("subject", {})
        assert "subject_id" in result.missing_required

    def test_missing_required_data_description(self):
        result = validate_record("data_description", {})
        assert set(result.missing_required) == {"modality", "project_name"}

    def test_no_required_for_procedures(self):
        result = validate_record("procedures", {})
        assert len(result.missing_required) == 0


class TestEnumValidation:
    """Test controlled vocabulary validation."""

    def test_valid_sex(self):
        for sex in VALID_SEX:
            result = validate_record("subject", {"sex": sex})
            errors = [i for i in result.issues if i.field == "sex" and i.severity == "error"]
            assert len(errors) == 0, f"'{sex}' should be valid"

    def test_invalid_sex(self):
        result = validate_record("subject", {"sex": "unknown_value"})
        errors = [i for i in result.issues if i.field == "sex" and i.severity == "error"]
        assert len(errors) == 1

    def test_valid_modality(self):
        for abbr in ["ecephys", "pophys", "SPIM", "behavior"]:
            result = validate_record("data_description", {"modality": [{"abbreviation": abbr}]})
            errors = [i for i in result.issues if "modality" in i.field and i.severity == "error"]
            assert len(errors) == 0, f"'{abbr}' should be valid"

    def test_invalid_modality(self):
        result = validate_record("data_description", {"modality": [{"abbreviation": "xray"}]})
        errors = [i for i in result.issues if "modality" in i.field and i.severity == "error"]
        assert len(errors) == 1

    def test_valid_species(self):
        result = validate_record("subject", {"species": {"name": "Mus musculus"}})
        warnings = [i for i in result.issues if "species" in i.field]
        assert len(warnings) == 0

    def test_unknown_species(self):
        result = validate_record("subject", {"species": {"name": "Canis lupus"}})
        warnings = [i for i in result.issues if "species" in i.field]
        assert len(warnings) == 1


class TestFormatValidation:
    """Test format checks."""

    def test_valid_subject_id(self):
        result = validate_record("subject", {"subject_id": "553429"})
        warnings = [i for i in result.issues if i.field == "subject_id"]
        assert len(warnings) == 0

    def test_short_subject_id(self):
        result = validate_record("subject", {"subject_id": "12"})
        warnings = [i for i in result.issues if i.field == "subject_id"]
        assert len(warnings) == 1

    def test_valid_coordinates(self):
        result = validate_record("procedures", {"coordinates": {"x": 20.0, "y": 50.0}})
        assert "coordinates" in result.valid_fields

    def test_positive_thickness(self):
        result = validate_record("procedures", {"section_thickness_um": 10.0})
        assert "section_thickness_um" in result.valid_fields

    def test_negative_thickness(self):
        result = validate_record("procedures", {"section_thickness_um": -5.0})
        errors = [i for i in result.issues if i.field == "section_thickness_um" and i.severity == "error"]
        assert len(errors) == 1


class TestCompletenessScore:
    """Test completeness scoring."""

    def test_full_completeness_subject(self):
        result = validate_record("subject", {"subject_id": "553429"})
        assert result.completeness_score == 1.0

    def test_full_completeness_data_description(self):
        result = validate_record("data_description", {
            "modality": [{"abbreviation": "pophys"}],
            "project_name": "BrainMap",
        })
        assert result.completeness_score == 1.0

    def test_zero_completeness_subject(self):
        result = validate_record("subject", {})
        assert result.completeness_score == 0.0

    def test_full_completeness_no_required_fields(self):
        result = validate_record("procedures", {})
        assert result.completeness_score == 1.0


class TestValidationResult:
    """Test the ValidationResult output format."""

    def test_to_dict_structure(self):
        result = validate_record("subject", {"subject_id": "553429", "sex": "invalid"})
        d = result.to_dict()
        assert "status" in d
        assert "completeness_score" in d
        assert "record_type" in d
        assert "errors" in d
        assert "warnings" in d
        assert "missing_required" in d
        assert "valid_fields" in d

    def test_valid_status(self):
        result = validate_record("subject", {"subject_id": "553429"})
        assert result.status == "valid"

    def test_error_status(self):
        result = validate_record("subject", {"sex": "invalid"})
        assert result.status == "errors"


class TestLegacyCompat:
    """Test the backward-compatible validate_metadata wrapper."""

    def test_legacy_wrapper_still_works(self):
        metadata = {
            "subject": {"subject_id": "553429"},
            "data_description": {
                "modality": [{"abbreviation": "pophys"}],
                "project_name": "BrainMap",
            },
        }
        result = validate_metadata(metadata)
        assert result.status in ("valid", "warnings", "errors")


class TestSchemaIntegration:
    """Test that aind-data-schema introspection provides correct enum sets."""

    def test_schema_available(self):
        assert SCHEMA_AVAILABLE is True

    def test_modalities_include_known_values(self):
        for abbr in ["ecephys", "pophys", "slap2", "BARseq"]:
            assert abbr in SCHEMA_MODALITIES, f"'{abbr}' should be in VALID_MODALITIES"

    def test_modalities_include_all_new_values(self):
        """Verify all modalities added in this session are present."""
        for abbr in ["EM", "MAPseq", "STPT", "brightfield", "scRNAseq"]:
            assert abbr in SCHEMA_MODALITIES, f"'{abbr}' should be in VALID_MODALITIES"

    def test_stale_modality_slap_rejected(self):
        """'slap' was renamed to 'slap2' in the schema — should not be valid."""
        assert "slap" not in VALID_MODALITIES

    def test_stale_modality_slap_causes_error(self):
        """Validating 'slap' as a modality abbreviation should produce an error."""
        result = validate_record("data_description", {"modality": [{"abbreviation": "slap"}]})
        errors = [i for i in result.issues if "modality" in i.field and i.severity == "error"]
        assert len(errors) == 1
        assert "slap" in errors[0].message

    def test_species_include_known_values(self):
        for name in ["Mus musculus", "Homo sapiens"]:
            assert name in SCHEMA_SPECIES, f"'{name}' should be in VALID_SPECIES"

    def test_sex_values(self):
        assert "Male" in SCHEMA_SEX
        assert "Female" in SCHEMA_SEX
        assert "Unknown" not in SCHEMA_SEX

    def test_unknown_sex_causes_error(self):
        """'Unknown' is not a valid sex in aind-data-schema — should produce an error."""
        result = validate_record("subject", {"sex": "Unknown"})
        errors = [i for i in result.issues if i.field == "sex" and i.severity == "error"]
        assert len(errors) == 1

    def test_all_record_types_have_known_fields(self):
        """Every record type in SCHEMA_MODELS should have a KNOWN_FIELDS entry."""
        for record_type in SCHEMA_MODELS:
            assert record_type in KNOWN_FIELDS, f"Missing KNOWN_FIELDS for '{record_type}'"
            assert len(KNOWN_FIELDS[record_type]) > 0, f"Empty KNOWN_FIELDS for '{record_type}'"

    def test_schema_models_cover_all_record_types(self):
        """All 9 record types should have schema model mappings."""
        expected = {"subject", "procedures", "data_description", "instrument",
                    "acquisition", "processing", "quality_control", "session", "rig"}
        assert set(SCHEMA_MODELS.keys()) == expected


class TestUnknownFields:
    """Test unknown-field warning detection."""

    def test_unknown_field_warning(self):
        result = validate_record("subject", {"subject_id": "12345", "bogus_field": "x"})
        warnings = [i for i in result.issues if i.severity == "warning" and "bogus_field" in i.message]
        assert len(warnings) == 1

    def test_known_field_no_warning(self):
        result = validate_record("subject", {"subject_id": "12345", "sex": "Male"})
        warnings = [i for i in result.issues if i.severity == "warning" and "unknown field" in i.message.lower()]
        assert len(warnings) == 0

    def test_unknown_field_on_procedures(self):
        result = validate_record("procedures", {"xyz_field": "x"})
        warnings = [i for i in result.issues if i.severity == "warning" and "xyz_field" in i.message]
        assert len(warnings) == 1

    def test_modality_singular_not_flagged_on_data_description(self):
        """Our app uses 'modality' (singular) — should not trigger unknown-field warning."""
        result = validate_record("data_description", {
            "modality": [{"abbreviation": "ecephys"}],
            "project_name": "Test",
        })
        warnings = [i for i in result.issues if i.severity == "warning" and "modality" in i.field]
        assert len(warnings) == 0

    def test_session_app_fields_not_flagged(self):
        """App-specific session fields (session_start_time, rig_id) should not trigger warnings."""
        result = validate_record("session", {
            "session_start_time": "2025-01-15T09:00:00",
            "rig_id": "rig-001",
        })
        warnings = [i for i in result.issues if i.severity == "warning" and "unknown field" in i.message.lower()]
        assert len(warnings) == 0

    def test_rig_app_fields_not_flagged(self):
        """App-specific rig field (rig_id) should not trigger unknown-field warning."""
        result = validate_record("rig", {"rig_id": "rig-001"})
        warnings = [i for i in result.issues if i.severity == "warning" and "rig_id" in i.message]
        assert len(warnings) == 0

    def test_unknown_field_on_multiple_record_types(self):
        """Unknown fields should be caught on any record type with KNOWN_FIELDS."""
        for record_type in ["session", "rig", "instrument", "acquisition", "processing", "quality_control"]:
            result = validate_record(record_type, {"totally_bogus_xyz": "x"})
            warnings = [i for i in result.issues if i.severity == "warning" and "totally_bogus_xyz" in i.message]
            assert len(warnings) == 1, f"Expected unknown-field warning for '{record_type}'"
