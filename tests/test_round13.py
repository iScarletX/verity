"""Round 13 tests: Disposition and Suppression."""
import json
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from verity.cli import main as cli_main
from verity.history import HistoryStore, HistoryError, _atomic_json
from verity.models import Finding
from verity.web.app import create_app


def test_disposition_basic_lifecycle(tmp_path):
    """Add, list, and expiry of dispositions."""
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Test", "test")
    
    # Valid fingerprint (64 hex chars)
    fp = "a" * 64
    future = datetime.now(timezone.utc) + timedelta(days=30)
    
    # Add disposition
    event = store.add_disposition(
        "test", fp, "accept_risk", future, "Test note")
    assert event["status"] == "accept_risk"
    assert "Test note" in event.get("note", "")
    
    # List active dispositions
    disps = store.list_dispositions("test")
    assert len(disps) == 1
    assert disps[0]["fingerprint"] == fp
    
    # Add another status on same fingerprint (overlay)
    future2 = datetime.now(timezone.utc) + timedelta(days=60)
    event2 = store.add_disposition("test", fp, "false_positive", future2)
    
    # Should see only the latest
    disps = store.list_dispositions("test")
    assert len(disps) == 1
    assert disps[0]["status"] == "false_positive"
    
    # Expired disposition not returned
    past = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(HistoryError, match="future"):
        store.add_disposition("test", "b" * 64, "wont_fix", past)


def test_disposition_validation(tmp_path):
    """Invalid inputs are rejected."""
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Test")
    future = datetime.now(timezone.utc) + timedelta(days=30)
    
    # Bad fingerprint
    with pytest.raises(HistoryError, match="fingerprint"):
        store.add_disposition("Test", "not-hex", "accept_risk", future)
    
    # Bad status
    with pytest.raises(HistoryError, match="status"):
        store.add_disposition("Test", "a" * 64, "ignore", future)
    
    # Note too long
    with pytest.raises(HistoryError, match="note"):
        store.add_disposition("Test", "a" * 64, "accept_risk", future, "x" * 201)
    
    # Expiry too far
    too_far = datetime.now(timezone.utc) + timedelta(days=200)
    with pytest.raises(HistoryError, match="exceed"):
        store.add_disposition("Test", "a" * 64, "accept_risk", too_far)


def test_disposition_enriches_diff(tmp_path):
    """Diff shows disposition status on findings."""
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Test", "test")
    
    # Create two versions with a finding
    # Use 64 lowercase hex fingerprint
    fp = "f" * 64
    sk = "5" * 64
    finding = Finding("F-1234567890abcdef", "s-123456789012", fp, "test", {}, sk, "claim", "high",
                      {"kind": "deterministic_rule"}, [])
    
    from verity.intake import intake_directory
    from verity.review import ReviewInputs, run_review
    
    root1 = tmp_path / "v1"
    root1.mkdir()
    (root1 / "SKILL.md").write_text("---\nname: test\n---\n")
    snap1, byts1 = intake_directory(root1, artifact_id=project["artifactId"])
    review1 = run_review(ReviewInputs("skill", snap1, byts1, profile="minimal"))
    review1 = replace(review1, findings=[finding])
    store.add_review("test", review1, profile="minimal")
    
    root2 = tmp_path / "v2"
    root2.mkdir()
    (root2 / "SKILL.md").write_text("---\nname: test v2\n---\n")
    snap2, byts2 = intake_directory(root2, artifact_id=project["artifactId"])
    review2 = run_review(ReviewInputs("skill", snap2, byts2, profile="minimal"))
    review2 = replace(review2, findings=[finding])
    store.add_review("test", review2, profile="minimal")
    
    # Add disposition
    future = datetime.now(timezone.utc) + timedelta(days=30)
    store.add_disposition("test", fp, "accept_risk", future, "Known issue")
    
    # Check diff
    diff = store.diff("test")
    assert diff["notedCounts"]["accept_risk"] == 1
    change = next(c for c in diff["changes"] if c["state"] == "existing")
    assert change["disposition"]["status"] == "accept_risk"
    assert change["disposition"]["note"] == "Known issue"


