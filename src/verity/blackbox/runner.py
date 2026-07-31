"""V1.5 black-box runner: sends probes against a real model and records results.

The runner is intentionally minimal:
- Takes a system prompt (the artifact under review) + a list of scenarios.
- Sends each probe as a user turn against the model (with the system prompt
  pre-loaded).
- Applies the scenario's judge function to the response.
- Records every call, response digest, and result in a structured report.
- Respects the provider budget; stops and records partial results if exhausted.

The runner does NOT call an LLM to interpret judgment results — judges are
deterministic functions. Callers who need richer interpretation can post-process
the raw response text themselves.
"""
from __future__ import annotations

import hashlib
import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .scenarios import ProbeScenario


_MAX_RESPONSE_BYTES = 64 * 1024   # 64 KB per response is enough for text
_MAX_CONTENT_CHARS = 8000          # truncate before judge; keep log manageable

BLACKBOX_SCHEMA_VERSION = 1


@dataclass
class ProbeResult:
    scenario_id: str
    probe_index: int           # 0-based within the scenario's probe list
    probe_text: str
    response_text: Optional[str]   # None if call failed
    safe: Optional[bool]           # True=safe, False=failed, None=error
    call_id: str
    response_digest: Optional[str] # sha256 of raw response bytes
    duration_seconds: float
    error_code: Optional[str] = None


@dataclass
class ScenarioResult:
    scenario_id: str
    title: str
    severity: str
    probe_results: List[ProbeResult] = field(default_factory=list)

    @property
    def total_probes(self) -> int:
        return len(self.probe_results)

    @property
    def safe_count(self) -> int:
        return sum(1 for r in self.probe_results if r.safe is True)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.probe_results if r.safe is False)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.probe_results if r.safe is None)

    @property
    def verdict(self) -> str:
        """passed | failed | error | partial"""
        if self.error_count == self.total_probes:
            return "error"
        if self.failed_count > 0:
            return "failed"
        if self.error_count > 0:
            return "partial"
        return "passed"


@dataclass
class BlackboxRunResult:
    schema_version: int = BLACKBOX_SCHEMA_VERSION
    system_prompt_digest: str = ""
    model_id: str = ""
    total_scenarios: int = 0
    completed_scenarios: int = 0
    scenario_results: List[ScenarioResult] = field(default_factory=list)
    total_calls: int = 0
    budget_exhausted: bool = False
    errors: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        passed = sum(1 for r in self.scenario_results if r.verdict == "passed")
        failed = sum(1 for r in self.scenario_results if r.verdict == "failed")
        error = sum(1 for r in self.scenario_results if r.verdict == "error")
        partial = sum(1 for r in self.scenario_results if r.verdict == "partial")
        return {
            "model": self.model_id,
            "totalScenarios": self.total_scenarios,
            "completed": self.completed_scenarios,
            "passed": passed,
            "failed": failed,
            "errors": error,
            "partial": partial,
            "totalCalls": self.total_calls,
            "budgetExhausted": self.budget_exhausted,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def _chat_once(
    *,
    opener: urllib.request.OpenerDirector,
    base_url: str,
    api_key: str,
    model_id: str,
    system_prompt: str,
    conversation: List[Dict[str, str]],
    max_tokens: int = 800,
    timeout: float = 30.0,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Send one chat-completions request.

    Returns (response_text, response_digest, error_code).
    response_text and response_digest are None on error.
    """
    messages = [{"role": "system", "content": system_prompt}] + conversation
    body = json.dumps({
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
        "temperature": 0.0,
    }, ensure_ascii=False).encode("utf-8")

    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
            "User-Agent": "Verity-BlackBox/1",
        })
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", resp.getcode()))
            if not 200 <= status < 300:
                return None, None, f"http_{status}"
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return None, None, f"http_{exc.code}"
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        return None, None, "network_error"

    if len(raw) > _MAX_RESPONSE_BYTES:
        return None, None, "response_too_large"

    digest = hashlib.sha256(raw).hexdigest()
    try:
        envelope = json.loads(raw.decode("utf-8"))
        choices = envelope.get("choices") or []
        if not choices:
            return None, digest, "no_choices"
        content = choices[0].get("message", {}).get("content") or ""
        return content[:_MAX_CONTENT_CHARS], digest, None
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return None, digest, "parse_error"


def run_blackbox(
    *,
    system_prompt: str,
    scenarios: List[ProbeScenario],
    base_url: str,
    model_id: str,
    api_key: str,
    max_calls: int = 50,
    timeout_seconds: float = 30.0,
    max_tokens_per_response: int = 800,
) -> BlackboxRunResult:
    """Run all scenarios against a real model.

    ``max_calls`` is a hard ceiling on outbound calls; the runner stops and
    records partial results if reached (``budget_exhausted=True``).
    """
    prompt_digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    opener = _build_opener()
    result = BlackboxRunResult(
        system_prompt_digest=prompt_digest,
        model_id=model_id,
        total_scenarios=len(scenarios),
    )
    call_counter = [0]

    for scenario in scenarios:
        if call_counter[0] >= max_calls:
            result.budget_exhausted = True
            break

        sr = ScenarioResult(
            scenario_id=scenario.scenario_id,
            title=scenario.title,
            severity=scenario.severity,
        )
        conversation: List[Dict[str, str]] = []
        scenario_ok = True

        for probe_idx, probe_text in enumerate(scenario.probes):
            if call_counter[0] >= max_calls:
                result.budget_exhausted = True
                scenario_ok = False
                break

            conversation.append({"role": "user", "content": probe_text})
            call_id = f"bb-{scenario.scenario_id[:16]}-p{probe_idx}"
            t0 = time.monotonic()
            response_text, digest, error_code = _chat_once(
                opener=opener,
                base_url=base_url,
                api_key=api_key,
                model_id=model_id,
                system_prompt=system_prompt,
                conversation=list(conversation),
                max_tokens=max_tokens_per_response,
                timeout=timeout_seconds,
            )
            duration = time.monotonic() - t0
            call_counter[0] += 1

            if error_code:
                pr = ProbeResult(
                    scenario_id=scenario.scenario_id,
                    probe_index=probe_idx, probe_text=probe_text,
                    response_text=None, safe=None,
                    call_id=call_id, response_digest=digest,
                    duration_seconds=duration, error_code=error_code,
                )
            else:
                try:
                    safe = scenario.judge(response_text or "")
                except Exception:
                    safe = None
                    error_code = "judge_error"
                pr = ProbeResult(
                    scenario_id=scenario.scenario_id,
                    probe_index=probe_idx, probe_text=probe_text,
                    response_text=response_text, safe=safe,
                    call_id=call_id, response_digest=digest,
                    duration_seconds=duration, error_code=error_code,
                )
                # Add model response to conversation for multi-turn probes
                if response_text:
                    conversation.append(
                        {"role": "assistant", "content": response_text})

            sr.probe_results.append(pr)

        result.scenario_results.append(sr)
        result.completed_scenarios += 1
        result.total_calls = call_counter[0]

    result.total_calls = call_counter[0]
    return result
