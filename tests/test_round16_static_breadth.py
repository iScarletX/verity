"""Round 16 standards-driven static breadth tests."""
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from verity.builtins import (build_finding_type_registry,
                              build_skill_rule_registry)
from verity.intake import IntakeError, intake_directory
from verity.review import ReviewInputs, run_review
from verity.web.app import create_app


class NoLeaks:
    def run_on_snapshot(self, snapshot, file_bytes):
        from verity.gitleaks_runner import GitleaksRunResult
        return GitleaksRunResult(
            status="completed", toolVersion="8.28.0", toolPath="test",
            stagedFileCount=len(file_bytes), pathMap={}, results=[])


def make_skill(tmp_path, root_name, frontmatter):
    root = tmp_path / root_name
    root.mkdir()
    (root / "SKILL.md").write_text("---\n" + frontmatter + "---\nbody\n")
    snap, data = intake_directory(root)
    return run_review(ReviewInputs("skill", snap, data),
                      gitleaks_runner=NoLeaks())


def field_issues(review):
    return {(f.subject.get("fieldName"), f.subject.get("fieldIssue"))
            for f in review.findings
            if f.findingType == "skill.manifest_field_issue"}


def test_name_exact_official_syntax_and_directory_match(tmp_path):
    good = make_skill(tmp_path, "valid-skill",
                      "name: valid-skill\ndescription: Valid synthetic skill.\n")
    assert not field_issues(good)
    assert good.artifactModel["agentSkillsSpec"] == {
        "specId": "agentskills.io/specification",
        "snapshot": "retrieved-2026-07-21",
    }


@pytest.mark.parametrize("name,issue", [
    ("Upper-Case", "invalid_syntax"),
    ("under_score", "invalid_syntax"),
    ("double--hyphen", "invalid_syntax"),
    ("-leading", "invalid_syntax"),
    ("trailing-", "invalid_syntax"),
    ("a" * 65, "too_long"),
])
def test_name_rejects_official_boundary_violations(tmp_path, name, issue):
    review = make_skill(tmp_path, "root-name",
                        f"name: {name}\ndescription: Synthetic.\n")
    assert ("name", issue) in field_issues(review)


def test_name_must_match_root_directory(tmp_path):
    review = make_skill(tmp_path, "actual-root",
                        "name: different-name\ndescription: Synthetic.\n")
    assert ("name", "directory_mismatch") in field_issues(review)
    assert review.artifactSnapshot.artifactRootName == "actual-root"
    assert "/" not in review.artifactSnapshot.artifactRootName


def test_required_field_types_are_not_misreported_as_blank(tmp_path):
    review = make_skill(tmp_path, "required-types",
                        "name: 42\ndescription: [not, a, string]\n")
    assert ("name", "invalid_type") in field_issues(review)
    assert ("description", "invalid_type") in field_issues(review)


def test_description_and_optional_field_constraints(tmp_path):
    long_description = "x" * 1025
    long_compatibility = "x" * 501
    review = make_skill(tmp_path, "field-boundaries", f"""
name: field-boundaries
description: {long_description}
compatibility: {long_compatibility}
metadata:
  owner: 42
allowed-tools:
  - Read
""".lstrip())
    issues = field_issues(review)
    assert ("description", "too_long") in issues
    assert ("compatibility", "too_long") in issues
    assert ("metadata", "invalid_value") in issues
    assert ("allowed-tools", "invalid_type") in issues


def test_valid_official_optional_fields(tmp_path):
    review = make_skill(tmp_path, "official-fields", """
name: official-fields
description: Reads a supplied file and returns a summary.
compatibility: Requires a local text file.
metadata:
  author: synthetic
  version: "1.0"
allowed-tools: Read Grep
""".lstrip())
    assert not field_issues(review)
    assert review.artifactModel["manifest"]["permissions"] == ["Read", "Grep"]


def test_capability_facts_are_static_bounded_and_not_findings(tmp_path):
    root = tmp_path / "capability-facts"
    root.mkdir()
    (root / "SKILL.md").write_text("""---
name: capability-facts
description: Synthetic capability fact fixture.
allowed-tools: Read Grep
---
""")
    (root / "requirements.txt").write_text("sample-package==1.2.3\n")
    (root / "run.py").write_text("""
import os
import requests
import subprocess
from pathlib import Path
secret_name = os.getenv("SYNTHETIC_NAME")
text = Path("input.txt").read_text()
response = requests.get("https://example.invalid")
subprocess.run(["/bin/echo", text], check=True)
quoted = "os.system('not a real call')"
class Reader:
    def read_text(self):
        return "not a filesystem object"
Reader().read_text()
""")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    facts = review.artifactModel["capabilityFacts"]
    categories = {f["category"] for f in facts["facts"]}
    assert {"tool", "installation", "network", "process", "file",
            "credential"} <= categories
    assert not any(f["operation"] == "os.system" for f in facts["facts"])
    assert sum(f["category"] == "file" for f in facts["facts"]) == 1
    assert all(not f["artifactPath"].startswith("/") for f in facts["facts"])
    assert facts["limitations"] == [
        "python_ast_and_manifest_only", "no_cross_file_dataflow",
        "no_runtime_observation"]
    assert not any(f.findingType == "skill.capability_observation"
                   for f in review.findings)


def test_parser_and_rules_declare_versioned_spec_migration():
    registry = build_skill_rule_registry(build_finding_type_registry())
    name = registry.get("skill.manifest_name_issue", "2.0.0")
    desc = registry.get("skill.manifest_description_missing", "2.0.0")
    assert name.supersedes == ["skill.manifest_name_issue@1.0.0"]
    assert desc.supersedes == ["skill.manifest_description_missing@1.0.0"]
    assert registry.get("skill.manifest_optional_field_issue", "1.0.0")


def test_intake_root_name_override_is_bounded_and_path_free(tmp_path):
    root = tmp_path / "temporary-container"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: browser-root\ndescription: Browser upload.\n---\n")
    snap, _ = intake_directory(root, artifact_root_name="browser-root")
    assert snap.artifactRootName == "browser-root"
    with pytest.raises(IntakeError):
        intake_directory(root, artifact_root_name="../escape")


def test_web_upload_uses_browser_root_not_temp_directory(tmp_path):
    with TestClient(create_app(history_root=tmp_path / "history"),
                    base_url="http://localhost") as client:
        files = [("files", ("browser-root/SKILL.md",
                            b"---\nname: browser-root\ndescription: Web.\n---\n",
                            "text/plain"))]
        response = client.post("/api/review/skill", files=files,
                               data={"profile": "minimal"})
        assert response.status_code == 200, response.text
        issues = {(f.get("subject") or {}).get("fieldIssue")
                  for f in response.json()["findings"]
                  if f["type"] == "skill.manifest_field_issue"}
        assert "directory_mismatch" not in issues


def test_web_rejects_mixed_upload_roots(tmp_path):
    with TestClient(create_app(history_root=tmp_path / "history"),
                    base_url="http://localhost") as client:
        response = client.post("/api/review/skill", files=[
            ("files", ("one/SKILL.md", b"---\nname: one\ndescription: One.\n---\n", "text/plain")),
            ("files", ("two/x.txt", b"x", "text/plain")),
        ], data={"profile": "minimal"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "bad_path"
