# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
import json
import pytest
from evals.chad_entity_extraction_probe import validate, route, grade, CASES


def test_rejects_invented_spans_and_duplicate_json():
    with pytest.raises(ValueError, match="span"):
        validate(json.dumps({"entities": [{"mention": "invented", "kind": "person", "parent": ""}],
                             "dependencies": []}), "email Jordan")
    with pytest.raises(ValueError, match="Duplicate JSON"):
        validate('{"entities":[],"entities":[],"dependencies":[]}', "task")


def test_person_resolution_preserves_source_and_conflicts():
    value = {"entities": [{"mention": "Jordan", "kind": "person", "parent": ""}], "dependencies": []}
    facts = [{"name": "Jordan", "email": "jordan@example.test", "source": "fixture"}]
    result = route(value, facts)[0]
    assert result["status"] == "resolved" and result["facts"] == facts
    facts.append({"name": "Jordan", "email": "different@example.test", "source": "fixture2"})
    result = route(value, facts)[0]
    assert result["status"] == "ambiguous" and result["email"] is None
    assert "query" not in result


def test_local_and_unknown_candidates_never_generate_public_queries():
    value = {"entities": [{"mention": n, "kind": k, "parent": ""} for n, k in
        [("Jordan", "person"), ("Draftout", "local_instance"), ("report.py", "local_file"),
         ("VellumForge", "unknown"), ("code", "artifact")]], "dependencies": []}
    assert all("query" not in row for row in route(value, []))


def test_semantic_grade_does_not_accept_syntax_as_success():
    value = validate('{"entities":[],"dependencies":[]}', CASES[0]["request"])
    assert not grade(CASES[0], value)["passed"]
