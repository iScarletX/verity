"""Deterministic static capability facts for Skill artifacts.

Facts are not vulnerabilities and never affect gates. They describe narrowly
observable declarations/imports/calls for later least-privilege comparison and
semantic evidence. No reviewed code is imported or executed.
"""
from __future__ import annotations

import ast
from typing import Any, Dict, List, Set, Tuple

CAPABILITY_FACT_SCHEMA = "verity.skill.capability-facts.v1"
MAX_FACTS = 2048


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _call_name(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return ""


def _literal_process_target(node: ast.Call) -> str:
    if not node.args:
        return ""
    value = node.args[0]
    if isinstance(value, (ast.List, ast.Tuple)) and value.elts:
        value = value.elts[0]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        token = value.value.strip().split(None, 1)[0] if value.value.strip() else ""
        return token.rsplit("/", 1)[-1][:80]
    return ""


def _add(facts: Set[Tuple[str, str, str, str, int, str]],
         category: str, operation: str, path: str, source: str,
         source_line: int = 0, target: str = "") -> None:
    if len(facts) < MAX_FACTS:
        facts.add((category, operation, path, source,
                   max(int(source_line), 0), target[:80]))


def extract_capability_facts(snapshot, file_bytes: Dict[str, bytes],
                             manifest: Dict[str, Any] | None) -> Dict[str, Any]:
    facts: Set[Tuple[str, str, str, str, int, str]] = set()
    if manifest:
        for tool in manifest.get("permissions") or []:
            if isinstance(tool, str) and tool.strip():
                _add(facts, "tool", tool[:160], "SKILL.md", "manifest")

    install_names = {
        "requirements.txt", "pyproject.toml", "package.json", "package-lock.json",
        "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "go.mod",
        "cargo.toml", "gemfile",
    }
    for artifact_file in snapshot.files:
        if artifact_file.status != "included":
            continue
        path = artifact_file.normalizedPath
        lower = path.lower()
        if lower.rsplit("/", 1)[-1] in install_names:
            _add(facts, "installation", "dependency_manifest", path, "filename")
        if lower.endswith((".json", ".yaml", ".yml", ".toml", ".ini", ".cfg")):
            _add(facts, "configuration", "configuration_file", path, "filename")
        if not lower.endswith(".py"):
            continue
        data = file_bytes.get(artifact_file.fileId, b"")
        try:
            tree = ast.parse(data.decode("utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""])
                if any(n.split(".")[0] in {"requests", "urllib", "httpx", "socket"}
                       for n in names):
                    _add(facts, "network", "network_library", path, "python_ast",
                         getattr(node, "lineno", 0))
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name.startswith("subprocess.") or name in {"os.system", "os.popen"}:
                _add(facts, "process", name, path, "python_ast",
                     getattr(node, "lineno", 0), _literal_process_target(node))
            file_calls = {"open", "io.open", "pathlib.Path.open", "Path.open",
                          "pathlib.Path.read_text", "Path.read_text",
                          "pathlib.Path.write_text", "Path.write_text",
                          "pathlib.Path.read_bytes", "Path.read_bytes",
                          "pathlib.Path.write_bytes", "Path.write_bytes"}
            path_constructor_call = (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Call)
                and _call_name(node.func.value.func) in {"Path", "pathlib.Path"}
                and node.func.attr in {"open", "read_text", "write_text",
                                       "read_bytes", "write_bytes"}
            )
            if name in file_calls or path_constructor_call:
                _add(facts, "file", name, path, "python_ast",
                     getattr(node, "lineno", 0))
            if name in {"os.getenv", "os.environ.get"}:
                _add(facts, "credential", "environment_access", path, "python_ast",
                     getattr(node, "lineno", 0))
            if (name.startswith("requests.") or name.startswith("httpx.")
                    or name in {"urllib.request.urlopen", "socket.socket"}):
                _add(facts, "network", name, path, "python_ast",
                     getattr(node, "lineno", 0))

    return {
        "schemaVersion": CAPABILITY_FACT_SCHEMA,
        "facts": [
            {
                "category": c, "operation": o, "artifactPath": p,
                "sourceKind": s,
                **({"sourceLine": line} if line else {}),
                **({"target": target} if target else {}),
            }
            for c, o, p, s, line, target in sorted(facts)
        ],
        "limitations": [
            "python_ast_and_manifest_only",
            "no_cross_file_dataflow",
            "no_runtime_observation",
        ],
    }
