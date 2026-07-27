"""Owner-only Web Provider preferences plus macOS Keychain credentials."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from ..history import (HistoryError, _atomic_json, _check_safe, _mkdir,
                       default_data_dir)
from .provider_web import ProviderWebError, validate_base_url


PREFERENCE_SCHEMA_VERSION = 1
PREFERENCE_RECORD_TYPE = "webProviderPreferences"
PREFERENCE_FILENAME = "web-provider.json"
MAX_PREFERENCE_BYTES = 16 * 1024
MAX_MODEL_LENGTH = 200
MAX_KEY_BYTES = 8 * 1024
KEYCHAIN_SERVICE = "com.verity.local-web.provider"
KEYCHAIN_ACCOUNT = "default"
KEYCHAIN_TIMEOUT_SECONDS = 10.0


class ProviderSettingsError(RuntimeError):
    """Stable, user-safe configuration-storage error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProviderPreferences:
    base_url: str = ""
    generator_model: str = ""
    validator_model: str = ""


def _validated_model(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ProviderSettingsError("bad_model", f"{label} must be a string")
    model = value.strip()
    if len(model) > MAX_MODEL_LENGTH or "\x00" in model:
        raise ProviderSettingsError(
            "bad_model", f"{label} must be at most {MAX_MODEL_LENGTH} characters")
    return model


def _validated_preferences(
        preferences: ProviderPreferences) -> ProviderPreferences:
    if not isinstance(preferences, ProviderPreferences):
        raise ProviderSettingsError(
            "bad_provider_settings", "provider settings have an invalid shape")
    if not isinstance(preferences.base_url, str):
        raise ProviderSettingsError(
            "bad_base_url", "base_url must be a string")
    try:
        base_url = (
            validate_base_url(preferences.base_url)
            if preferences.base_url else "")
    except ProviderWebError as exc:
        raise ProviderSettingsError(exc.code, exc.message) from exc
    return ProviderPreferences(
        base_url=base_url,
        generator_model=_validated_model(
            preferences.generator_model, "generator_model"),
        validator_model=_validated_model(
            preferences.validator_model, "validator_model"),
    )


class ProviderPreferenceStore:
    """Strict non-secret preference file under Verity's local data dir."""

    def __init__(self, root=None) -> None:
        self.root = Path(root) if root else default_data_dir()
        self.path = self.root / PREFERENCE_FILENAME

    def load(self) -> ProviderPreferences:
        if not self.path.exists():
            return ProviderPreferences()
        try:
            _check_safe(self.root, True)
            _check_safe(self.path)
            raw = self.path.read_bytes()
            if len(raw) > MAX_PREFERENCE_BYTES:
                raise ProviderSettingsError(
                    "provider_settings_corrupt",
                    "saved Provider settings exceed the size limit")

            def no_duplicates(pairs):
                out = {}
                for key, value in pairs:
                    if key in out:
                        raise ProviderSettingsError(
                            "provider_settings_corrupt",
                            "saved Provider settings contain duplicate fields")
                    out[key] = value
                return out

            obj = json.loads(
                raw.decode("utf-8"), object_pairs_hook=no_duplicates)
        except ProviderSettingsError:
            raise
        except (HistoryError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderSettingsError(
                "provider_settings_corrupt",
                "saved Provider settings cannot be read safely") from exc
        expected = {
            "schemaVersion", "recordType", "baseUrl",
            "generatorModel", "validatorModel",
        }
        if (
            not isinstance(obj, dict)
            or set(obj) != expected
            or obj.get("schemaVersion") != PREFERENCE_SCHEMA_VERSION
            or obj.get("recordType") != PREFERENCE_RECORD_TYPE
        ):
            raise ProviderSettingsError(
                "provider_settings_corrupt",
                "saved Provider settings have an unsupported format")
        return _validated_preferences(ProviderPreferences(
            base_url=obj.get("baseUrl"),
            generator_model=obj.get("generatorModel"),
            validator_model=obj.get("validatorModel"),
        ))

    def save(self, preferences: ProviderPreferences) -> ProviderPreferences:
        saved = _validated_preferences(preferences)
        obj = {
            "schemaVersion": PREFERENCE_SCHEMA_VERSION,
            "recordType": PREFERENCE_RECORD_TYPE,
            "baseUrl": saved.base_url,
            "generatorModel": saved.generator_model,
            "validatorModel": saved.validator_model,
        }
        try:
            _atomic_json(self.path, obj)
        except (HistoryError, OSError) as exc:
            raise ProviderSettingsError(
                "provider_settings_unavailable",
                "Provider settings could not be saved safely") from exc
        return saved

    def clear(self) -> None:
        if not self.path.exists():
            return
        try:
            _check_safe(self.root, True)
            _check_safe(self.path)
            self.path.unlink()
        except (HistoryError, OSError) as exc:
            raise ProviderSettingsError(
                "provider_settings_unavailable",
                "Provider settings could not be removed safely") from exc


class MacOSKeychainCredentialStore:
    """One Provider key stored in the current macOS login keychain."""

    def __init__(
            self, *, security_binary: Optional[str] = None,
            runner: Optional[Callable] = None,
            platform: Optional[str] = None) -> None:
        self.platform = platform or sys.platform
        self.security_binary = security_binary
        if self.security_binary is None and self.platform == "darwin":
            self.security_binary = shutil.which("security")
        self.runner = runner or subprocess.run

    def _available_binary(self) -> str:
        if self.platform != "darwin" or not self.security_binary:
            raise ProviderSettingsError(
                "credential_store_unavailable",
                "macOS Keychain is unavailable")
        return self.security_binary

    def _run(self, args, *, stdin_value: Optional[str] = None):
        binary = self._available_binary()
        try:
            return self.runner(
                [binary] + list(args),
                input=stdin_value,
                text=True,
                capture_output=True,
                timeout=KEYCHAIN_TIMEOUT_SECONDS,
                shell=False,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderSettingsError(
                "credential_store_unavailable",
                "macOS Keychain could not be accessed") from exc

    def save_key(self, api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderSettingsError(
                "api_key_required", "api_key is required")
        key = api_key.strip()
        if len(key.encode("utf-8")) > MAX_KEY_BYTES:
            raise ProviderSettingsError(
                "api_key_too_large", "api_key is too large")
        result = self._run([
            "add-generic-password", "-U",
            "-a", KEYCHAIN_ACCOUNT,
            "-s", KEYCHAIN_SERVICE,
            "-w",
        ], stdin_value=key + "\n" + key + "\n")
        if result.returncode != 0:
            raise ProviderSettingsError(
                "credential_store_unavailable",
                "API Key could not be saved to macOS Keychain")

    def load_key(self) -> Optional[str]:
        result = self._run([
            "find-generic-password",
            "-a", KEYCHAIN_ACCOUNT,
            "-s", KEYCHAIN_SERVICE,
            "-w",
        ])
        if result.returncode == 44:
            return None
        if result.returncode != 0:
            raise ProviderSettingsError(
                "credential_store_unavailable",
                "API Key could not be read from macOS Keychain")
        value = result.stdout.rstrip("\r\n")
        if not value:
            return None
        if len(value.encode("utf-8")) > MAX_KEY_BYTES:
            raise ProviderSettingsError(
                "credential_store_unavailable",
                "saved API Key has an invalid size")
        return value

    def has_key(self) -> bool:
        return self.load_key() is not None

    def delete_key(self) -> None:
        result = self._run([
            "delete-generic-password",
            "-a", KEYCHAIN_ACCOUNT,
            "-s", KEYCHAIN_SERVICE,
        ])
        if result.returncode not in {0, 44}:
            raise ProviderSettingsError(
                "credential_store_unavailable",
                "API Key could not be removed from macOS Keychain")


class ProviderSettingsStore:
    """Coordinates public preferences and the non-exportable credential."""

    def __init__(self, preferences: ProviderPreferenceStore,
                 credentials) -> None:
        self.preferences = preferences
        self.credentials = credentials

    def public_settings(
            self) -> Tuple[ProviderPreferences, bool]:
        return self.preferences.load(), self.credentials.has_key()

    def save(self, preferences: ProviderPreferences, *,
             api_key: str = "") -> ProviderPreferences:
        validated = _validated_preferences(preferences)
        had_preferences = self.preferences.path.exists()
        previous = self.preferences.load()
        if (
            not api_key
            and self.credentials.has_key()
            and previous.base_url != validated.base_url
        ):
            raise ProviderSettingsError(
                "api_key_required",
                "a new API Key is required when Provider address changes")
        saved = self.preferences.save(validated)
        if api_key:
            try:
                self.credentials.save_key(api_key)
            except ProviderSettingsError:
                try:
                    if had_preferences:
                        self.preferences.save(previous)
                    else:
                        self.preferences.clear()
                except ProviderSettingsError as rollback_error:
                    raise ProviderSettingsError(
                        "provider_settings_unavailable",
                        "Provider settings could not be rolled back safely",
                    ) from rollback_error
                raise
        return saved

    def resolve_key(self) -> Optional[str]:
        return self.credentials.load_key()

    def clear(self) -> None:
        self.credentials.delete_key()
        self.preferences.clear()


def create_provider_settings_store(
        *, root=None, credential_store=None) -> ProviderSettingsStore:
    _mkdir(Path(root) if root else default_data_dir())
    return ProviderSettingsStore(
        ProviderPreferenceStore(root),
        credential_store or MacOSKeychainCredentialStore(),
    )
