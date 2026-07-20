import hashlib, json, os, stat
from dataclasses import replace
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from verity.baseline import compare
from verity.cli import main as cli_main
from verity.history import (HistoryStore, HistoryError, _atomic_json,
                            _strict_load, project_review)
from verity.intake import intake_directory
from verity.models import (AnalysisPlanItem, CoverageAssessment, ExecutionRecord,
                           Finding, ReviewPlan, RuleMatchEvent)
from verity.review import ReviewInputs, run_review
from verity.web.app import create_app

class Tool:
    def __init__(self,status="completed"): self.status=status
    def run_on_snapshot(self,*a):
        if self.status=="completed":
            return type("R",(),dict(status="completed",toolName="x",toolVersion="1",exitCode=0,durationSeconds=0,stagedFileCount=0,pathMap={},results=[],reasonCode=None,toolPath="/private/tool",toolSha256="x"))()
        return type("R",(),dict(status="error",toolName="x",toolVersion="1",exitCode=1,durationSeconds=0,stagedFileCount=0,pathMap={},results=[],reasonCode="failed",toolPath="/private/tool",toolSha256="x"))()

def review(root, aid, text="---\nname: demo\ndescription: ok\n---\n", bandit="completed"):
    root.mkdir(); (root/"SKILL.md").write_text(text)
    snap, byts=intake_directory(root,artifact_id=aid)
    return run_review(ReviewInputs("skill",snap,byts,profile="minimal"),bandit_runner=Tool(bandit))

def finding(fid,fp,sk):
    return Finding(fid,"s",fp,"x",{},sk,"c","high",{"kind":"deterministic_rule"},[])


def persisted_finding(label, fp_label, sk_label):
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    return Finding(
        "F-" + digest(label)[:16], "s-" + "0" * 12, digest(fp_label),
        "skill.test", {}, digest(sk_label), "Controlled test finding", "high",
        {"kind": "deterministic_rule"}, [],
    )
def cov(status="sufficient"):
    return CoverageAssessment("c","r","p",1,status)

def test_five_diff_states_and_relevant_scope():
    prev=[finding("old-exact","fp1","a"),finding("old-change","fp2","b"),finding("old-resolve","fp3","c"),finding("old-unknown","fp4","d")]
    cur=[finding("new-exact","fp1","a"),finding("new-change","other","b"),finding("new","fp5","e")]
    rs=compare(prev,cur,previous_snapshot_id="p",current_snapshot_id="c",baseline_scope_id="x",current_coverage=cov(),required_plan_items={"old-resolve":["pi-ok"],"old-unknown":["pi-analyzer-bandit"]},current_execution_status={"pi-ok":"completed","pi-analyzer-bandit":"failed"})
    assert {r.state for r in rs} == {"new","existing","changed","resolved","unknown_due_to_coverage"}
    assert next(r for r in rs if r.previousFindingIds==["old-unknown"]).reasonCodes==["coverage_insufficient"]

def test_diff_refuses_cross_artifact_and_scope():
    with pytest.raises(ValueError): compare([],[],previous_snapshot_id="p",current_snapshot_id="c",baseline_scope_id="x",current_coverage=cov(),previous_artifact_id="a",current_artifact_id="b")
    with pytest.raises(ValueError): compare([],[],previous_snapshot_id="p",current_snapshot_id="c",baseline_scope_id="x",current_coverage=cov(),previous_scope_id="x",current_scope_id="y")

def test_identity_versions_safe_projection_and_no_autolink(tmp_path):
    store=HistoryStore(tmp_path/"data"); p=store.create_project("Demo","demo"); q=store.create_project("Demo 2","other")
    r1=review(tmp_path/"one",p["artifactId"]); r2=review(tmp_path/"two",p["artifactId"])
    a=store.add_review("demo",r1,profile="minimal"); b=store.add_review("demo",r2,profile="minimal")
    assert a["artifactId"]==b["artifactId"] and a["reviewId"]!=b["reviewId"]
    assert q["artifactId"]!=p["artifactId"]
    raw=b"".join(x.read_bytes() for x in (tmp_path/"data").rglob("*.json"))
    for forbidden in (b"SKILL.md",b"/private/tool",str(tmp_path).encode(),b"RedactionMap",b"providerPayload",b"apiKey"):
        assert forbidden not in raw
    with pytest.raises(HistoryError): store.add_review("other",r1,profile="minimal")
    with pytest.raises(HistoryError): store.resolve("a-"+"0"*32)

