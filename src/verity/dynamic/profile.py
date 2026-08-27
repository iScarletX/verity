"""Deterministic, evidence-backed behavior profiles for reviewed artifacts.

The profile is a planning input, not a Finding.  Every positive fact is
anchored to bytes already present in the reviewed snapshot; absence never
creates an inferred capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, Optional, Tuple

from ..canonical import canonical_json, domain_tag, sha256_hex
from ..models import ArtifactSnapshot


MAX_PROFILE_FACTS = 256


@dataclass(frozen=True)
class ProfileFact:
    fact_id: str
    kind: str
    value: str
    source_path: str
    start_byte: int
    end_byte: int
    confidence: str = "declared"


@dataclass(frozen=True)
class ArtifactBehaviorProfile:
    runtime_kind: str
    domain_tags: Tuple[str, ...] = ()
    inputs: Tuple[str, ...] = ()
    outputs: Tuple[str, ...] = ()
    constraints: Tuple[str, ...] = ()
    tool_families: Tuple[str, ...] = ()
    state_requirements: Tuple[str, ...] = ()
    side_effects: Tuple[str, ...] = ()
    sensitive_data: Tuple[str, ...] = ()
    external_content: bool = False
    facts: Tuple[ProfileFact, ...] = ()


def _included_texts(
    snapshot: ArtifactSnapshot, file_bytes: Dict[str, bytes]
) -> Iterable[tuple[str, str]]:
    for entry in snapshot.files:
        if entry.status != "included" or entry.entryType != "file":
            continue
        raw = file_bytes.get(entry.fileId)
        if raw is None:
            continue
        try:
            yield entry.normalizedPath, raw.decode("utf-8")
        except UnicodeDecodeError:
            continue


def _fact(
    *, snapshot_id: str, kind: str, value: str, source_path: str,
    text: str, start: int, end: int,
) -> ProfileFact:
    start_byte = len(text[:start].encode("utf-8"))
    end_byte = len(text[:end].encode("utf-8"))
    payload = {
        "snapshotId": snapshot_id,
        "kind": kind,
        "value": value,
        "sourcePath": source_path,
        "startByte": start_byte,
        "endByte": end_byte,
    }
    digest = sha256_hex(domain_tag("behavior-profile-fact"), canonical_json(payload))
    return ProfileFact(
        fact_id=f"pf-{digest[:16]}",
        kind=kind,
        value=value,
        source_path=source_path,
        start_byte=start_byte,
        end_byte=end_byte,
    )


def _observed_fact(
    *, snapshot_id: str, kind: str, value: str, source_path: str,
) -> ProfileFact:
    payload = {
        "snapshotId": snapshot_id,
        "kind": kind,
        "value": value,
        "sourcePath": source_path,
        "startByte": 0,
        "endByte": 0,
        "confidence": "deterministic_observed",
    }
    digest = sha256_hex(domain_tag("behavior-profile-fact"), canonical_json(payload))
    return ProfileFact(
        fact_id=f"pf-{digest[:16]}",
        kind=kind,
        value=value,
        source_path=source_path,
        start_byte=0,
        end_byte=0,
        confidence="deterministic_observed",
    )


def _first_term_fact(
    *, snapshot_id: str, kind: str, value: str, source_path: str,
    text: str, terms: Tuple[str, ...], search_text: Optional[str] = None,
) -> Optional[ProfileFact]:
    lowered = (search_text if search_text is not None else text).lower()
    for term in terms:
        lowered_term = term.lower()
        if re.fullmatch(r"[a-z0-9_ ]+", lowered_term):
            match = re.search(
                r"(?<![a-z0-9_])" + re.escape(lowered_term)
                + r"(?![a-z0-9_])",
                lowered,
            )
            start = match.start() if match else -1
        else:
            start = lowered.find(lowered_term)
        if start >= 0:
            return _fact(
                snapshot_id=snapshot_id,
                kind=kind,
                value=value,
                source_path=source_path,
                text=text,
                start=start,
                end=start + len(term),
            )
    return None


def _mask_markdown_code(text: str) -> str:
    """Mask fenced and inline code without changing character positions."""
    chars = list(text)
    offset = 0
    fence = None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = None
        if stripped.startswith("```"):
            marker = "```"
        elif stripped.startswith("~~~"):
            marker = "~~~"
        mask_line = fence is not None or marker is not None
        if mask_line:
            for index in range(offset, offset + len(line)):
                if chars[index] not in "\r\n":
                    chars[index] = " "
        if marker is not None:
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
        offset += len(line)
    masked = "".join(chars)
    for match in re.finditer(r"(?<!`)`[^`\r\n]+`(?!`)", masked):
        for index in range(match.start(), match.end()):
            chars[index] = " "
    return "".join(chars)


def extract_behavior_profile(
    *, engine: str, snapshot: ArtifactSnapshot, file_bytes: Dict[str, bytes],
    artifact_model: Dict[str, Any],
) -> ArtifactBehaviorProfile:
    """Extract a bounded profile using only controlled deterministic facts."""
    executable_paths = sorted(
        entry.normalizedPath
        for entry in snapshot.files
        if entry.status == "included"
        and entry.entryType == "file"
        and entry.normalizedPath.lower().endswith(
            (".py", ".sh", ".js", ".ts", ".rb", ".go")
        )
    )
    if engine == "prompt":
        runtime_kind = "prompt"
    elif executable_paths:
        runtime_kind = "executable_skill"
    else:
        runtime_kind = "agent_instruction"
    domain_tags = []
    inputs = []
    outputs = []
    constraints = []
    tool_families = []
    state_requirements = []
    side_effects = []
    sensitive_data = []
    external_content = False
    facts = [
        _observed_fact(
            snapshot_id=snapshot.snapshotId,
            kind="entry_point",
            value=path,
            source_path=path,
        )
        for path in executable_paths
    ]

    capability_family = {
        "network": "network_access",
        "process": "process_execution",
        "file": "file_access",
        "credential": "credential_access",
        "deserialization": "deserialization",
        "sql_query": "database",
    }
    capability_facts = artifact_model.get("capabilityFacts") or {}
    for raw_fact in capability_facts.get("facts") or []:
        category = raw_fact.get("category")
        family = capability_family.get(category)
        if not family or family in tool_families:
            continue
        source_path = str(raw_fact.get("artifactPath") or "SKILL.md")
        tool_families.append(family)
        facts.append(_observed_fact(
            snapshot_id=snapshot.snapshotId,
            kind="tool_family",
            value=family,
            source_path=source_path,
        ))
        if category == "credential" and "api_credentials" not in sensitive_data:
            sensitive_data.append("api_credentials")
            facts.append(_observed_fact(
                snapshot_id=snapshot.snapshotId,
                kind="sensitive_data",
                value="api_credentials",
                source_path=source_path,
            ))
        if category == "network":
            external_content = True
            facts.append(_observed_fact(
                snapshot_id=snapshot.snapshotId,
                kind="external_content",
                value="network_content",
                source_path=source_path,
            ))
            if "external_request" not in side_effects:
                side_effects.append("external_request")
                facts.append(_observed_fact(
                    snapshot_id=snapshot.snapshotId,
                    kind="side_effect",
                    value="external_request",
                    source_path=source_path,
                ))
        if category == "process" and "process_spawn" not in side_effects:
            side_effects.append("process_spawn")
            facts.append(_observed_fact(
                snapshot_id=snapshot.snapshotId,
                kind="side_effect",
                value="process_spawn",
                source_path=source_path,
            ))
        operation = str(raw_fact.get("operation") or "")
        if (category == "file" and "write" in operation
                and "file_write" not in side_effects):
            side_effects.append("file_write")
            facts.append(_observed_fact(
                snapshot_id=snapshot.snapshotId,
                kind="side_effect",
                value="file_write",
                source_path=source_path,
            ))

    for source_path, text in _included_texts(snapshot, file_bytes):
        search_text = _mask_markdown_code(text)
        art_style = _first_term_fact(
            snapshot_id=snapshot.snapshotId,
            kind="domain",
            value="art_style",
            source_path=source_path,
            text=text,
            terms=("画风", "视觉风格", "art style", "style prompt"),
            search_text=search_text,
        )
        if art_style and "art_style" not in domain_tags:
            domain_tags.append("art_style")
            facts.append(art_style)

        director = _first_term_fact(
            snapshot_id=snapshot.snapshotId,
            kind="domain",
            value="director_storyboard",
            source_path=source_path,
            text=text,
            terms=("导演", "分镜", "镜头列表", "storyboard", "shot list"),
            search_text=search_text,
        )
        if director and "director_storyboard" not in domain_tags:
            domain_tags.append("director_storyboard")
            facts.append(director)

        script_input = _first_term_fact(
            snapshot_id=snapshot.snapshotId,
            kind="input",
            value="script",
            source_path=source_path,
            text=text,
            terms=("剧本", "script"),
            search_text=search_text,
        )
        if script_input and "script" not in inputs:
            inputs.append("script")
            facts.append(script_input)

        json_input = _first_term_fact(
            snapshot_id=snapshot.snapshotId,
            kind="input",
            value="json_document",
            source_path=source_path,
            text=text,
            terms=("json 文件", "json file", "json input", "输入 json"),
            search_text=search_text,
        )
        if json_input and "json_document" not in inputs:
            inputs.append("json_document")
            facts.append(json_input)

        shot_output = _first_term_fact(
            snapshot_id=snapshot.snapshotId,
            kind="output",
            value="shot_list",
            source_path=source_path,
            text=text,
            terms=("镜头列表", "分镜", "shot list", "storyboard"),
            search_text=search_text,
        )
        if shot_output and "shot_list" not in outputs:
            outputs.append("shot_list")
            facts.append(shot_output)

        continuity = _first_term_fact(
            snapshot_id=snapshot.snapshotId,
            kind="state_requirement",
            value="continuity",
            source_path=source_path,
            text=text,
            terms=("连续性", "保持人物", "continuity"),
            search_text=search_text,
        )
        if continuity and "continuity" not in state_requirements:
            state_requirements.append("continuity")
            facts.append(continuity)

        preserve_content = _first_term_fact(
            snapshot_id=snapshot.snapshotId,
            kind="constraint",
            value="content_preservation",
            source_path=source_path,
            text=text,
            terms=("不得改写", "保持原剧情", "do not rewrite"),
            search_text=search_text,
        )
        if preserve_content and "content_preservation" not in constraints:
            constraints.append("content_preservation")
            facts.append(preserve_content)

        preserve = _first_term_fact(
            snapshot_id=snapshot.snapshotId,
            kind="constraint",
            value="subject_preservation",
            source_path=source_path,
            text=text,
            terms=("保持人物身份", "保持主体", "不得改变主体", "preserve subject"),
            search_text=search_text,
        )
        if preserve and "subject_preservation" not in constraints:
            constraints.append("subject_preservation")
            facts.append(preserve)

        # Legacy generic probes have deliberately narrow judges.  Record the
        # exact contract each one can test instead of using broad proxies such
        # as "has any output" or "has any domain", which would make an art or
        # director prompt receive unrelated Skill/JSON/bullet-list probes.
        for constraint_name, terms in (
            (
                "role_scope",
                ("仅负责", "职责范围", "不执行其它任务", "only handles", "out of scope"),
            ),
            (
                "bullet_output",
                ("项目符号", "要点列表", "分点回复", "bullet points", "bullet list"),
            ),
            (
                "json_output",
                ("输出 json", "json 格式", "return json", "json only", "valid json"),
            ),
        ):
            contract = _first_term_fact(
                snapshot_id=snapshot.snapshotId,
                kind="constraint",
                value=constraint_name,
                source_path=source_path,
                text=text,
                terms=terms,
                search_text=search_text,
            )
            if contract and constraint_name not in constraints:
                constraints.append(constraint_name)
                facts.append(contract)

        for field_name in ("positive_prompt", "negative_prompt"):
            field_fact = _first_term_fact(
                snapshot_id=snapshot.snapshotId,
                kind="output",
                value=field_name,
                source_path=source_path,
                text=text,
                terms=(field_name,),
                search_text=search_text,
            )
            if field_fact and field_name not in outputs:
                outputs.append(field_name)
                facts.append(field_fact)

    # A Skill may contain hundreds of executable files.  Entry-point
    # inventory is useful, but it must not consume the entire evidence budget
    # and evict the facts that actually justify task-specific checks.
    behavioral_facts = [fact for fact in facts if fact.kind != "entry_point"]
    entry_point_facts = [fact for fact in facts if fact.kind == "entry_point"]
    bounded_facts = tuple(
        (behavioral_facts + entry_point_facts)[:MAX_PROFILE_FACTS]
    )

    return ArtifactBehaviorProfile(
        runtime_kind=runtime_kind,
        domain_tags=tuple(domain_tags),
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        constraints=tuple(constraints),
        tool_families=tuple(tool_families),
        state_requirements=tuple(state_requirements),
        side_effects=tuple(side_effects),
        sensitive_data=tuple(sensitive_data),
        external_content=external_content,
        facts=bounded_facts,
    )
