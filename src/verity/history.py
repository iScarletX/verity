"""Trusted local Skill project identity and safe immutable review history."""
from __future__ import annotations

import json, os, secrets, stat, tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baseline import compare
from .models import Finding, CoverageAssessment, AnalysisPlanItem, ExecutionRecord

SCHEMA_VERSION = 1
MAX_PROJECTS = 128
MAX_VERSIONS = 200
MAX_RECORD_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024

class HistoryError(RuntimeError): pass


def default_data_dir() -> Path:
    return Path(os.environ.get("VERITY_DATA_DIR", ".verity-data"))


def _strict_load(path: Path) -> dict:
    def no_dupes(pairs):
        d = {}
        for k, v in pairs:
            if k in d: raise HistoryError("duplicate JSON key")
            d[k] = v
        return d
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_RECORD_BYTES: raise HistoryError("record exceeds size budget")
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=no_dupes)
    except HistoryError: raise
    except Exception as e: raise HistoryError("corrupt history record") from e
    if not isinstance(obj, dict) or obj.get("schemaVersion") != SCHEMA_VERSION:
        raise HistoryError("unsupported history schema")
    return obj


def _check_safe(path: Path, directory=False) -> None:
    if path.is_symlink(): raise HistoryError("symlinked history path refused")
    if not path.exists(): return
    st = path.stat()
    if hasattr(os, "getuid") and st.st_uid != os.getuid(): raise HistoryError("history path wrong owner")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH): raise HistoryError("unsafe history permissions")
    if directory and not stat.S_ISDIR(st.st_mode): raise HistoryError("history directory expected")


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700); _check_safe(path, True)


