"""Round 20 V1 closure and completed-semantic consumer parity."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

from verity.cli import _gate_from_report
from verity.closure import evaluate_v1_closure
from verity.corpus import _review_semantic_case, load_semantic_replay
from verity.report import review_to_dict, to_html
from verity.sarif import review_to_sarif
from verity.web.view import build_view_model


ENGINEERING_GREEN = {
    "prompt_web_cli": True,
    "skill_web_cli": True,
    "json_html_sarif": True,
    "coverage_failure": True,
    "score_confidence_remediation": True,
    "history_v1_v2_diff": True,
    "security_boundaries": True,
    "install_start_preflight": True,
    "tests_and_ci": True,
}


def semantic_case(finding_type, assessment="confirmed"):
    return next(c for c in load_semantic_replay()["cases"]
                if c["findingType"] == finding_type
                and c["expectedAssessment"] == assessment)


def test_current_closure_is_engineering_ready_but_quality_not_ready():
    report = evaluate_v1_closure(engineering_checks=ENGINEERING_GREEN)
    assert report["decision"] == "not_ready"
    assert report["engineeringReady"] is True
    assert report["qualityEvidenceReady"] is False
    codes = {x["code"] for x in report["blockers"]}
    assert codes == {
        "evaluation_labels_provisional",
        "accepted_real_model_selection_absent",
        "sealed_semantic_test_unconsumed",
        "no_substantial_or_evaluated_risk_coverage",
    }
    assert report["evidenceSummary"]["evaluatedLayerCount"] == 0
    assert report["evidenceSummary"]["acceptedRealModelSelectionPresent"] is False
    assert report["evidenceSummary"]["sealedTestConsumed"] is False
    assert all(x["code"].startswith(("v1_5", "v2_", "provider_"))
               for x in report["deferred"])


def test_engineering_failure_is_separate_blocker():
    checks = dict(ENGINEERING_GREEN); checks["json_html_sarif"] = False
    report = evaluate_v1_closure(engineering_checks=checks)
    assert report["engineeringReady"] is False
    assert any(x["code"] == "engineering_check_failed:json_html_sarif"
               for x in report["blockers"])


def test_closure_rejects_missing_or_unknown_check_names():
    with pytest.raises(ValueError):
        evaluate_v1_closure(engineering_checks={})
    bad = dict(ENGINEERING_GREEN); bad["marketing_green"] = True
    with pytest.raises(ValueError):
        evaluate_v1_closure(engineering_checks=bad)


def test_confirmed_high_semantic_finding_blocks_web_cli_html_and_sarif():
    case = semantic_case("semantic.skill.external_instruction_trust_gap")
    review, _, _ = _review_semantic_case(case)
    report = review_to_dict(review)
    assert report["semantic"]["status"] == "completed"
    assert report["semantic"]["findings"][0]["severity"] == "high"
    assert "L1_semantic" in report["score"]["includedLayers"]
    assert report["score"]["evaluatedLayers"] == ["L0_static", "L1_semantic"]
    assert report["score"]["value"] <= 59
    assert report["verdict"]["subject"]["outcome"] == "do_not_install"
    assert report["verdict"]["policyVersion"] == "2"
    assert "high_or_critical_finding_present" in report["verdict"]["reasonCodes"]

    gate, code, finding_count, high_count = _gate_from_report(
        report, semantic_requested=True)
    assert (gate, code) == ("findings_block", 1)
    assert finding_count >= 1 and high_count >= 1

    view = build_view_model(report, "rid")
    assert view["headline"]["tone"] == "bad"
    semantic_view = next(x for x in view["findings"]
                         if x["type"] == case["findingType"])
    assert semantic_view["sourceLayer"] == "L1_semantic"
    assert any(x["sourceLayer"] == "L1_semantic"
               for x in report["remediations"])

    html = to_html(review)
    assert case["findingType"] in html
    sarif = review_to_sarif(report)
    results = sarif["runs"][0]["results"]
    semantic_result = next(x for x in results
                           if x["ruleId"] == case["findingType"])
    assert semantic_result["level"] == "error"
    assert semantic_result["properties"]["verity.sourceLayer"] == "L1_semantic"
    descriptor = next(x for x in sarif["runs"][0]["tool"]["driver"]["rules"]
                      if x["id"] == case["findingType"])
    assert "engine:skill" in descriptor["properties"]["tags"]
    props = sarif["runs"][0]["properties"]
    assert props["verity.score.value"] == report["score"]["value"]
    assert props["verity.reviewConfidence.grade"] == report["reviewConfidence"]["grade"]


def test_rejected_semantic_candidate_never_becomes_finding_or_deduction():
    case = semantic_case("semantic.prompt.excessive_tool_scope", "rejected")
    review, _, _ = _review_semantic_case(case)
    report = review_to_dict(review)
    assert report["semantic"]["status"] == "completed"
    assert report["semantic"]["findings"] == []
    assert all(x["findingType"] != case["findingType"]
               for x in report["findings"])
    assert all(x["findingType"] != case["findingType"]
               for x in report["score"]["deductions"])
    assert all(x["ruleId"] != case["findingType"]
               for x in review_to_sarif(report)["runs"][0]["results"])


def test_local_package_install_preflight_is_offline_and_versioned(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy(root / "pyproject.toml", source / "pyproject.toml")
    shutil.copy(root / "setup.py", source / "setup.py")
    shutil.copy(root / "README.md", source / "README.md")
    shutil.copytree(root / "src", source / "src",
                    ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"))
    target = tmp_path / "installed"
    env = dict(os.environ)
    env.update({"PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps",
         "--no-build-isolation", "--target", str(target), str(source)],
        capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    check = subprocess.run(
        [sys.executable, "-c",
         "import verity; print(verity.__version__)"],
        capture_output=True, text=True,
        env={**env, "PYTHONPATH": str(target)}, timeout=30)
    assert check.returncode == 0, check.stderr
    assert check.stdout.strip() == "0.1.0"
    assert (target / "verity" / "web" / "static" / "index.html").is_file()
    assert (target / "bin" / "verity").is_file()


def test_packaging_versions_and_descriptions_stay_aligned():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    setup = (root / "setup.py").read_text()
    init = (root / "src/verity/__init__.py").read_text()
    for text in (pyproject, setup, init):
        assert '0.1.0' in text
    phrase = "local read-only Prompt & Skill auditor (V1 engineering preview)"
    assert phrase in pyproject and phrase in setup


def test_static_html_and_web_assets_preserve_closure_language():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text()
    index = (root / "src/verity/web/static/index.html").read_text()
    assert "Score is not a safety guarantee" in readme
    assert 'never “100% safe”' in readme
    assert "guarantees 100% safe" not in readme.lower()
    assert "安全分只评价本次实际完成的检查" in index
