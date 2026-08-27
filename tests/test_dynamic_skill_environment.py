import json
import subprocess
from pathlib import Path

import pytest

from verity.dynamic.environment import (
    DynamicEnvironmentPlan,
    SyntheticFixture,
    build_skill_environment,
)
from verity.dynamic.planner import build_dynamic_plan
from verity.dynamic.profile import ArtifactBehaviorProfile, ProfileFact
from verity.intake import intake_directory
from verity.sandbox.models import SandboxConfigurationError, SandboxRunRequest
from verity.sandbox.runner import SandboxRunner, _SpawnHandle


def _profile(**overrides):
    values = {
        "runtime_kind": "executable_skill",
        "inputs": ("json_document",),
        "facts": (
            ProfileFact(
                fact_id="pf-input",
                kind="input",
                value="json_document",
                source_path="SKILL.md",
                start_byte=1,
                end_byte=5,
            ),
        ),
    }
    values.update(overrides)
    return ArtifactBehaviorProfile(**values)


def _skill(tmp_path: Path, extra_files=None):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: fixture-test\ndescription: test\nversion: 1.0.0\n---\n"
    )
    script = tmp_path / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n")
    for relative_path, content in (extra_files or {}).items():
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return intake_directory(str(tmp_path))


def test_executable_skill_receives_only_relevant_synthetic_inputs():
    profile = _profile()
    environment = build_skill_environment(profile, build_dynamic_plan(profile))

    assert [item.relative_path for item in environment.fixtures] == ["input.json"]
    assert all("credential" not in item.purpose for item in environment.fixtures)
    assert environment.argv == ("input.json",)


def test_credential_decoy_is_staged_only_when_credential_check_selected():
    profile = _profile(
        inputs=(),
        tool_families=("credential_access",),
        sensitive_data=("api_credentials",),
        facts=(
            ProfileFact(
                fact_id="pf-credential",
                kind="tool_family",
                value="credential_access",
                source_path="scripts/main.py",
                start_byte=0,
                end_byte=0,
            ),
        ),
    )

    environment = build_skill_environment(profile, build_dynamic_plan(profile))

    assert [item.relative_path for item in environment.fixtures] == [
        "credentials.json"
    ]


def test_environment_fixture_limits_and_paths_are_validated():
    with pytest.raises(ValueError, match="relative fixture path"):
        DynamicEnvironmentPlan(
            fixtures=(SyntheticFixture("../escape.json", b"{}", "test"),),
            argv=(),
            reason_codes=(),
        )

    fixtures = tuple(
        SyntheticFixture(f"input-{index}.json", b"{}", "test")
        for index in range(17)
    )
    with pytest.raises(ValueError, match="at most 16"):
        DynamicEnvironmentPlan(fixtures=fixtures, argv=(), reason_codes=())


def test_fixture_cannot_overwrite_reviewed_artifact_file(tmp_path):
    snapshot, file_bytes = _skill(tmp_path)
    runner = SandboxRunner(inject_spawn=lambda *args, **kwargs: None)
    runner._sandbox_available = True
    request = SandboxRunRequest(
        entry_point="scripts/main.py",
        syntheticFixtures=[
            SyntheticFixture("scripts/main.py", b"replacement", "conflict")
        ],
    )

    with pytest.raises(
        SandboxConfigurationError,
        match="synthetic_fixture_conflicts_with_artifact",
    ):
        runner.run(request, snapshot=snapshot, file_bytes=file_bytes)


def test_fixture_metadata_is_reported_without_raw_content(tmp_path):
    snapshot, file_bytes = _skill(tmp_path)
    holder = {}

    class Popen:
        pid = 1234
        returncode = 0

        def communicate(self, timeout=None):
            observation = {
                "raisedException": None,
                "fileEvents": [],
                "networkAttempts": [],
                "subprocessAttempts": [],
                "sqlAttempts": [],
                "truncated": {},
                "driverExitCode": 0,
            }
            (Path(holder["cwd"]) / "_verity_observation.json").write_text(
                json.dumps(observation)
            )
            return b"", b""

        def poll(self):
            return self.returncode

    def spawn(args, *, cwd, env, cpu_seconds):
        holder["cwd"] = cwd
        return _SpawnHandle(popen=Popen())

    runner = SandboxRunner(inject_spawn=spawn, inject_ps_probe=lambda pgid: None)
    runner._sandbox_available = True
    fixture = SyntheticFixture("input.json", b'{"value": 7}', "declared_json_input")
    observation = runner.run(
        SandboxRunRequest(
            entry_point="scripts/main.py", syntheticFixtures=[fixture]
        ),
        snapshot=snapshot,
        file_bytes=file_bytes,
    )

    assert observation.syntheticFixtures[0]["relativePath"] == "input.json"
    assert observation.syntheticFixtures[0]["purpose"] == "declared_json_input"
    assert len(observation.syntheticFixtures[0]["contentDigest"]) == 64
    assert "content" not in observation.syntheticFixtures[0]
    assert b'{"value": 7}' not in json.dumps(
        observation.syntheticFixtures, sort_keys=True
    ).encode()


def test_agent_instruction_plan_reports_runtime_unavailable():
    profile = ArtifactBehaviorProfile(runtime_kind="agent_instruction")

    plan = build_dynamic_plan(profile)

    runtime = plan.item("agent_instruction.runtime")
    assert runtime.status == "unavailable"
    assert runtime.reason_codes == ("agent_runtime_not_configured",)
