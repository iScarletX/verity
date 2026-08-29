"""Contract tests for the canonical interactive Chinese Verity manual.

These tests intentionally verify the rendered-document contract rather than
individual prose formatting.  The handbook is a single, dependency-free HTML
artifact, so a small stdlib parser is enough to guard landmarks, deep links,
static fallback content, and the DOM hooks used by its interactions.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from verity.builtins import (
    build_finding_type_registry,
    build_prompt_rule_registry,
    build_skill_rule_registry,
)


REPO = Path(__file__).resolve().parents[1]
MANUAL = REPO / "docs" / "verity-manual-zh.html"
EXPLAINER = REPO / "docs" / "project-explainer.html"


class ManualParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.anchors: list[str] = []
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.scripts: list[str] = []
        self.text_parts: list[str] = []
        self._script_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_map = dict(attrs)
        self.tags.append((tag, attr_map))
        if attr_map.get("id"):
            self.ids.append(attr_map["id"])
        href = attr_map.get("href", "")
        if href.startswith("#") and len(href) > 1:
            self.anchors.append(href[1:])
        if tag == "script":
            self._script_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._script_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            self.scripts.append(data)
        else:
            text = " ".join(data.split())
            if text:
                self.text_parts.append(text)


@pytest.fixture(scope="module")
def manual() -> ManualParser:
    parser = ManualParser()
    parser.feed(MANUAL.read_text(encoding="utf-8"))
    return parser


def _classes(attrs: dict[str, str]) -> set[str]:
    return set(attrs.get("class", "").split())


def _has_tag(manual: ManualParser, tag: str, **required: str) -> bool:
    return any(
        actual_tag == tag
        and all(attrs.get(name) == value for name, value in required.items())
        for actual_tag, attrs in manual.tags
    )


def _relative_luminance(color: str) -> float:
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    light, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


def _css_hex_vars(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def test_manual_is_an_accessible_self_contained_document(manual: ManualParser):
    assert _has_tag(manual, "html", lang="zh-Hans")
    assert sum(1 for tag, _ in manual.tags if tag == "main") == 1
    assert _has_tag(manual, "main", id="main")
    assert any(
        tag == "a" and attrs.get("href") == "#main" and "skip-link" in _classes(attrs)
        for tag, attrs in manual.tags
    )
    assert any(
        tag == "a" and attrs.get("href") == "#global-search"
        and "skip-link" in _classes(attrs)
        for tag, attrs in manual.tags
    )
    assert any(
        tag == "div" and attrs.get("role") == "progressbar"
        and attrs.get("aria-valuemin") == "0"
        and attrs.get("aria-valuemax") == "100"
        for tag, attrs in manual.tags
    )

    assert len(manual.ids) == len(set(manual.ids)), "HTML ids must be unique"
    missing_targets = sorted(set(manual.anchors) - set(manual.ids))
    assert missing_targets == [], f"broken internal anchors: {missing_targets}"

    external_resources = []
    for tag, attrs in manual.tags:
        if tag == "script" and attrs.get("src"):
            external_resources.append((tag, attrs["src"]))
        if tag == "link" and attrs.get("rel") in {"stylesheet", "preload", "modulepreload"}:
            external_resources.append((tag, attrs.get("href", "")))
        if tag in {"img", "audio", "video", "iframe", "source"} and attrs.get("src"):
            external_resources.append((tag, attrs["src"]))
    assert external_resources == []


def test_manual_exposes_the_complete_handbook_information_architecture(
    manual: ManualParser,
):
    required_sections = {
        "summary",
        "capabilities",
        "quickstart",
        "pathfinder",
        "howitworks",
        "architecture",
        "operations",
        "report-reference",
        "history",
        "trust",
        "surface",
        "limits",
        "troubleshooting",
        "faq",
        "glossary",
        "sources",
    }
    assert required_sections <= set(manual.ids)


def test_manual_keeps_faq_and_glossary_readable_without_javascript(
    manual: ManualParser,
):
    faq_items = [
        attrs for tag, attrs in manual.tags
        if tag == "details" and "faq-item" in _classes(attrs)
    ]
    glossary_items = [
        attrs for tag, attrs in manual.tags
        if tag == "details" and "glossary-item" in _classes(attrs)
    ]
    assert len(faq_items) >= 60
    assert len(glossary_items) >= 20
    assert all(attrs.get("id", "").startswith("faq-") for attrs in faq_items)
    assert all(attrs.get("id", "").startswith("term-") for attrs in glossary_items)


def test_manual_declares_the_interactive_dom_contract(manual: ManualParser):
    required_ids = {
        "global-search",
        "search-results",
        "search-status",
        "mobile-nav-toggle",
        "path-options",
        "path-detail",
        "workflow-tabs",
        "workflow-steps",
        "workflow-detail",
        "architecture-map",
        "architecture-detail",
        "copy-status",
        "faq-search",
        "faq-count",
        "glossary-search",
        "glossary-count",
        "back-to-top",
    }
    assert required_ids <= set(manual.ids)
    assert any(
        tag == "button" and "copy-button" in _classes(attrs)
        and attrs.get("data-copy-target")
        for tag, attrs in manual.tags
    )
    assert any(
        tag == "button" and attrs.get("data-workflow") == "prompt"
        and attrs.get("aria-pressed") == "true"
        for tag, attrs in manual.tags
    )
    assert any(
        tag == "button" and attrs.get("data-workflow") == "skill"
        for tag, attrs in manual.tags
    )
    assert any(
        tag == "div" and attrs.get("id") == "search-status"
        and attrs.get("aria-live") == "polite"
        for tag, attrs in manual.tags
    )
    for detail_id in ("path-detail", "workflow-detail", "architecture-detail"):
        assert any(
            attrs.get("id") == detail_id
            and attrs.get("role") == "region"
            and attrs.get("aria-live") == "polite"
            for _, attrs in manual.tags
        )
    for count_id in ("rule-count", "cat-count", "risk-count"):
        assert any(
            attrs.get("id") == count_id
            and attrs.get("role") == "status"
            and attrs.get("aria-live") == "polite"
            for _, attrs in manual.tags
        )
    script = "\n".join(manual.scripts)
    assert "function restoreModalFocus" in script
    assert "window.setTimeout" in script


def test_manual_architecture_map_can_reflow_before_the_mobile_breakpoint():
    source = MANUAL.read_text(encoding="utf-8")
    assert ".trust-map { display: grid; grid-template-columns: repeat(auto-fit," in source
    assert "repeat(5, minmax(145px,1fr))" not in source
    assert "pre code { display: block; min-width: 0;" in source
    assert ".tile, .detail-cell { min-width: 0; overflow-wrap: anywhere; }" in source
    assert "button, input[type=\"search\"] { min-height: 44px; }" in source
    assert ".copy-button { position: absolute; right: 8px; top: 8px; min-height: 44px; }" in source


def test_manual_dark_theme_meets_normal_text_contrast():
    source = MANUAL.read_text(encoding="utf-8")
    root = re.search(r":root\s*\{(.*?)\}", source, re.DOTALL)
    dark = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\}', source, re.DOTALL)
    assert root and dark
    root_vars = _css_hex_vars(root.group(1))
    dark_vars = _css_hex_vars(dark.group(1))
    dark_surface = dark_vars["--surface-1"]
    for name in (
        "--series-1",
        "--st-good",
        "--st-warning",
        "--st-serious",
        "--st-critical",
    ):
        assert _contrast(dark_vars[name], dark_surface) >= 4.5, name
    assert _contrast(dark_vars["--series-1-fill"], "#ffffff") >= 4.5
    assert _contrast(root_vars["--series-1-fill"], "#ffffff") >= 4.5
    assert _contrast(root_vars["--st-serious-fill"], "#ffffff") >= 4.5
    assert source.count("var(--series-1-fill)") >= 3
    assert "background:var(--st-serious-fill)" in source


def test_manual_search_reveals_filtered_targets_and_supports_arrow_keys(
    manual: ManualParser,
):
    script = "\n".join(manual.scripts)
    assert '.replace(/"/g, "&quot;")' in script
    assert ".replace(/'/g, \"&#39;\")" in script
    assert "function revealSearchTarget" in script
    assert "function openSearchResult" in script
    assert "try { id = decodeURIComponent(rawHash); }" in script
    assert "catch (error) { return; }" in script
    assert "event.preventDefault();" in script
    assert "window.history.pushState" in script
    assert "window.requestAnimationFrame" in script
    assert 'event.key === "ArrowDown"' in script
    assert 'event.key === "ArrowUp"' in script
    explainer_source = EXPLAINER.read_text(encoding="utf-8")
    assert '.replace(/"/g, "&quot;")' in explainer_source
    assert ".replace(/'/g, \"&#39;\")" in explainer_source


def test_manual_static_server_is_loopback_only_and_nojs_limits_are_explicit(
    manual: ManualParser,
):
    visible_text = " ".join(manual.text_parts)
    command = "python3 -m http.server 8766 --bind 127.0.0.1 --directory docs"
    assert command in visible_text
    assert command in (REPO / "README.md").read_text(encoding="utf-8")
    assert "动态清单不会渲染" in visible_text


def test_manual_states_current_zip_semantic_and_sandbox_truths(manual: ManualParser):
    visible_text = " ".join(manual.text_parts)
    source = MANUAL.read_text(encoding="utf-8")
    assert "Web 支持单个 Skill ZIP" in visible_text
    assert "CLI 仍只接受 Skill 文件夹" in visible_text
    assert "sandbox_isolation_hardening_required" in visible_text
    assert "provider_not_configured" in visible_text
    assert "没有 ZIP 摄入" not in visible_text
    assert "tests/test_architecture.py" not in visible_text
    assert "src/verity/rules.py" not in source
    assert "standards/risks.yaml" not in source

    # The handbook must follow run_review's source order: deterministic
    # execution, profile, dynamic plan, then deterministic Coverage.
    for workflow in ("prompt", "skill"):
        match = re.search(
            rf"{workflow}: \[(.*?)\]\.concat\(COMMON_TAIL\)", source, re.DOTALL
        )
        assert match, workflow
        block = match.group(1)
        static_name = "静态执行" if workflow == "prompt" else "静态与成熟工具"
        profile_name = "行为画像" if workflow == "prompt" else "能力与行为画像"
        assert (
            block.index(f'name:"{static_name}"')
            < block.index(f'name:"{profile_name}"')
            < block.index('name:"动态计划"')
            < block.index('name:"静态 Coverage"')
        )

    # Current capability/report contracts and product boundaries.
    assert "static</code> 可为 <code>completed</code> 或 <code>failed" in source
    for field in ("semantic", "promptBlackbox", "skillSandbox", "agentInstructionRuntime"):
        assert f"<code>{field}</code>（条件字段）" in source
    assert "V1.5 是可显式开启的真实黑盒运行路径" in visible_text
    assert "V2 只有失败关闭的请求路径" in visible_text
    assert "CLI 当前不限制重复传入的校验者票数" in visible_text
    assert "Web 最多允许 3 名校验者（含主校验者）" in visible_text
    assert "15 条" in source
    assert "只负责语义 Candidate/Validator" in source
    assert "黑盒和 Harness 各走自己的受控运行路径" in source

    # Dispositions annotate history/gating; they never erase current findings.
    assert "处置记录不会删除当前报告里的 Finding" in source
    assert "恰好两种" not in source
    assert "整个项目里唯一允许" not in source
    assert "最多额外 4" not in source
    assert "共 18 条" not in source

    # The current engineering gate is distinct from the historical V1 closure.
    assert "4163 / 4163" in visible_text
    assert "19 / 19" in visible_text
    assert "历史 V1 收尾制品" in visible_text
    assert "23 个仍是" in visible_text
    assert "其余 61 个" in visible_text


def test_manual_rule_inventory_matches_the_runtime_registry():
    source = MANUAL.read_text(encoding="utf-8")
    match = re.search(r"var DATA = (\{.*?\});\n", source, re.DOTALL)
    assert match
    embedded = json.loads(match.group(1))
    additions_match = re.search(
        r"DATA\.rules\.push\((.*?)\);\n\n/\*", source, re.DOTALL
    )
    additions = json.loads("[" + additions_match.group(1) + "]") if additions_match else []
    finding_types = build_finding_type_registry()
    runtime_rules = (
        build_prompt_rule_registry(finding_types).all()
        + build_skill_rule_registry(finding_types).all()
    )
    assert {rule["id"] for rule in embedded["rules"] + additions} == {
        rule.ruleId for rule in runtime_rules
    }


def test_manual_inline_javascript_is_syntax_valid(manual: ManualParser, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for manual JavaScript syntax checks")
    script = tmp_path / "verity-manual-zh.js"
    script.write_text("\n".join(manual.scripts), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_manual_javascript_has_no_network_or_persistence_side_effects(
    manual: ManualParser,
):
    script = "\n".join(manual.scripts)
    forbidden = (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "localStorage",
        "sessionStorage",
        "serviceWorker",
        "indexedDB",
    )
    assert [token for token in forbidden if token in script] == []