def test_cli_disposition_commands(tmp_path):
    """CLI dispose and dispositions commands."""
    data_dir = tmp_path / "data"
    
    # Create project
    exit_code = cli_main([
        "project", "--data-dir", str(data_dir),
        "create", "--name", "CLI Test", "--alias", "cli"
    ])
    assert exit_code == 0
    
    # Add disposition
    fp = "d" * 64
    exit_code = cli_main([
        "project", "--data-dir", str(data_dir),
        "dispose", "--project", "cli",
        "--fingerprint", fp,
        "--status", "false_positive",
        "--expiry", "90",
        "--note", "Test from CLI"
    ])
    assert exit_code == 0
    
    # List dispositions
    exit_code = cli_main([
        "project", "--data-dir", str(data_dir),
        "dispositions", "--project", "cli"
    ])
    assert exit_code == 0


def test_cli_respect_dispositions_gate(tmp_path):
    """--respect-dispositions affects exit code."""
    from verity.intake import intake_directory
    from verity.review import ReviewInputs, run_review
    
    data_dir = tmp_path / "data"
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: risky\npermissions: '*'\n---\n")
    
    # Create project
    cli_main(["project", "--data-dir", str(data_dir), 
              "create", "--name", "Gate Test", "--alias", "gate"])
    
    # First review: should fail (high finding)
    exit_code = cli_main([
        "project", "--data-dir", str(data_dir),
        "review", "--project", "gate",
        "--input-dir", str(skill_dir),
        "--profile", "minimal"
    ])
    assert exit_code == 1  # findings_block
    
    # Find the fingerprint from stored version
    store = HistoryStore(data_dir)
    versions = store.versions("gate")
    assert len(versions) == 1
    high_finding = next(f for f in versions[0]["findings"] 
                        if f["severity"] == "high")
    fp = high_finding["fingerprint"]
    
    # Add accept_risk disposition
    future = datetime.now(timezone.utc) + timedelta(days=30)
    store.add_disposition("gate", fp, "accept_risk", future)
    
    # Review without --respect-dispositions: still fails
    exit_code = cli_main([
        "project", "--data-dir", str(data_dir),
        "review", "--project", "gate",
        "--input-dir", str(skill_dir),
        "--profile", "minimal"
    ])
    assert exit_code == 1
    
    # Review with --respect-dispositions: passes
    exit_code = cli_main([
        "project", "--data-dir", str(data_dir),
        "review", "--project", "gate",
        "--input-dir", str(skill_dir),
        "--profile", "minimal",
        "--respect-dispositions"
    ])
    assert exit_code == 0  # accepted risk, gate passes


def test_disposition_rate_limit(tmp_path):
    """100 events/minute/project rate limit."""
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Test")
    
    future = datetime.now(timezone.utc) + timedelta(days=1)
    
    # Add 100 dispositions quickly
    for i in range(100):
        fp = f"{i:064x}"
        store.add_disposition(project["artifactId"], fp, "acknowledged", future)
    
    # 101st should fail
    with pytest.raises(HistoryError, match="rate limit"):
        store.add_disposition(project["artifactId"], "9" * 64, "acknowledged", future)


def test_disposition_storage_safety(tmp_path):
    """Symlinks, corruption, and size limits."""
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Test")
    
    # Add one disposition
    fp = "e" * 64
    future = datetime.now(timezone.utc) + timedelta(days=30)
    store.add_disposition(project["artifactId"], fp, "wont_fix", future)
    
    disp_dir = store.projects / project["artifactId"] / "dispositions"
    disp_file = disp_dir / f"{fp}.json"
    
    # Replace with symlink
    disp_file.unlink()
    disp_file.symlink_to("/etc/passwd")
    
    with pytest.raises(HistoryError):
        store.list_dispositions("Test")
    
    # Corrupt JSON
    disp_file.unlink()
    disp_file.write_text("{corrupt")
    
    with pytest.raises(HistoryError):
        store.list_dispositions("Test")
    
    # Exceed per-fingerprint event limit
    disp_file.unlink()
    events = [{"status": "acknowledged", 
               "expiryDate": future.isoformat(),
               "createdAt": datetime.now(timezone.utc).isoformat(),
               "createdBy": "test"} for _ in range(33)]
    _atomic_json(disp_file, {
        "schemaVersion": 1,
        "recordType": "dispositionHistory",
        "fingerprint": fp,
        "events": events
    })
    
    with pytest.raises(HistoryError):
        store.list_dispositions("Test")


