"""Tests for schema validation logic, validation feedback, and registry lookup extraction."""

import pytest

from agent.schema_info import SCHEMA_AVAILABLE, SCHEMA_MODELS, KNOWN_FIELDS, VALID_MODALITIES as SCHEMA_MODALITIES, VALID_SPECIES as SCHEMA_SPECIES, VALID_SEX as SCHEMA_SEX
from agent.validation import validate_record, validate_metadata, VALID_SEX, VALID_MODALITIES
from agent.tools.capture_mcp import _format_validation_summary, _extract_registry_queries, _format_registry_summary


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


class TestValidationSummaryFormatter:
    """Test _format_validation_summary output for agent feedback."""

    def test_valid_record_summary(self):
        v = {"status": "valid", "errors": [], "warnings": [], "missing_required": []}
        summary = _format_validation_summary(v)
        assert "VALIDATION PASSED" in summary

    def test_error_summary(self):
        v = {
            "status": "errors",
            "errors": [{"field": "sex", "message": "Invalid sex 'Unknown'", "severity": "error"}],
            "warnings": [],
            "missing_required": [],
        }
        summary = _format_validation_summary(v)
        assert "VALIDATION ERRORS" in summary
        assert "sex" in summary
        assert "Invalid sex" in summary
        assert "MUST report" in summary

    def test_warning_summary(self):
        v = {
            "status": "warnings",
            "errors": [],
            "warnings": [{"field": "bogus", "message": "Unknown field 'bogus'", "severity": "warning"}],
            "missing_required": [],
        }
        summary = _format_validation_summary(v)
        assert "WARNINGS" in summary
        assert "bogus" in summary

    def test_missing_required_summary(self):
        v = {"status": "warnings", "errors": [], "warnings": [], "missing_required": ["subject_id", "sex"]}
        summary = _format_validation_summary(v)
        assert "MISSING REQUIRED" in summary
        assert "subject_id" in summary
        assert "sex" in summary

    def test_combined_errors_and_warnings(self):
        v = {
            "status": "errors",
            "errors": [{"field": "sex", "message": "Invalid", "severity": "error"}],
            "warnings": [{"field": "foo", "message": "Unknown", "severity": "warning"}],
            "missing_required": ["modality"],
        }
        summary = _format_validation_summary(v)
        assert "VALIDATION ERRORS" in summary
        assert "MISSING REQUIRED" in summary
        assert "WARNINGS" in summary


