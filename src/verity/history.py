"""Trusted local Skill project identity and safe immutable review history."""
from __future__ import annotations

import json, os, secrets, stat, tempfile, threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baseline import compare
from .models import Finding, CoverageAssessment, AnalysisPlanItem, ExecutionRecord

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}
MAX_PROJECTS = 128
MAX_VERSIONS = 200
MAX_RECORD_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_DISPOSITIONS_PER_PROJECT = 256
MAX_DISPOSITION_EVENTS_PER_FINGERPRINT = 32
MAX_DISPOSITION_NOTE_LENGTH = 200
MAX_DISPOSITION_EXPIRY_DAYS = 180

DISPOSITION_STATUSES = {
    "acknowledged", "accept_risk", "false_positive", "wont_fix"
}

class HistoryError(RuntimeError): pass


def default_data_dir() -> Path:
    return Path(os.environ.get("VERITY_DATA_DIR", ".verity-data"))


def _valid_opaque_id(value: Any, prefix: str, hex_length: int) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and len(value) == len(prefix) + hex_length
        and all(c in "0123456789abcdef" for c in value[len(prefix):])
    )


def _bounded_string(value: Any, *, max_length: int = 512) -> bool:
    return isinstance(value, str) and len(value) <= max_length and "\x00" not in value


