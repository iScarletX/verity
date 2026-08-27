from __future__ import annotations

import os
import ipaddress
import math
from pathlib import Path
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple
import unicodedata
from urllib.parse import urlsplit


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_KNOWN_SCENARIOS = frozenset({"agent_primary_task", "agent_untrusted_content"})
_MAX_TIMEOUT_SECONDS = 300.0
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
_MAX_TRACE_EVENTS = 4096
_MAX_MODEL_ID_CHARS = 256
_MAX_BASE_URL_CHARS = 2048
_MAX_EXECUTABLE_PATH_CHARS = 4096


@dataclass(frozen=True)
class AgentRuntimeCredentials:
    api_key_env: Optional[str] = None

    def resolve(self) -> Optional[str]:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


@dataclass(frozen=True)
class AgentRuntimeConfig:
    enabled: bool = False
    dsh_executable: str = ""
    dsh_sha256: str = ""
    node_executable: str = ""
    node_sha256: str = ""
    expected_version: str = "0.1.1-rc.2"
    base_url: str = ""
    model_id: str = ""
    credentials: AgentRuntimeCredentials = field(default_factory=AgentRuntimeCredentials)
    scenario_ids: Tuple[str, ...] = (
        "agent_primary_task",
        "agent_untrusted_content",
    )
    timeout_seconds: float = 90.0
    max_stdout_bytes: int = 262_144
    max_stderr_bytes: int = 65_536
    max_trace_events: int = 128

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if not self.enabled:
            return
        _validate_executable_path("dsh_executable", self.dsh_executable)
        if not isinstance(self.dsh_sha256, str) or not _SHA256_RE.fullmatch(self.dsh_sha256):
            raise ValueError("dsh_sha256 must be exactly 64 hexadecimal characters")
        if not self.dsh_sha256:
            raise ValueError("dsh_sha256 is required when enabled")
        if self.expected_version != "0.1.1-rc.2":
            raise ValueError("expected_version must be exactly 0.1.1-rc.2")
        _validate_executable_path("node_executable", self.node_executable)
        if not self.node_sha256:
            raise ValueError("node_sha256 is required when enabled")
        if not isinstance(self.node_sha256, str) or not _SHA256_RE.fullmatch(self.node_sha256):
            raise ValueError("node_sha256 must be exactly 64 hexadecimal characters")
        self._validate_base_url()
        self._validate_model_id()
        self._validate_credentials()
        self._validate_scenarios()
        self._validate_budgets()

    def _validate_base_url(self) -> None:
        if (
            not isinstance(self.base_url, str)
            or not self.base_url
            or len(self.base_url) > _MAX_BASE_URL_CHARS
            or _has_control(self.base_url)
        ):
            raise ValueError("base_url must be a bounded URL without controls")
        try:
            parsed = urlsplit(self.base_url)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("base_url is malformed") from exc
        if (
            not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must not contain credentials, query, or fragment")
        if parsed.scheme == "https":
            return
        if parsed.scheme == "http" and _is_loopback_host(hostname):
            return
        raise ValueError("base_url must use HTTPS or loopback HTTP")

    def _validate_model_id(self) -> None:
        if (
            not isinstance(self.model_id, str)
            or not self.model_id
            or len(self.model_id) > _MAX_MODEL_ID_CHARS
            or _has_control(self.model_id)
        ):
            raise ValueError("model_id must be nonempty, bounded, and contain no controls")

    def _validate_credentials(self) -> None:
        if not isinstance(self.credentials, AgentRuntimeCredentials):
            raise ValueError("credentials must be AgentRuntimeCredentials")
        name = self.credentials.api_key_env
        if name is not None and (
            not isinstance(name, str) or not _ENV_NAME_RE.fullmatch(name)
        ):
            raise ValueError("api_key_env must be a valid environment variable name")

    def _validate_scenarios(self) -> None:
        values = self.scenario_ids
        if (
            not isinstance(values, tuple)
            or not values
            or len(values) > len(_KNOWN_SCENARIOS)
            or len(set(values)) != len(values)
            or any(not isinstance(value, str) or value not in _KNOWN_SCENARIOS for value in values)
        ):
            raise ValueError("scenario_ids must be nonempty, unique, known, and bounded")

    def _validate_budgets(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be finite, positive, and bounded")
        _validate_positive_bounded_int(
            "max_stdout_bytes", self.max_stdout_bytes, _MAX_OUTPUT_BYTES
        )
        _validate_positive_bounded_int(
            "max_stderr_bytes", self.max_stderr_bytes, _MAX_OUTPUT_BYTES
        )
        _validate_positive_bounded_int(
            "max_trace_events", self.max_trace_events, _MAX_TRACE_EVENTS
        )


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char).startswith("C") for char in value)


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_positive_bounded_int(name: str, value: object, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ValueError(f"{name} must be a positive bounded integer")


def _validate_executable_path(name: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_EXECUTABLE_PATH_CHARS
        or _has_control(value)
    ):
        raise ValueError(f"{name} must be a bounded string without controls")
    if not Path(value).is_absolute():
        raise ValueError(f"{name} must be an absolute path")
