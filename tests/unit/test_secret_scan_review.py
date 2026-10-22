"""Credential candidates must not disappear through broad fixture exclusions."""
import json
from pathlib import Path

import pytest

from scripts.security.check_trufflehog import main, reviewed


def candidate(raw, *, file=".env.example", verified=False):
    return {"DetectorName": "Postgres", "Raw": raw, "Verified": verified,
            "SourceMetadata": {"Data": {"Git": {"file": file}}}}


def example_url():
    text = Path(".env.example").read_text()
    # TruffleHog's Postgres detector reports the connection URL without the DB path.
    return next(line.split("=", 1)[1].rsplit("/", 1)[0] for line in text.splitlines()
                if line.startswith("# SUPABASE_DB_URL="))


def test_review_requires_exact_value_path_and_unverified_status():
    raw = example_url()
    assert reviewed(candidate(raw))
    assert not reviewed(candidate(raw + "changed"))
    assert not reviewed(candidate(raw, file="config/secrets.yaml"))
    assert not reviewed(candidate(raw, verified=True))


def test_unmatched_candidate_blocks_without_printing_value(tmp_path, capsys):
    report = tmp_path / "report.jsonl"
    value = "fictional-credential-candidate"
    report.write_text(json.dumps(candidate(value)) + "\n")
    assert main(str(report)) == 1
    assert value not in capsys.readouterr().out


def test_scanner_error_cannot_pass_as_an_empty_report(tmp_path):
    report = tmp_path / "report.jsonl"
    report.write_text(json.dumps({"level": "error", "msg": "scan failed"}))
    with pytest.raises(RuntimeError):
        main(str(report))
