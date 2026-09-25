"""The D03 exclusion projection must retain dates and remove only unusable cases."""

from scripts.project_historical_minute_exclusions import project


def _source(cases):
    return {
        "version": "historical_minute_readiness_audit/v1",
        "selection_policy": "quality_only",
        "start_date": "2024-09-01",
        "end_date": "2024-09-30",
        "cases": cases,
    }


def test_projection_unions_missing_and_noncontinuous_per_case():
    result = project(_source([
        {"selection_date": "2024-09-02", "outcome_date": "2024-09-03",
         "candidate_codes": 3, "codes_without_bars": ["000001"],
         "codes_without_continuous_minute_pair": ["000001", "000002"]},
        {"selection_date": "2024-09-03", "outcome_date": "2024-09-04",
         "candidate_codes": 1, "codes_without_bars": [],
         "codes_without_continuous_minute_pair": []},
    ]), "audit-hash")

    assert result["case_count"] == result["ready_case_count"] == 2
    assert result["candidate_codes_original"] == 4
    assert result["candidate_codes_excluded"] == 2
    assert result["candidate_codes_after_exclusion"] == 2
    assert result["cases"][0]["excluded_codes"] == ["000001", "000002"]
    assert result["source_audit_sha256"] == "audit-hash"


def test_projection_blocks_a_date_with_no_usable_candidates():
    result = project(_source([
        {"selection_date": "2024-09-02", "outcome_date": "2024-09-03",
         "candidate_codes": 1, "codes_without_bars": ["000001"],
         "codes_without_continuous_minute_pair": []},
    ]), "hash")
    assert result["ready_case_count"] == 0
    assert result["cases"][0]["readiness_after_exclusion"] == "BLOCKED"


def test_projection_rejects_duplicate_selection_date():
    case = {"selection_date": "2024-09-02", "outcome_date": "2024-09-03",
            "candidate_codes": 1, "codes_without_bars": [],
            "codes_without_continuous_minute_pair": []}
    try:
        project(_source([case, case]), "hash")
    except ValueError as error:
        assert "duplicate selection date" in str(error)
    else:
        raise AssertionError("duplicate selection dates must be rejected")