class TestRegistryQueryExtraction:
    """Test _extract_registry_queries for identifying lookup-worthy fields."""

    def test_subject_genotype_single(self):
        queries = _extract_registry_queries("subject", {"genotype": "Ai14"})
        assert "mgi" in queries
        assert "Ai14" in queries["mgi"]
        assert "ncbi_gene" in queries
        assert "Ai14" in queries["ncbi_gene"]

    def test_subject_genotype_composite(self):
        """Composite genotypes split on ; should produce separate queries."""
        queries = _extract_registry_queries("subject", {"genotype": "Ai14;Slc17a7-Cre"})
        assert queries["mgi"] == ["Ai14", "Slc17a7-Cre"]
        assert queries["ncbi_gene"] == ["Ai14", "Slc17a7-Cre"]

    def test_subject_genotype_slash_separator(self):
        queries = _extract_registry_queries("subject", {"genotype": "Emx1-Cre/Ai94"})
        assert "Emx1-Cre" in queries["mgi"]
        assert "Ai94" in queries["mgi"]

    def test_subject_no_genotype(self):
        queries = _extract_registry_queries("subject", {"subject_id": "123", "sex": "Male"})
        assert queries == {}

    def test_subject_short_genotype_ignored(self):
        """Genotype strings <= 2 chars should be ignored."""
        queries = _extract_registry_queries("subject", {"genotype": "wt"})
        assert queries == {}

    def test_subject_alleles(self):
        queries = _extract_registry_queries("subject", {
            "alleles": [{"name": "Ai14"}, {"name": "Slc17a7-Cre"}]
        })
        assert "mgi" in queries
        assert "Ai14" in queries["mgi"]
        assert "Slc17a7-Cre" in queries["mgi"]

    def test_procedures_nested_plasmid(self):
        """Plasmid names nested inside subject_procedures should be found."""
        queries = _extract_registry_queries("procedures", {
            "subject_procedures": [{
                "injection_materials": [{"name": "pAAV-EF1a-DIO-hChR2-EYFP"}],
            }]
        })
        assert "addgene" in queries
        assert "pAAV-EF1a-DIO-hChR2-EYFP" in queries["addgene"]

    def test_procedures_catalog_number(self):
        """Addgene catalog numbers (4-6 digits) should be extracted."""
        queries = _extract_registry_queries("procedures", {
            "injection_materials": "pAAV-EF1a (Addgene 26973)"
        })
        assert "addgene" in queries
        assert "26973" in queries["addgene"]

    def test_procedures_top_level_materials(self):
        queries = _extract_registry_queries("procedures", {
            "injection_materials": "pCAG-Cre into cortex"
        })
        assert "addgene" in queries
        assert "pCAG-Cre" in queries["addgene"]

    def test_procedures_no_plasmid(self):
        """Procedures without plasmid-like fields should produce no queries."""
        queries = _extract_registry_queries("procedures", {
            "procedure_type": "Craniotomy",
            "coordinates": {"x": 1.0, "y": 2.0},
        })
        assert queries == {}

    def test_session_no_queries(self):
        queries = _extract_registry_queries("session", {"session_start_time": "2025-01-01"})
        assert queries == {}

    def test_data_description_no_queries(self):
        queries = _extract_registry_queries("data_description", {"project_name": "Test"})
        assert queries == {}

    def test_queries_deduplicated(self):
        """Duplicate query terms should be removed."""
        queries = _extract_registry_queries("procedures", {
            "subject_procedures": [
                {"injection_materials": [{"name": "pAAV-EF1a"}]},
                {"injection_materials": [{"name": "pAAV-EF1a"}]},
            ]
        })
        assert queries["addgene"].count("pAAV-EF1a") == 1


class TestRegistrySummaryFormatter:
    """Test _format_registry_summary output."""

    def test_empty_results(self):
        assert _format_registry_summary([]) == ""

    def test_found_result_with_url(self):
        results = [{"registry": "mgi", "query": "Ai14", "found": True, "url": "https://mgi.org/Ai14"}]
        summary = _format_registry_summary(results)
        assert "REGISTRY LOOKUPS" in summary
        assert "FOUND" in summary
        assert "Ai14" in summary

    def test_not_found_result(self):
        results = [{"registry": "ncbi_gene", "query": "FakeGene", "found": False}]
        summary = _format_registry_summary(results)
        assert "NOT FOUND" in summary
        assert "FakeGene" in summary

    def test_error_result(self):
        results = [{"registry": "addgene", "query": "pAAV", "error": "timeout"}]
        summary = _format_registry_summary(results)
        assert "failed" in summary
        assert "timeout" in summary

    def test_ncbi_gene_result_with_details(self):
        results = [{
            "registry": "ncbi_gene",
            "query": "Slc17a7",
            "found": True,
            "results": [{"symbol": "Slc17a7", "description": "vesicular glutamate transporter", "url": "https://ncbi.nlm.nih.gov/gene/140919"}],
        }]
        summary = _format_registry_summary(results)
        assert "Slc17a7" in summary
        assert "vesicular glutamate" in summary

    def test_addgene_result_with_plasmid_details(self):
        """Addgene results with structured plasmid data should show names and IDs."""
        results = [{
            "registry": "addgene",
            "query": "AAV11",
            "found": True,
            "results": [
                {"catalog_number": "240486", "name": "pAAV2/11", "description": "AAV packaging plasmid expressing AAV2 Rep and AAV11 capsid", "url": "https://www.addgene.org/240486/"},
                {"catalog_number": "50465", "name": "pAAV-hSyn-EGFP", "description": "EGFP under human synapsin promoter", "url": "https://www.addgene.org/50465/"},
            ],
        }]
        summary = _format_registry_summary(results)
        assert "#240486" in summary
        assert "pAAV2/11" in summary
        assert "#50465" in summary
        assert "pAAV-hSyn-EGFP" in summary
        assert "AAV packaging plasmid" in summary

    def test_addgene_empty_results(self):
        """Addgene results with no matches should show NOT FOUND."""
        results = [{
            "registry": "addgene",
            "query": "xyznonexistent",
            "found": False,
            "results": [],
        }]
        summary = _format_registry_summary(results)
        assert "NOT FOUND" in summary


