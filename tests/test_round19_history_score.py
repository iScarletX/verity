"""Round 19 persisted score projection and comparison compatibility."""
import json
from copy import deepcopy
from pathlib import Path

from verity.history import HistoryStore, _strict_load
from verity.intake import intake_directory
from verity.review import ReviewInputs, run_review


def add_version(store, project, root, *, risky=False, profile="minimal"):
    root.mkdir()
    if risky:
        (root / "SKILL.md").write_text(
            "---\nname: score-test\ndescription: Test.\nallowed-tools: '*'\n---\n")
    else:
        (root / "SKILL.md").write_text(
            "---\nname: score-test\ndescription: Reads one local note.\nallowed-tools: Read\n---\n")
    snap, data = intake_directory(root, artifact_id=project["artifactId"])
    review = run_review(ReviewInputs("skill", snap, data, profile=profile))
    return store.add_review(project["artifactId"], review, profile=profile)


def test_new_history_persists_allowlisted_score_and_compares(tmp_path):
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Score Test")
    first = add_version(store, project, tmp_path / "v1", risky=True)
    second = add_version(store, project, tmp_path / "v2", risky=False)
    assert first["schemaVersion"] == second["schemaVersion"] == 2
    assert first["score"]["status"] == "available"
    assert set(first["score"]) == {
        "status", "value", "policyId", "policyVersion", "highestSeverity",
        "deductionTotal", "severityCap", "includedLayers", "evaluatedLayers",
        "confidenceGrade", "confidencePolicyVersion"}
    diff = store.diff(project["artifactId"])
    sc = diff["scoreComparison"]
    assert sc["status"] == "comparable"
    assert sc["previous"] == first["score"]["value"]
    assert sc["current"] == second["score"]["value"]
    assert sc["delta"] == sc["current"] - sc["previous"]
    assert "does not itself prove remediation" in sc["note"]
    assert diff["counts"]["resolved"] >= 1


def test_v1_history_remains_readable_but_score_is_not_backfilled(tmp_path):
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Legacy")
    first = add_version(store, project, tmp_path / "v1", risky=True)
    version_path = (store.projects / project["artifactId"] / "versions"
                    / f'{first["reviewId"]}.json')
    legacy = json.loads(version_path.read_text())
    legacy["schemaVersion"] = 1
    legacy.pop("score")
    version_path.write_text(json.dumps(legacy))
    assert _strict_load(version_path)["schemaVersion"] == 1
    second = add_version(store, project, tmp_path / "v2", risky=False)
    versions = store.versions(project["artifactId"])
    assert "score" not in versions[0]
    assert versions[1]["score"] == second["score"]
    assert store.diff(project["artifactId"])["scoreComparison"] == {
        "status": "not_comparable",
        "reasonCodes": ["historical_score_unavailable"]}


def test_policy_or_coverage_change_refuses_comparison():
    base = {"coverage": {"status": "sufficient"}, "score": {
        "status": "available", "value": 80,
        "policyId": "verity-safety-score", "policyVersion": "1.0.0",
        "evaluatedLayers": ["L0_static"]}}
    changed = deepcopy(base); changed["score"]["policyVersion"] = "2.0.0"
    assert HistoryStore._score_comparison(base, changed)["reasonCodes"] == [
        "score_policy_changed"]
    incomplete = deepcopy(base); incomplete["coverage"]["status"] = "insufficient"
    assert HistoryStore._score_comparison(base, incomplete)["reasonCodes"] == [
        "coverage_or_score_unavailable"]
    semantic = deepcopy(base); semantic["score"]["evaluatedLayers"] = [
        "L0_static", "L1_semantic"]
    assert HistoryStore._score_comparison(base, semantic)["reasonCodes"] == [
        "evaluated_layers_changed"]


def test_disposition_is_not_part_of_persisted_raw_score(tmp_path):
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Disposition Score")
    record = add_version(store, project, tmp_path / "v1", risky=True)
    assert "disposition" not in record["score"]
    assert "accepted" not in json.dumps(record["score"]).lower()
