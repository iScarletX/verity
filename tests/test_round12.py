import json, os, stat
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from verity.baseline import compare
from verity.history import HistoryStore, HistoryError, _atomic_json, _strict_load
from verity.intake import intake_directory
from verity.models import Finding, CoverageAssessment
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
    def fail(src,dst): raise OSError("interrupt")
    with pytest.raises(OSError): _atomic_json(pp,{"schemaVersion":1},replace=fail)
    assert pp.read_bytes()==good

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
        assert "artifactId" not in c.get("/").text

def test_standalone_web_regression(tmp_path):
    with TestClient(create_app(history_root=tmp_path/"h"),base_url="http://localhost") as c:
        assert c.post("/api/review/prompt",json={"text":"hello","prompt_kind":"user_prompt"}).status_code==200