def _atomic_json(path: Path, obj: dict, replace=os.replace) -> None:
    data = (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(data) > MAX_RECORD_BYTES: raise HistoryError("record exceeds size budget")
    _mkdir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
        replace(tmp, path); os.chmod(path, 0o600)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass


def _finding_scope(review, finding) -> list[str]:
    event_ids = set((finding.origin or {}).get("ruleMatchEventIds", []))
    rule_ids = {e.ruleId for e in review.ruleMatches if e.eventId in event_ids}
    out = {f"pi-{r}" for r in rule_ids}
    # Rules consuming analyzer output also require that analyzer.
    if any(r.startswith("skill.bandit.") for r in rule_ids): out.add("pi-analyzer-bandit")
    if "skill.gitleaks_finding" in rule_ids: out.add("pi-analyzer-gitleaks")
    return sorted(out)


def project_review(review, *, profile: str) -> dict:
    """Allowlisted projection: intentionally no bytes/evidence/provider/path/tool details."""
    counts = {s: 0 for s in ("low","medium","high","critical")}
    findings=[]
    for f in review.findings:
        counts[f.severity] += 1
        findings.append({
            "findingId": f.findingId, "fingerprint": f.findingOccurrenceFingerprint,
            "findingType": f.findingType, "subjectKey": f.subjectKey,
            "severity": f.severity, "claim": f.claim,
            "requiredPlanItemIds": _finding_scope(review, f),
        })
    return {
        "schemaVersion": SCHEMA_VERSION, "recordType":"skillReview",
        "artifactId": review.artifactSnapshot.artifactId,
        "snapshotId": review.artifactSnapshot.snapshotId, "reviewId": review.reviewId,
        "createdAt": datetime.now(timezone.utc).isoformat(), "engine":"skill", "profile":profile,
        "scopeId": f"skill:{profile}:coverage-policy-v1:baseline-policy-v2",
        "contentDigest": review.artifactSnapshot.contentRootDigest,
        "coverage": {"status":review.coverage.status,"reasonCodes":list(review.coverage.reasonCodes)},
        "plan": [{"planItemId":p.planItemId,"componentKind":p.componentKind,"componentId":p.componentId,"componentVersion":p.componentVersion} for p in review.plan.items],
        "executions": [{"planItemId":e.planItemId,"status":e.status} for e in review.executions],
        "findingCounts": counts, "findings": findings,
        "reviewStatus": "complete" if review.coverage.status == "sufficient" else "coverage_incomplete",
    }


class HistoryStore:
    def __init__(self, root=None):
        self.root=Path(root) if root else default_data_dir(); self.projects=self.root/"projects"
        _mkdir(self.root); _mkdir(self.projects)
    def _project_path(self, aid): return self.projects/aid/"project.json"
    def _validate_id(self, aid):
        if not isinstance(aid,str) or not aid.startswith("a-") or len(aid)!=34 or not all(c in "0123456789abcdef" for c in aid[2:]): raise HistoryError("unknown project")
    def create_project(self, display_name: str, alias: str|None=None) -> dict:
        name=display_name.strip()
        if not name or len(name)>80 or any(ord(c)<32 for c in name): raise HistoryError("invalid display name")
        if len(list(self.projects.glob("*/project.json"))) >= MAX_PROJECTS: raise HistoryError("project budget exceeded")
        if alias is not None and (not alias or len(alias)>40 or not alias.replace("-","").replace("_","").isalnum()): raise HistoryError("invalid alias")
        if alias and any(p.get("alias")==alias for p in self.list_projects()): raise HistoryError("alias already exists")
        aid="a-"+secrets.token_hex(16); now=datetime.now(timezone.utc).isoformat()
        p={"schemaVersion":SCHEMA_VERSION,"recordType":"skillProject","artifactId":aid,"displayName":name,"alias":alias,"createdAt":now,"versionIds":[]}
        _atomic_json(self._project_path(aid),p); return p
    def list_projects(self):
        out=[]
        for path in sorted(self.projects.glob("*/project.json")):
            _check_safe(path.parent,True); _check_safe(path); out.append(_strict_load(path))
        return out
    def resolve(self, ref):
        matches=[p for p in self.list_projects() if p["artifactId"]==ref or p.get("alias")==ref]
        if len(matches)!=1: raise HistoryError("unknown or ambiguous project")
        return matches[0]
    def get_project(self, ref): return self.resolve(ref)
    def add_review(self, ref, review, *, profile="standard"):
        p=self.resolve(ref)
        if review.engine!="skill" or review.artifactSnapshot.artifactId!=p["artifactId"]: raise HistoryError("review identity mismatch")
        if len(p["versionIds"])>=MAX_VERSIONS: raise HistoryError("version budget exceeded")
        rec=project_review(review,profile=profile); vp=self.projects/p["artifactId"]/"versions"/f'{rec["reviewId"]}.json'
        if vp.exists(): raise HistoryError("immutable review already exists")
        total=sum(x.stat().st_size for x in self.root.rglob("*.json") if x.is_file())
        size=len(json.dumps(rec).encode())
        if total+size>MAX_TOTAL_BYTES: raise HistoryError("total history budget exceeded")
        _atomic_json(vp,rec)
        p=dict(p); p["versionIds"]=[*p["versionIds"],rec["reviewId"]]
        try: _atomic_json(self._project_path(p["artifactId"]),p)
        except Exception:
            vp.unlink(missing_ok=True); raise
        return rec
    def versions(self, ref):
        p=self.resolve(ref); out=[]
        for rid in p["versionIds"]:
            vp=self.projects/p["artifactId"]/"versions"/f"{rid}.json"; _check_safe(vp); out.append(_strict_load(vp))
        return out
    def diff(self, ref, previous_review_id=None, current_review_id=None):
        p=self.resolve(ref); vs=self.versions(p["artifactId"])
        if len(vs)<2: raise HistoryError("two versions required")
        by={v["reviewId"]:v for v in vs}; prev=by.get(previous_review_id) if previous_review_id else vs[-2]; cur=by.get(current_review_id) if current_review_id else vs[-1]
        if not prev or not cur: raise HistoryError("unknown version")
        if prev["artifactId"]!=cur["artifactId"] or prev["scopeId"]!=cur["scopeId"]: raise HistoryError("incompatible baseline scope")
        def fs(r):
            return [Finding(findingId=f["findingId"],snapshotId=r["snapshotId"],findingOccurrenceFingerprint=f["fingerprint"],findingType=f["findingType"],subject={},subjectKey=f["subjectKey"],claim=f["claim"],severity=f["severity"],origin={"kind":"deterministic_rule"},evidenceIds=[]) for f in r["findings"]]
        req={f["findingId"]:f["requiredPlanItemIds"] for f in prev["findings"]}
        cov=CoverageAssessment("stored",cur["reviewId"],"stored",1,cur["coverage"]["status"],reasonCodes=cur["coverage"]["reasonCodes"])
        recs=compare(fs(prev),fs(cur),previous_snapshot_id=prev["snapshotId"],current_snapshot_id=cur["snapshotId"],baseline_scope_id=cur["scopeId"],current_coverage=cov,required_plan_items=req,current_execution_status={e["planItemId"]:e["status"] for e in cur["executions"]})
        return {"previousReviewId":prev["reviewId"],"currentReviewId":cur["reviewId"],"counts":{s:sum(x.state==s for x in recs) for s in ("new","existing","changed","resolved","unknown_due_to_coverage")},"changes":[asdict(x) for x in recs]}