def _exact_keys(value: Any, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def _validate_score_projection(score: dict) -> None:
    keys = {"status", "value", "policyId", "policyVersion",
            "highestSeverity", "deductionTotal", "severityCap",
            "includedLayers", "evaluatedLayers", "confidenceGrade",
            "confidencePolicyVersion"}
    if not _exact_keys(score, keys):
        raise HistoryError("invalid score projection")
    if score["status"] not in {"available", "unavailable"}:
        raise HistoryError("invalid score status")
    if score["status"] == "available":
        if (not isinstance(score["value"], int) or isinstance(score["value"], bool)
                or not 0 <= score["value"] <= 100):
            raise HistoryError("invalid score value")
    elif score["value"] is not None:
        raise HistoryError("unavailable score must have null value")
    if (not all(_bounded_string(score[k], max_length=120) for k in
                ("policyId", "policyVersion", "confidenceGrade",
                 "confidencePolicyVersion"))
            or score["confidenceGrade"] not in {"A", "B", "C", "D"}):
        raise HistoryError("invalid score policy/confidence")
    if score["highestSeverity"] not in {None, "low", "medium", "high", "critical"}:
        raise HistoryError("invalid score highest severity")
    for key in ("deductionTotal", "severityCap"):
        if (score[key] is not None and
                (not isinstance(score[key], int) or isinstance(score[key], bool)
                 or score[key] < 0)):
            raise HistoryError("invalid score arithmetic")
    if (not isinstance(score["includedLayers"], list)
            or not isinstance(score["evaluatedLayers"], list)
            or not set(score["includedLayers"]) <= {"L0_static", "L1_semantic"}
            or not set(score["evaluatedLayers"]) <= {"L0_static", "L1_semantic"}
            or (score["status"] == "available"
                and "L0_static" not in score["evaluatedLayers"])):
        raise HistoryError("invalid score layers")


def _validate_review_projection(obj: dict) -> None:
    if obj.get("engine") != "skill" or obj.get("profile") not in {
            "standard", "minimal"}:
        raise HistoryError("invalid review scope")
    if not (_bounded_string(obj.get("createdAt"), max_length=80)
            and _bounded_string(obj.get("scopeId"), max_length=160)
            and _valid_opaque_id(obj.get("contentDigest"), "", 64)):
        raise HistoryError("invalid review metadata")

    coverage = obj.get("coverage")
    if not _exact_keys(coverage, {"status", "reasonCodes"}):
        raise HistoryError("invalid coverage projection")
    if coverage["status"] not in {"sufficient", "insufficient", "failed"}:
        raise HistoryError("invalid coverage status")
    if (not isinstance(coverage["reasonCodes"], list)
            or len(coverage["reasonCodes"]) > 512
            or not all(_bounded_string(x) for x in coverage["reasonCodes"])):
        raise HistoryError("invalid coverage reasons")

    plan = obj.get("plan")
    if not isinstance(plan, list) or len(plan) > 2048:
        raise HistoryError("invalid plan projection")
    plan_ids = set()
    for item in plan:
        if not _exact_keys(item, {"planItemId", "componentKind",
                                  "componentId", "componentVersion"}):
            raise HistoryError("invalid plan item")
        if (item["componentKind"] not in {"parser", "analyzer", "rule",
                                           "candidate_generator", "validator"}
                or not all(_bounded_string(item[k]) for k in (
                    "planItemId", "componentId", "componentVersion"))
                or item["planItemId"] in plan_ids):
            raise HistoryError("invalid plan item values")
        plan_ids.add(item["planItemId"])

    executions = obj.get("executions")
    allowed_statuses = {"completed", "partial", "failed", "cancelled",
                        "unsupported", "not_applicable",
                        "blocked_by_upstream_failure"}
    if not isinstance(executions, list) or len(executions) > 4096:
        raise HistoryError("invalid execution projection")
    for execution in executions:
        if (not _exact_keys(execution, {"planItemId", "status"})
                or not _bounded_string(execution["planItemId"])
                or execution["planItemId"] not in plan_ids
                or execution["status"] not in allowed_statuses):
            raise HistoryError("invalid execution item")

    counts = obj.get("findingCounts")
    if not _exact_keys(counts, {"low", "medium", "high", "critical"}):
        raise HistoryError("invalid finding counts")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
               for v in counts.values()):
        raise HistoryError("invalid finding count values")

    findings = obj.get("findings")
    finding_keys = {"findingId", "fingerprint", "findingType", "subjectKey",
                    "severity", "claim", "requiredPlanItemIds"}
    if not isinstance(findings, list) or len(findings) > 4096:
        raise HistoryError("invalid findings projection")
    seen_findings = set()
    calculated_counts = {s: 0 for s in ("low", "medium", "high", "critical")}
    for finding in findings:
        if not _exact_keys(finding, finding_keys):
            raise HistoryError("invalid finding projection")
        if (not _valid_opaque_id(finding["findingId"], "F-", 16)
                or not _valid_opaque_id(finding["fingerprint"], "", 64)
                or not _valid_opaque_id(finding["subjectKey"], "", 64)
                or not _bounded_string(finding["findingType"], max_length=160)
                or finding["severity"] not in calculated_counts
                or not _bounded_string(finding["claim"], max_length=512)
                or not isinstance(finding["requiredPlanItemIds"], list)
                or len(finding["requiredPlanItemIds"]) > 64
                or not all(_bounded_string(x)
                           and x in plan_ids
                           for x in finding["requiredPlanItemIds"])
                or finding["findingId"] in seen_findings):
            raise HistoryError("invalid finding values")
        seen_findings.add(finding["findingId"])
        calculated_counts[finding["severity"]] += 1
    if calculated_counts != counts:
        raise HistoryError("finding counts do not match findings")
    expected_review_status = (
        "complete" if coverage["status"] == "sufficient"
        else "coverage_incomplete")
    if obj.get("reviewStatus") != expected_review_status:
        raise HistoryError("invalid review status")
    if obj.get("schemaVersion") == 2:
        _validate_score_projection(obj.get("score"))


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
    if (not isinstance(obj, dict)
            or obj.get("schemaVersion") not in SUPPORTED_SCHEMA_VERSIONS):
        raise HistoryError("unsupported history schema")
    kind=obj.get("recordType")
    project_keys={"schemaVersion","recordType","artifactId","displayName","alias","createdAt","versionIds"}
    review_keys={"schemaVersion","recordType","artifactId","snapshotId","reviewId","createdAt","engine","profile","scopeId","contentDigest","coverage","plan","executions","findingCounts","findings","reviewStatus"}
    review_keys_v2 = review_keys | {"score"}
    disposition_keys = {"schemaVersion", "recordType", "fingerprint", "events"}
    if kind == "skillProject":
        expected = project_keys
    elif kind == "skillReview":
        expected = review_keys_v2 if obj.get("schemaVersion") == 2 else review_keys
    elif kind == "dispositionHistory":
        expected = disposition_keys
    else:
        expected = None
    if expected is None or set(obj)!=expected: raise HistoryError("history record violates strict schema")
    if kind == "skillProject":
        if not _valid_opaque_id(obj.get("artifactId"), "a-", 32):
            raise HistoryError("invalid project identity")
        if (not _bounded_string(obj.get("displayName"), max_length=80)
                or not obj["displayName"].strip()
                or not _bounded_string(obj.get("createdAt"), max_length=80)):
            raise HistoryError("invalid project metadata")
        alias = obj.get("alias")
        if alias is not None and (
                not _bounded_string(alias, max_length=40)
                or not alias
                or not alias.replace("-", "").replace("_", "").isalnum()):
            raise HistoryError("invalid project alias")
        if not isinstance(obj.get("versionIds"), list):
            raise HistoryError("invalid project schema")
        if (len(obj["versionIds"]) > MAX_VERSIONS
                or len(set(obj["versionIds"])) != len(obj["versionIds"])
                or not all(_valid_opaque_id(v, "r-", 12)
                           for v in obj["versionIds"])):
            raise HistoryError("invalid version identity")
    if kind == "skillReview":
        if not _valid_opaque_id(obj.get("artifactId"), "a-", 32):
            raise HistoryError("invalid review artifact identity")
        if not _valid_opaque_id(obj.get("reviewId"), "r-", 12):
            raise HistoryError("invalid review identity")
        if not _valid_opaque_id(obj.get("snapshotId"), "s-", 12):
            raise HistoryError("invalid snapshot identity")
        if not all(isinstance(obj.get(k), list)
                   for k in ("plan", "executions", "findings")):
            raise HistoryError("invalid review schema")
        _validate_review_projection(obj)
    if kind == "dispositionHistory":
        if not _valid_opaque_id(obj.get("fingerprint"), "", 64):
            raise HistoryError("invalid disposition fingerprint")
        if (not isinstance(obj.get("events"), list)
                or len(obj["events"]) > MAX_DISPOSITION_EVENTS_PER_FINGERPRINT):
            raise HistoryError("invalid disposition events")
        disposition_event_keys = {"status", "expiryDate", "createdAt", "createdBy"}
        for event in obj["events"]:
            required_keys = disposition_event_keys.copy()
            if "note" in event:
                required_keys.add("note")
            if not _exact_keys(event, required_keys):
                raise HistoryError("invalid disposition event keys")
            if (event["status"] not in DISPOSITION_STATUSES
                    or not _bounded_string(event["expiryDate"], max_length=40)
                    or not _bounded_string(event["createdAt"], max_length=40)
                    or not _bounded_string(event["createdBy"], max_length=80)):
                raise HistoryError("invalid disposition event values")
            if "note" in event and (
                    not _bounded_string(event["note"],
                                        max_length=MAX_DISPOSITION_NOTE_LENGTH)
                    or any(ord(c) < 32 for c in event["note"])):
                raise HistoryError("invalid disposition note")
            try:
                datetime.fromisoformat(event["expiryDate"])
                datetime.fromisoformat(event["createdAt"])
            except ValueError:
                raise HistoryError("invalid disposition dates")
    return obj