def test_storage_permissions_symlink_corrupt_oversize_and_atomicity(tmp_path):
    root=tmp_path/"data"; s=HistoryStore(root); p=s.create_project("Demo")
    assert stat.S_IMODE(root.stat().st_mode)==0o700
    pp=s._project_path(p["artifactId"]); assert stat.S_IMODE(pp.stat().st_mode)==0o600
    good=pp.read_bytes(); pp.write_text("{")
    with pytest.raises(HistoryError): s.get_project(p["artifactId"])
    pp.write_bytes(good); os.chmod(pp,0o666)
    with pytest.raises(HistoryError): s.list_projects()
    os.chmod(pp,0o600)
    victim=tmp_path/"victim"; victim.write_bytes(good); pp.unlink(); pp.symlink_to(victim)
    with pytest.raises(HistoryError): s.list_projects()
    pp.unlink(); pp.write_bytes(good); os.chmod(pp,0o600)
    huge=tmp_path/"huge"; huge.write_bytes(b"{"+b"x"*(1024*1024)+b"}")
    with pytest.raises(HistoryError): _strict_load(huge)
    unknown=tmp_path/"unknown"; unknown.write_text('{"schemaVersion":1,"recordType":"future"}')
    with pytest.raises(HistoryError): _strict_load(unknown)
    extra=tmp_path/"extra"; extra.write_text('{"schemaVersion":1,"recordType":"skillProject","artifactId":"a","displayName":"x","alias":null,"createdAt":"x","versionIds":[],"unexpected":true}')
    with pytest.raises(HistoryError): _strict_load(extra)
    def fail(src,dst): raise OSError("interrupt")
    with pytest.raises(OSError): _atomic_json(pp,{"schemaVersion":1},replace=fail)
    assert pp.read_bytes()==good

def test_web_uploads_reject_duplicate_and_case_colliding_paths(tmp_path):
    with TestClient(create_app(history_root=tmp_path / "history"),
                    base_url="http://localhost") as client:
        project = client.post(
            "/api/projects", json={"displayName": "Duplicates"}).json()["project"]
        files = [
            ("files", ("root/SKILL.md", b"first", "text/plain")),
            ("files", ("root/skill.md", b"second", "text/plain")),
        ]
        project_response = client.post(
            f'/api/projects/{project["artifactId"]}/versions', files=files,
            data={"profile": "minimal"})
        standalone_response = client.post(
            "/api/review/skill", files=files, data={"profile": "minimal"})
        for response in (project_response, standalone_response):
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "bad_path"


