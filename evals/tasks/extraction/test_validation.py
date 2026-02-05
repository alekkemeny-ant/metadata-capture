"""Tests for schema validation logic."""

from agent.validation import validate_record, validate_metadata, VALID_SEX


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
        errors = [i for i in result.issues if i.field == "section_thickness_um"]
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