def _check_safe(path: Path, directory=False) -> None:
    if path.is_symlink(): raise HistoryError("symlinked history path refused")
    if not path.exists(): return
    st = path.stat()
    if hasattr(os, "getuid") and st.st_uid != os.getuid(): raise HistoryError("history path wrong owner")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH): raise HistoryError("unsafe history permissions")
    if directory and not stat.S_ISDIR(st.st_mode): raise HistoryError("history directory expected")


def _mkdir(path: Path) -> None:
    # Check before mkdir/chmod so an attacker-controlled symlink cannot make
    # Verity change permissions on its target before the rejection happens.
    if path.is_symlink():
        raise HistoryError("symlinked history path refused")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _check_safe(path, True)
    os.chmod(path, 0o700)


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
    from .report import review_to_dict
    report = review_to_dict(review)
    score = report["score"]
    confidence = report["reviewConfidence"]
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
        "score": {
            "status": score["status"], "value": score["value"],
            "policyId": score["policyId"], "policyVersion": score["policyVersion"],
            "highestSeverity": score["highestSeverity"],
            "deductionTotal": score["deductionTotal"],
            "severityCap": score["severityCap"],
            "includedLayers": list(score["includedLayers"]),
            "evaluatedLayers": list(score["evaluatedLayers"]),
            "confidenceGrade": confidence["grade"],
            "confidencePolicyVersion": confidence["policyVersion"],
        },
    }


class HistoryStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else default_data_dir()
        self.projects = self.root / "projects"
        self._lock = threading.RLock()
        self._disposition_rate_limit: dict[str, list[float]] = {}
        _mkdir(self.root)
        _mkdir(self.projects)
    def _project_path(self, aid): return self.projects/aid/"project.json"
    def _validate_id(self, aid):
        if not _valid_opaque_id(aid, "a-", 32):
            raise HistoryError("unknown project")
    def create_project(self, display_name: str, alias: str|None=None) -> dict:
        name = display_name.strip()
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise HistoryError("invalid display name")
        if alias is not None and (
                not alias or len(alias) > 40
                or not alias.replace("-", "").replace("_", "").isalnum()):
            raise HistoryError("invalid alias")
        with self._lock:
            if len(list(self.projects.glob("*/project.json"))) >= MAX_PROJECTS:
                raise HistoryError("project budget exceeded")
            if alias and any(p.get("alias") == alias
                             for p in self.list_projects()):
                raise HistoryError("alias already exists")
            aid = "a-" + secrets.token_hex(16)
            now = datetime.now(timezone.utc).isoformat()
            p = {"schemaVersion": SCHEMA_VERSION,
                 "recordType": "skillProject", "artifactId": aid,
                 "displayName": name, "alias": alias,
                 "createdAt": now, "versionIds": []}
            _atomic_json(self._project_path(aid), p)
            return p
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
        with self._lock:
            p = self.resolve(ref)
            if (review.engine != "skill"
                    or review.artifactSnapshot.artifactId != p["artifactId"]):
                raise HistoryError("review identity mismatch")
            if len(p["versionIds"]) >= MAX_VERSIONS:
                raise HistoryError("version budget exceeded")
            rec = project_review(review, profile=profile)
            vp = (self.projects / p["artifactId"] / "versions"
                  / f'{rec["reviewId"]}.json')
            if vp.exists():
                raise HistoryError("immutable review already exists")
            total = sum(x.stat().st_size for x in self.root.rglob("*.json")
                        if x.is_file())
            size = len(json.dumps(rec).encode())
            if total + size > MAX_TOTAL_BYTES:
                raise HistoryError("total history budget exceeded")
            _atomic_json(vp, rec)
            p = dict(p)
            p["versionIds"] = [*p["versionIds"], rec["reviewId"]]
            try:
                _atomic_json(self._project_path(p["artifactId"]), p)
            except Exception:
                vp.unlink(missing_ok=True)
                raise
            return rec
    
    def add_disposition(self, ref: str, fingerprint: str, status: str,
                        expiry_date: datetime, note: str | None = None,
                        created_by: str = "user") -> dict:
        if status not in DISPOSITION_STATUSES:
            raise HistoryError("invalid disposition status")
        if not _valid_opaque_id(fingerprint, "", 64):
            raise HistoryError("invalid fingerprint")
        if note is not None and (
                len(note) > MAX_DISPOSITION_NOTE_LENGTH
                or any(ord(c) < 32 for c in note)):
            raise HistoryError("invalid disposition note")
        now = datetime.now(timezone.utc)
        if expiry_date <= now:
            raise HistoryError("expiry date must be in the future")
        if (expiry_date - now).days > MAX_DISPOSITION_EXPIRY_DAYS:
            raise HistoryError(
                f"expiry date cannot exceed {MAX_DISPOSITION_EXPIRY_DAYS} days")
        
        with self._lock:
            project = self.resolve(ref)
            aid = project["artifactId"]
            
            # Rate limiting
            key = f"disp:{aid}"
            now_ts = now.timestamp()
            events = self._disposition_rate_limit.get(key, [])
            events = [t for t in events if now_ts - t < 60]
            if len(events) >= 100:
                raise HistoryError(
                    "disposition rate limit exceeded (100 events/minute)")
            
            disp_dir = self.projects / aid / "dispositions"
            if disp_dir.exists():
                _check_safe(disp_dir, True)
                existing = len(list(disp_dir.glob("*.json")))
                if existing >= MAX_DISPOSITIONS_PER_PROJECT:
                    raise HistoryError("project disposition limit exceeded")
            
            disp_file = disp_dir / f"{fingerprint}.json"
            if disp_file.exists():
                _check_safe(disp_file)
                record = _strict_load(disp_file)
            else:
                record = {
                    "schemaVersion": SCHEMA_VERSION,
                    "recordType": "dispositionHistory",
                    "fingerprint": fingerprint,
                    "events": [],
                }
            
            event = {
                "status": status,
                "expiryDate": expiry_date.isoformat(),
                "createdAt": now.isoformat(),
                "createdBy": created_by,
            }
            if note:
                event["note"] = note.strip()
            
            record["events"].append(event)
            _atomic_json(disp_file, record)
            
            events.append(now_ts)
            self._disposition_rate_limit[key] = events
            
            return event
    
    def list_dispositions(self, ref: str) -> list[dict]:
        project = self.resolve(ref)
        result = []
        for fp, disp in self._effective_dispositions(project["artifactId"]).items():
            result.append({**disp, "fingerprint": fp})
        return result
    
    def _effective_dispositions(self, aid: str) -> dict[str, dict]:
        """Return {fingerprint: latest_active_disposition}."""
        disp_dir = self.projects / aid / "dispositions"
        if not disp_dir.exists():
            return {}
        _check_safe(disp_dir, True)
        
        now = datetime.now(timezone.utc)
        result = {}
        
        for path in sorted(disp_dir.glob("*.json")):
            _check_safe(path)
            record = _strict_load(path)
            if record["fingerprint"] != path.stem:
                raise HistoryError("disposition filename mismatch")
            
            # Find latest non-expired event
            latest = None
            for event in record["events"]:
                expiry = datetime.fromisoformat(event["expiryDate"])
                if expiry > now and (latest is None
                                     or event["createdAt"] > latest["createdAt"]):
                    latest = event
            
            if latest:
                result[record["fingerprint"]] = latest
        
        return result
    
    def versions(self, ref):
        p = self.resolve(ref)
        out = []
        versions_dir = self.projects / p["artifactId"] / "versions"
        if p["versionIds"]:
            _check_safe(versions_dir, True)
        for rid in p["versionIds"]:
            if not _valid_opaque_id(rid, "r-", 12):
                raise HistoryError("invalid version identity")
            vp = versions_dir / f"{rid}.json"
            _check_safe(vp)
            record = _strict_load(vp)
            if record["artifactId"] != p["artifactId"] or record["reviewId"] != rid:
                raise HistoryError("version identity mismatch")
            out.append(record)
        return out
    @staticmethod
    def _score_comparison(previous: dict, current: dict) -> dict:
        prev = previous.get("score")
        cur = current.get("score")
        if not prev or not cur:
            return {"status": "not_comparable", "reasonCodes": [
                "historical_score_unavailable"]}
        if (previous["coverage"]["status"] != "sufficient"
                or current["coverage"]["status"] != "sufficient"
                or prev.get("status") != "available"
                or cur.get("status") != "available"):
            return {"status": "not_comparable", "reasonCodes": [
                "coverage_or_score_unavailable"]}
        if (prev.get("policyId") != cur.get("policyId")
                or prev.get("policyVersion") != cur.get("policyVersion")):
            return {"status": "not_comparable", "reasonCodes": [
                "score_policy_changed"]}
        if prev.get("evaluatedLayers") != cur.get("evaluatedLayers"):
            return {"status": "not_comparable", "reasonCodes": [
                "evaluated_layers_changed"]}
        delta = cur["value"] - prev["value"]
        return {
            "status": "comparable", "reasonCodes": [],
            "policyId": cur["policyId"],
            "policyVersion": cur["policyVersion"],
            "previous": prev["value"], "current": cur["value"],
            "delta": delta,
            "direction": ("improved" if delta > 0 else
                          "declined" if delta < 0 else "unchanged"),
            "note": ("Score change is secondary to finding-state diff and "
                     "does not itself prove remediation."),
        }

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
        recs = compare(
            fs(prev), fs(cur),
            previous_snapshot_id=prev["snapshotId"],
            current_snapshot_id=cur["snapshotId"],
            baseline_scope_id=cur["scopeId"], current_coverage=cov,
            required_plan_items=req,
            current_execution_status={e["planItemId"]: e["status"]
                                      for e in cur["executions"]},
        )
        prev_findings = {f["findingId"]: f for f in prev["findings"]}
        cur_findings = {f["findingId"]: f for f in cur["findings"]}
        changes = []
        for match in recs:
            source = None
            if match.currentFindingIds:
                source = cur_findings.get(match.currentFindingIds[0])
            if source is None and match.previousFindingIds:
                source = prev_findings.get(match.previousFindingIds[0])
            safe_summary = {
                "findingType": source.get("findingType") if source else "unknown",
                "severity": source.get("severity") if source else "medium",
                "claim": source.get("claim") if source else "",
            }
            item = asdict(match)
            item["summary"] = safe_summary
            changes.append(item)
        states = ("new", "existing", "changed", "resolved",
                  "unknown_due_to_coverage")
        
        # Enrich with dispositions
        dispositions = self._effective_dispositions(p["artifactId"])
        noted_counts = {s: 0 for s in DISPOSITION_STATUSES}
        for change in changes:
            state = change["state"]
            if state in ("resolved", "unknown_due_to_coverage"):
                continue
            fp = None
            if state in ("new", "existing", "changed") and change.get(
                    "currentFindingIds"):
                cur_fid = change["currentFindingIds"][0]
                cur_finding = cur_findings.get(cur_fid)
                if cur_finding:
                    fp = cur_finding["fingerprint"]
            elif state == "changed" and change.get("previousFindingIds"):
                prev_fid = change["previousFindingIds"][0]
                prev_finding = prev_findings.get(prev_fid)
                if prev_finding:
                    fp = prev_finding["fingerprint"]
            if fp and fp in dispositions:
                disp = dispositions[fp]
                change["disposition"] = {
                    "status": disp["status"],
                    "note": disp.get("note"),
                    "expiresAt": disp["expiryDate"],
                }
                noted_counts[disp["status"]] += 1
        
        return {
            "previousReviewId": prev["reviewId"],
            "currentReviewId": cur["reviewId"],
            "counts": {s: sum(x.state == s for x in recs) for s in states},
            "notedCounts": noted_counts,
            "scoreComparison": self._score_comparison(prev, cur),
            "changes": changes,
        }