class TestParseAddgeneResults:
    """Tests for parsing Addgene search page content."""

    def setup_method(self):
        from agent.tools.registry_lookup import _parse_addgene_results
        self.parse = _parse_addgene_results

    def test_parse_markdown_links(self):
        """Should extract plasmid entries from markdown-style links."""
        text = """
Showing: 1 - 3 of 3 results

1. [pAAV2/11](/240486/)

    #240486

    Purpose

    AAV packaging plasmid expressing AAV2 Rep and AAV11 capsid

2. [pAAV-hSyn-EGFP](/50465/)

    #50465

    Purpose

    EGFP under human synapsin promoter

3. [pAAV-CAG-GFP](/37825/)

    #37825

    Purpose

    GFP under CAG promoter
"""
        results = self.parse(text)
        assert len(results) == 3
        assert results[0]["catalog_number"] == "240486"
        assert results[0]["name"] == "pAAV2/11"
        assert results[1]["catalog_number"] == "50465"
        assert results[1]["name"] == "pAAV-hSyn-EGFP"
        assert results[2]["catalog_number"] == "37825"

    def test_parse_descriptions(self):
        """Should extract purpose/description text for each plasmid."""
        text = """
[pAAV2/11](/240486/)

#240486

Purpose

AAV packaging plasmid expressing AAV2 Rep and AAV11 capsid
"""
        results = self.parse(text)
        assert len(results) == 1
        assert "AAV packaging plasmid" in results[0]["description"]

    def test_parse_empty_page(self):
        """No results page should return empty list."""
        text = "No results found for your query."
        results = self.parse(text)
        assert results == []

    def test_parse_generates_urls(self):
        """Each result should have a full Addgene URL."""
        text = "[pAAV-hSyn-EGFP](/50465/)"
        results = self.parse(text)
        assert len(results) == 1
        assert results[0]["url"] == "https://www.addgene.org/50465/"

    def test_max_results_limit(self):
        """Should respect the max_results parameter."""
        entries = "\n".join(f"[plasmid{i}](/{10000+i}/)" for i in range(10))
        results = self.parse(entries, max_results=3)
        assert len(results) == 3

    def test_deduplicates_by_catalog_number(self):
        """Same catalog number appearing twice should only produce one result."""
        text = "[pAAV2/11](/240486/)\n[pAAV2/11](/240486/)"
        results = self.parse(text)
        assert len(results) == 1


class TestExtractRegistryQueriesAAV:
    """Tests for extracting Addgene queries from AAV serotype names."""

    def test_bare_serotype_aav11(self):
        """AAV11 (bare serotype) should trigger an Addgene lookup."""
        data = {"subject_procedures": [{"injection_materials": [{"name": "AAV11"}]}]}
        queries = _extract_registry_queries("procedures", data)
        assert "addgene" in queries
        assert any("AAV11" in q for q in queries["addgene"])

    def test_plasmid_name_paav_hsyn(self):
        """pAAV-hSyn-EGFP should trigger an Addgene lookup."""
        data = {"injection_materials": [{"name": "pAAV-hSyn-EGFP"}]}
        queries = _extract_registry_queries("procedures", data)
        assert "addgene" in queries
        assert any("pAAV-hSyn-EGFP" in q for q in queries["addgene"])

    def test_aav_with_capsid_suffix(self):
        """AAV9 and similar should trigger lookup."""
        data = {"injection_materials": [{"name": "AAV9"}]}
        queries = _extract_registry_queries("procedures", data)
        assert "addgene" in queries