def test_project_version_upload_rejects_path_escape(tmp_path):
    with TestClient(create_app(history_root=tmp_path / "history"),
                    base_url="http://localhost") as client:
        project = client.post(
            "/api/projects", json={"displayName": "Paths"}).json()["project"]
        response = client.post(
            f'/api/projects/{project["artifactId"]}/versions',
            files=[("files", ("root/../../outside", b"x",
                              "application/octet-stream"))],
            data={"profile": "minimal"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "bad_path"


def test_web_project_context_cannot_be_overridden_by_upload(tmp_path):
    app = create_app(history_root=tmp_path / "history")
    with TestClient(app, base_url="http://localhost") as client:
        first = client.post("/api/projects", json={"displayName": "Same"})
        second = client.post("/api/projects", json={"displayName": "Same"})
        aid_a = first.json()["project"]["artifactId"]
        aid_b = second.json()["project"]["artifactId"]
        assert aid_a != aid_b
        identical = [
            ("files", ("root/SKILL.md",
                       b"---\nname: same\ndescription: same\n---\n",
                       "text/plain")),
        ]
        response = client.post(
            f"/api/projects/{aid_a}/versions", files=identical,
            data={"profile": "minimal", "artifactId": aid_b,
                  "project": aid_b, "baseline": aid_b},
        )
        assert response.status_code == 201, response.text
        assert response.json()["version"]["artifactId"] == aid_a
        assert len(client.get(f"/api/projects/{aid_a}").json()["versions"]) == 1
        assert len(client.get(f"/api/projects/{aid_b}").json()["versions"]) == 0


def test_web_e2e_create_submit_two_versions_and_diff(tmp_path):
    app=create_app(history_root=tmp_path/"history")
    with TestClient(app,base_url="http://localhost") as c:
        made=c.post("/api/projects",json={"displayName":"My Skill","alias":"mine"}); assert made.status_code==201
        pid=made.json()["project"]["artifactId"]
        assert len(c.get("/api/projects").json()["projects"])==1
        files=[("files",("root/SKILL.md",b"---\nname: x\ndescription: ok\n---\n","text/plain"))]
        first=c.post(f"/api/projects/{pid}/versions",files=files,data={"profile":"minimal"}); assert first.status_code==201, first.text
        files=[("files",("root/SKILL.md",b"---\nname: x\ndescription: changed\npermissions: '*'\n---\n","text/plain"))]
        second=c.post(f"/api/projects/{pid}/versions",files=files,data={"profile":"minimal"}); assert second.status_code==201, second.text
        page=c.get(f"/api/projects/{pid}").json(); assert len(page["versions"])==2
        d=c.get(f"/api/projects/{pid}/diff"); assert d.status_code==200 and "counts" in d.json()["diff"]
        assert all(set(change["summary"]) == {"findingType", "severity", "claim"}
                   for change in d.json()["diff"]["changes"])
        assert "artifactId" not in c.get("/").text

def test_history_diff_end_to_end_all_five_states(tmp_path):
    """The persisted-history workflow, not only the matcher, proves all states."""
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Five states", "five")
    aid = project["artifactId"]
    base = review(tmp_path / "base", aid)
    current_base = review(tmp_path / "current", aid)

    previous_findings = [
        persisted_finding("old-exact", "fp-exact", "subject-exact"),
        persisted_finding("old-change", "fp-change-old", "subject-change"),
        replace(persisted_finding(
                    "old-resolve", "fp-resolve", "subject-resolve"),
                origin={"kind": "deterministic_rule",
                        "ruleMatchEventIds": ["event-resolve"]}),
        replace(persisted_finding(
                    "old-unknown", "fp-unknown", "subject-unknown"),
                origin={"kind": "deterministic_rule",
                        "ruleMatchEventIds": ["event-unknown"]}),
    ]
    current_findings = [
        persisted_finding("new-exact", "fp-exact", "subject-exact"),
        persisted_finding("new-change", "fp-change-new", "subject-change"),
        persisted_finding("brand-new", "fp-new", "subject-new"),
    ]
    events = [
        RuleMatchEvent("event-resolve", base.artifactSnapshot.snapshotId,
                       "skill.rule.resolve", "1", [], "d1", "x1"),
        RuleMatchEvent("event-unknown", base.artifactSnapshot.snapshotId,
                       "skill.bandit.B602", "1", [], "d2", "x2"),
    ]
    plan_items = [
        AnalysisPlanItem("pi-skill.rule.resolve", "rule",
                         "skill.rule.resolve", "1", [], "required", "normal"),
        AnalysisPlanItem("pi-skill.bandit.B602", "rule",
                         "skill.bandit.B602", "1", [], "required", "normal"),
        AnalysisPlanItem("pi-analyzer-bandit", "analyzer",
                         "bandit", "1", [], "required", "normal"),
    ]
    current_executions = [
        ExecutionRecord("x1", "pi-skill.rule.resolve", "completed"),
        ExecutionRecord("x2", "pi-skill.bandit.B602", "completed"),
        ExecutionRecord("x3", "pi-analyzer-bandit", "failed"),
    ]
    previous = replace(
        base, findings=previous_findings, ruleMatches=events,
        plan=ReviewPlan(base.plan.reviewPlanId, base.reviewId,
                        1, "initial", 0, plan_items),
        executions=[
            ExecutionRecord("p1", "pi-skill.rule.resolve", "completed"),
            ExecutionRecord("p2", "pi-skill.bandit.B602", "completed"),
            ExecutionRecord("p3", "pi-analyzer-bandit", "completed"),
        ],
    )
    current = replace(
        current_base,
        findings=current_findings,
        plan=ReviewPlan(current_base.plan.reviewPlanId, current_base.reviewId,
                        1, "initial", 0, plan_items),
        executions=current_executions,
        coverage=replace(current_base.coverage, status="insufficient",
                         reasonCodes=["bandit_failed"]),
    )
    store.add_review("five", previous, profile="minimal")
    store.add_review("five", current, profile="minimal")
    diff = store.diff("five")
    assert diff["counts"] == {
        "new": 1, "existing": 1, "changed": 1,
        "resolved": 1, "unknown_due_to_coverage": 1,
    }


def test_history_rejects_corrupt_project_metadata_and_versions_symlink(tmp_path):
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Strict", "strict")
    project_path = store._project_path(project["artifactId"])
    original = project_path.read_bytes()
    corrupt = json.loads(original)
    corrupt["displayName"] = ""
    _atomic_json(project_path, corrupt)
    with pytest.raises(HistoryError):
        store.get_project(project["artifactId"])

    project_path.write_bytes(original)
    os.chmod(project_path, 0o600)
    record = store.add_review(
        "strict", review(tmp_path / "strict-v1", project["artifactId"]),
        profile="minimal")
    versions_dir = store.projects / project["artifactId"] / "versions"
    real_versions = tmp_path / "outside-versions"
    versions_dir.rename(real_versions)
    versions_dir.symlink_to(real_versions, target_is_directory=True)
    with pytest.raises(HistoryError):
        store.versions("strict")


def test_history_rejects_corrupt_nested_review_projection(tmp_path):
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Nested", "nested")
    record = store.add_review(
        "nested", review(tmp_path / "nested-v1", project["artifactId"]),
        profile="minimal")
    version_path = (store.projects / project["artifactId"] / "versions"
                    / f'{record["reviewId"]}.json')
    corrupt = json.loads(version_path.read_text())
    corrupt["findings"].append({"findingId": "../../outside"})
    _atomic_json(version_path, corrupt)
    with pytest.raises(HistoryError):
        store.versions("nested")

    valid = project_review(
        review(tmp_path / "nested-v2", project["artifactId"]),
        profile="minimal")
    valid["executions"][0]["planItemId"] = "pi-invented"
    _atomic_json(version_path, valid)
    with pytest.raises(HistoryError):
        store.versions("nested")


def test_history_rejects_malicious_version_id_before_path_resolution(tmp_path):
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Demo")
    path = store._project_path(project["artifactId"])
    record = json.loads(path.read_text())
    record["versionIds"] = ["../../outside"]
    _atomic_json(path, record)
    with pytest.raises(HistoryError):
        store.versions(project["artifactId"])


def test_symlinked_history_root_is_refused_without_chmod_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    root = tmp_path / "linked-history"
    root.symlink_to(target, target_is_directory=True)
    before = stat.S_IMODE(target.stat().st_mode)
    with pytest.raises(HistoryError):
        HistoryStore(root)
    assert stat.S_IMODE(target.stat().st_mode) == before


def test_project_version_and_total_budgets_are_visible(tmp_path, monkeypatch):
    import verity.history as history
    root = tmp_path / "history"
    monkeypatch.setattr(history, "MAX_PROJECTS", 1)
    store = HistoryStore(root)
    project = store.create_project("One", "one")
    with pytest.raises(HistoryError, match="project budget"):
        store.create_project("Two")

    monkeypatch.setattr(history, "MAX_VERSIONS", 1)
    first = review(tmp_path / "v1", project["artifactId"])
    second = review(tmp_path / "v2", project["artifactId"])
    store.add_review("one", first, profile="minimal")
    with pytest.raises(HistoryError, match="version budget"):
        store.add_review("one", second, profile="minimal")

    other_root = tmp_path / "total"
    monkeypatch.setattr(history, "MAX_PROJECTS", 128)
    total_store = HistoryStore(other_root)
    total_project = total_store.create_project("Total", "total")
    monkeypatch.setattr(history, "MAX_TOTAL_BYTES", 1)
    total_review = review(tmp_path / "v3", total_project["artifactId"])
    with pytest.raises(HistoryError, match="total history budget"):
        total_store.add_review("total", total_review, profile="minimal")


def test_cli_project_review_uses_review_gate(tmp_path, capsys):
    data = tmp_path / "history"
    assert cli_main(["project", "--data-dir", str(data), "create",
                     "--name", "Risky", "--alias", "risky"]) == 0
    rc = cli_main(["project", "--data-dir", str(data), "review",
                   "--project", "risky", "--input-dir",
                   str(Path(__file__).parent / "fixtures" /
                       "risky_permissions_skill"), "--profile", "minimal"])
    assert rc == 1
    assert "gate=findings_block" in capsys.readouterr().out


def test_history_store_serializes_concurrent_version_appends(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = HistoryStore(tmp_path / "history")
    project = store.create_project("Concurrent", "concurrent")
    aid = project["artifactId"]
    reviews = [review(tmp_path / f"concurrent-{i}", aid,
                      text=f"---\nname: demo-{i}\ndescription: ok\n---\n")
               for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(
            lambda r: store.add_review("concurrent", r, profile="minimal"),
            reviews))
    assert len(records) == 4
    assert len(store.versions("concurrent")) == 4
    assert len(store.get_project("concurrent")["versionIds"]) == 4


def test_project_web_defaults_to_standard_secret_scan(tmp_path):
    with TestClient(create_app(history_root=tmp_path / "h"),
                    base_url="http://localhost") as c:
        html = c.get("/").text
        js = c.get("/static/app.js").text
        assert 'id="project-profile"' in html
        assert '<option value="standard"' in html
        assert 'fd.append("profile",$("project-profile").value)' in js
        assert "d.changes.forEach" in js
        assert "本轮相关检查未完整完成" in js


def test_standalone_web_regression(tmp_path):
    with TestClient(create_app(history_root=tmp_path/"h"),base_url="http://localhost") as c:
        assert c.post("/api/review/prompt",json={"text":"hello","prompt_kind":"user_prompt"}).status_code==200