def test_web_disposition_api(tmp_path):
    """Web endpoints for dispositions."""
    with TestClient(create_app(history_root=tmp_path / "history"),
                    base_url="http://localhost") as client:
        # Create project
        p = client.post("/api/projects", 
                        json={"displayName": "Web Test"}).json()["project"]
        
        # Add disposition
        fp = "c" * 64
        response = client.post(
            f"/api/projects/{p['artifactId']}/dispositions/{fp}",
            json={"status": "acknowledged", "expiryDays": 45, 
                  "note": "From web"}
        )
        assert response.status_code == 201
        disp = response.json()["disposition"]
        assert disp["status"] == "acknowledged"
        assert disp["createdBy"] == "web"
        
        # List dispositions
        response = client.get(f"/api/projects/{p['artifactId']}/dispositions")
        assert response.status_code == 200
        assert len(response.json()["dispositions"]) == 1
        
        # Invalid status
        response = client.post(
            f"/api/projects/{p['artifactId']}/dispositions/{fp}",
            json={"status": "ignore_forever"}
        )
        assert response.status_code == 400
        
        # Invalid expiry
        response = client.post(
            f"/api/projects/{p['artifactId']}/dispositions/{fp}",
            json={"status": "accept_risk", "expiryDays": 365}
        )
        assert response.status_code == 400


def test_disposition_cannot_affect_resolved(tmp_path):
    """Resolved/unknown_due_to_coverage findings cannot be dispositioned."""
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Test", "test")
    
    # Two versions: finding in v1, resolved in v2
    from verity.intake import intake_directory
    from verity.review import ReviewInputs, run_review
    from verity.models import AnalysisPlanItem, ExecutionRecord
    
    fp = "9" * 64
    sk = "7" * 64
    finding = Finding("F-9999999999999999", "s-999999999999", fp, "test", {}, sk, "claim", "high",
                      {"kind": "deterministic_rule", "ruleMatchEventIds": ["e1"]}, [])
    
    root1 = tmp_path / "v1"
    root1.mkdir()
    (root1 / "SKILL.md").write_text("---\nname: test\n---\n")
    snap1, _ = intake_directory(root1, artifact_id=project["artifactId"])
    review1 = run_review(ReviewInputs("skill", snap1, {}, profile="minimal"))
    
    # Inject plan items and executions so the finding has required scope
    from verity.models import RuleMatchEvent
    plan_item = AnalysisPlanItem("pi-skill.test", "rule", "skill.test", "1.0", [], "required", "normal")
    review1 = replace(review1, 
                      findings=[finding],
                      plan=replace(review1.plan, items=[plan_item]),
                      executions=[ExecutionRecord("x1", "pi-skill.test", "completed")],
                      ruleMatches=[RuleMatchEvent("e1", snap1.snapshotId, "skill.test", "1.0", [], "d", "x")])
    store.add_review("test", review1, profile="minimal")
    
    root2 = tmp_path / "v2"
    root2.mkdir()
    (root2 / "SKILL.md").write_text("---\nname: test v2\n---\n")
    snap2, _ = intake_directory(root2, artifact_id=project["artifactId"])
    review2 = run_review(ReviewInputs("skill", snap2, {}, profile="minimal"))
    # No findings but same plan/execution to mark as resolved
    review2 = replace(review2, 
                      findings=[],
                      plan=replace(review2.plan, items=[plan_item]),
                      executions=[ExecutionRecord("x2", "pi-skill.test", "completed")])
    store.add_review("test", review2, profile="minimal")
    
    # Add disposition on the fingerprint
    future = datetime.now(timezone.utc) + timedelta(days=30)
    store.add_disposition("test", fp, "accept_risk", future)
    
    # Check diff: resolved finding should NOT have disposition
    diff = store.diff("test")
    # When finding is resolved with proper coverage, baseline marks "resolved"
    states = [c["state"] for c in diff["changes"]]
    assert "resolved" in states or "unknown_due_to_coverage" in states
    
    # Either way, disposition should not affect it
    non_active = next(c for c in diff["changes"] 
                      if c["state"] in ("resolved", "unknown_due_to_coverage"))
    assert "disposition" not in non_active
    assert diff["notedCounts"]["accept_risk"] == 0