"""Bounded synthetic runtime inputs selected from an artifact-aware plan."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Tuple

from .planner import DynamicReviewPlan
from .profile import ArtifactBehaviorProfile


MAX_FIXTURES = 16
MAX_FIXTURE_BYTES = 64 * 1024
_RESERVED_PATHS = {
    "_sandboxdriver.py",
    "_verity_observation.json",
    "_verity_profile.sb",
}


@dataclass(frozen=True)
class SyntheticFixture:
    relative_path: str
    content: bytes
    purpose: str

    def __post_init__(self) -> None:
        path = self.relative_path
        parts = PurePosixPath(path).parts
        if (
            not path
            or "\\" in path
            or path.startswith("/")
            or "//" in path
            or any(part in {"", ".", "..", ".git"} for part in parts)
            or path in _RESERVED_PATHS
        ):
            raise ValueError("invalid relative fixture path")
        if not isinstance(self.content, bytes):
            raise ValueError("fixture content must be bytes")
        if not self.purpose or len(self.purpose) > 120:
            raise ValueError("fixture purpose must be 1..120 characters")


@dataclass(frozen=True)
class DynamicEnvironmentPlan:
    fixtures: Tuple[SyntheticFixture, ...]
    argv: Tuple[str, ...]
    reason_codes: Tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.fixtures) > MAX_FIXTURES:
            raise ValueError("environment supports at most 16 fixtures")
        if sum(len(item.content) for item in self.fixtures) > MAX_FIXTURE_BYTES:
            raise ValueError("environment fixtures exceed 64 KiB")
        paths = [item.relative_path for item in self.fixtures]
        if len(paths) != len(set(paths)):
            raise ValueError("environment fixture paths must be unique")
        if len(self.argv) > 64 or any(len(value) > 4096 for value in self.argv):
            raise ValueError("environment argv exceeds limits")


def build_skill_environment(
    profile: ArtifactBehaviorProfile, plan: DynamicReviewPlan,
) -> DynamicEnvironmentPlan:
    if profile.runtime_kind != "executable_skill":
        return DynamicEnvironmentPlan(
            fixtures=(), argv=(),
            reason_codes=("runtime_kind_not_executable",),
        )

    selected = {
        item.check_id for item in plan.items if item.status == "selected"
    }
    fixtures = []
    argv = []
    reasons = []

    if "json_document" in profile.inputs:
        fixtures.append(SyntheticFixture(
            "input.json",
            b'{"value":"verity-synthetic-input","count":1}',
            "declared_json_input",
        ))
        argv.append("input.json")
        reasons.append("declared_json_input_selected")

    # Import only fixed, trusted Verity-owned payloads. Nothing from the
    # reviewed artifact can choose their bytes or paths.
    if "sandbox_fake_credential_read" in selected:
        from verity.sandbox.runner import (
            _FAKE_CREDENTIAL_DECOY_CONTENT,
            _FAKE_CREDENTIAL_DECOY_NAME,
        )
        fixtures.append(SyntheticFixture(
            _FAKE_CREDENTIAL_DECOY_NAME,
            _FAKE_CREDENTIAL_DECOY_CONTENT,
            "selected_fake_credential_probe",
        ))
        reasons.append("credential_boundary_selected")

    if selected & {
        "sandbox_injected_content_propagation",
        "sandbox_sql_injected_query",
    }:
        from verity.sandbox.runner import (
            _INJECTED_CONTENT_DECOY_CONTENT,
            _INJECTED_CONTENT_DECOY_NAME,
        )
        fixtures.append(SyntheticFixture(
            _INJECTED_CONTENT_DECOY_NAME,
            _INJECTED_CONTENT_DECOY_CONTENT,
            "selected_untrusted_content_probe",
        ))
        reasons.append("external_content_boundary_selected")

    if "sandbox_deserialization_effect" in selected:
        from verity.sandbox.runner import (
            _DESERIALIZATION_DECOY_CONTENT,
            _DESERIALIZATION_DECOY_NAME,
        )
        fixtures.append(SyntheticFixture(
            _DESERIALIZATION_DECOY_NAME,
            _DESERIALIZATION_DECOY_CONTENT,
            "selected_deserialization_probe",
        ))
        reasons.append("deserialization_boundary_selected")

    return DynamicEnvironmentPlan(
        fixtures=tuple(fixtures),
        argv=tuple(argv),
        reason_codes=tuple(reasons or ("passive_observation_only",)),
    )
