"""Persistent Web Provider settings without plaintext credential storage."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from threading import Event, Thread, current_thread

import pytest

from verity.web.provider_settings import (
    MacOSKeychainCredentialStore,
    ProviderPreferenceStore,
    ProviderPreferences,
    ProviderSettingsError,
    ProviderSettingsStore,
)


@dataclass
class _Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class _MemoryCredentials:
    def __init__(self):
        self.value = None

    def save_key(self, value):
        self.value = value

    def load_key(self):
        return self.value

    def has_key(self):
        return self.value is not None

    def delete_key(self):
        self.value = None


class _FailingSaveCredentials(_MemoryCredentials):
    def save_key(self, value):
        raise ProviderSettingsError(
            "credential_store_unavailable", "test credential failure")


class _BlockingSaveCredentials(_MemoryCredentials):
    def __init__(self, value):
        super().__init__()
        self.value = value
        self.save_started = Event()
        self.finish_save = Event()
        self.resolver_loaded = Event()

    def save_key(self, value):
        self.save_started.set()
        if not self.finish_save.wait(2):
            raise AssertionError("timed out waiting to finish credential save")
        self.value = value

    def load_key(self):
        value = super().load_key()
        if current_thread().name == "settings-resolver":
            self.resolver_loaded.set()
        return value


class _ObservedPreferenceStore(ProviderPreferenceStore):
    def __init__(self, root):
        super().__init__(root)
        self.resolver_loaded = Event()
        self.finish_resolver_load = Event()

    def load(self):
        preferences = super().load()
        if current_thread().name == "settings-resolver":
            self.resolver_loaded.set()
            if not self.finish_resolver_load.wait(2):
                raise AssertionError(
                    "timed out waiting to finish preference load")
        return preferences


def test_preferences_are_owner_only_and_never_contain_api_key(tmp_path):
    store = ProviderPreferenceStore(tmp_path)
    saved = store.save(ProviderPreferences(
        base_url="https://openrouter.ai/api/v1/",
        generator_model="openai/generator",
        validator_model="anthropic/validator",
    ))

    assert saved.base_url == "https://openrouter.ai/api/v1"
    path = tmp_path / "web-provider.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_text()
    assert "apiKey" not in raw
    assert "secret" not in raw.lower()
    assert json.loads(raw) == {
        "baseUrl": "https://openrouter.ai/api/v1",
        "generatorModel": "openai/generator",
        "recordType": "webProviderPreferences",
        "schemaVersion": 1,
        "validatorModel": "anthropic/validator",
    }
    assert store.load() == saved


def test_preferences_reject_world_readable_file(tmp_path):
    store = ProviderPreferenceStore(tmp_path)
    store.save(ProviderPreferences(base_url="https://saved.example"))
    store.path.chmod(0o644)

    with pytest.raises(ProviderSettingsError) as exc:
        store.load()

    assert exc.value.code == "provider_settings_corrupt"


@pytest.mark.parametrize("base_url", [123, "https://user:pass@example.com"])
def test_invalid_base_url_has_stable_settings_error(tmp_path, base_url):
    store = ProviderPreferenceStore(tmp_path)
    with pytest.raises(ProviderSettingsError) as exc:
        store.save(ProviderPreferences(base_url=base_url))
    assert exc.value.code == "bad_base_url"


def test_keychain_save_uses_stdin_not_process_arguments():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return _Result()

    store = MacOSKeychainCredentialStore(
        security_binary="/usr/bin/security",
        runner=runner,
        platform="darwin",
    )
    key = "provider-" + "secret-value"
    store.save_key(key)

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[-1] == "-w"
    assert key not in args
    assert kwargs["input"] == key + "\n" + key + "\n"
    assert kwargs["shell"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["start_new_session"] is True


def test_keychain_load_and_missing_item_are_bounded():
    responses = [
        _Result(returncode=0, stdout="saved-key\n"),
        _Result(returncode=44),
    ]

    def runner(_args, **_kwargs):
        return responses.pop(0)

    store = MacOSKeychainCredentialStore(
        security_binary="/usr/bin/security",
        runner=runner,
        platform="darwin",
    )
    assert store.load_key() == "saved-key"
    assert store.load_key() is None


def test_non_macos_keychain_fails_without_plaintext_fallback():
    store = MacOSKeychainCredentialStore(
        security_binary=None,
        platform="linux",
    )
    with pytest.raises(ProviderSettingsError) as exc:
        store.save_key("not-written")
    assert exc.value.code == "credential_store_unavailable"


def test_combined_store_keeps_existing_key_when_save_omits_key(tmp_path):
    credentials = _MemoryCredentials()
    store = ProviderSettingsStore(
        ProviderPreferenceStore(tmp_path), credentials)
    prefs = ProviderPreferences(
        base_url="https://openrouter.ai/api/v1",
        generator_model="g",
        validator_model="v",
    )

    store.save(prefs, api_key="first-key")
    store.save(prefs, api_key="")

    loaded, key_saved = store.public_settings()
    assert loaded == prefs
    assert key_saved is True
    assert store.resolve_key() == "first-key"

    store.clear()
    assert store.public_settings() == (ProviderPreferences(), False)
    assert store.resolve_key() is None


def test_failed_keychain_save_rolls_back_non_secret_preferences(tmp_path):
    preferences = ProviderPreferenceStore(tmp_path)
    old = ProviderPreferences(
        base_url="https://old.example",
        generator_model="old-g",
        validator_model="old-v",
    )
    preferences.save(old)
    store = ProviderSettingsStore(
        preferences, _FailingSaveCredentials())

    with pytest.raises(ProviderSettingsError) as exc:
        store.save(ProviderPreferences(
            base_url="https://new.example",
            generator_model="new-g",
            validator_model="new-v",
        ), api_key="new-key")

    assert exc.value.code == "credential_store_unavailable"
    assert preferences.load() == old


def test_saved_key_cannot_be_rebound_to_new_provider_without_new_key(
        tmp_path):
    preferences = ProviderPreferenceStore(tmp_path)
    credentials = _MemoryCredentials()
    store = ProviderSettingsStore(preferences, credentials)
    old = ProviderPreferences(
        base_url="https://saved.example",
        generator_model="old-g",
        validator_model="old-v",
    )
    store.save(old, api_key="saved-key")

    with pytest.raises(ProviderSettingsError) as exc:
        store.save(ProviderPreferences(
            base_url="https://other.example",
            generator_model="new-g",
            validator_model="new-v",
        ))

    assert exc.value.code == "api_key_required"
    assert store.public_settings() == (old, True)
    assert store.resolve_key() == "saved-key"


def test_resolution_cannot_observe_in_progress_save(tmp_path):
    preferences = _ObservedPreferenceStore(tmp_path)
    old = ProviderPreferences(base_url="https://old.example")
    new = ProviderPreferences(base_url="https://new.example")
    preferences.save(old)
    credentials = _BlockingSaveCredentials("old-test-key")
    store = ProviderSettingsStore(preferences, credentials)
    thread_errors = []
    resolution_outcome = []

    def save():
        try:
            store.save(new, api_key="new-test-key")
        except BaseException as exc:
            thread_errors.append(exc)

    def resolve():
        try:
            loaded, key_saved = store.public_settings()
            key = store.resolve_key()
            resolution_outcome.append((
                loaded == new,
                key_saved,
                key == "new-test-key",
            ))
        except BaseException as exc:
            thread_errors.append(exc)

    saver = Thread(target=save, daemon=True)
    resolver = Thread(
        target=resolve, name="settings-resolver", daemon=True)
    saver.start()
    assert credentials.save_started.wait(2)
    resolver.start()

    observed_partial_save = preferences.resolver_loaded.wait(0.5)
    preferences.finish_resolver_load.set()
    if observed_partial_save:
        assert credentials.resolver_loaded.wait(2)
    credentials.finish_save.set()
    saver.join(3)
    resolver.join(3)

    assert saver.is_alive() is False
    assert resolver.is_alive() is False
    if thread_errors:
        raise thread_errors[0]
    assert resolution_outcome == [(True, True, True)]


@pytest.mark.parametrize("mutation", ["save", "clear"])
def test_resolution_rejects_concurrent_provider_mutation(
        tmp_path, mutation):
    credentials = _MemoryCredentials()
    store = ProviderSettingsStore(
        ProviderPreferenceStore(tmp_path), credentials)
    old = ProviderPreferences(base_url="https://old.example")
    store.save(old, api_key="old-test-key")

    snapshot_ready = Event()
    mutation_done = Event()
    thread_errors = []
    resolution_outcome = []

    def resolve():
        try:
            preferences, key_saved = store.public_settings()
            assert preferences == old
            assert key_saved is True
            snapshot_ready.set()
            assert mutation_done.wait(2)
            try:
                store.resolve_key()
            except ProviderSettingsError as exc:
                resolution_outcome.append(("error", exc.code))
            else:
                resolution_outcome.append(("returned",))
        except BaseException as exc:
            thread_errors.append(exc)
            snapshot_ready.set()

    def mutate():
        try:
            assert snapshot_ready.wait(2)
            if mutation == "save":
                store.save(
                    ProviderPreferences(base_url="https://new.example"),
                    api_key="new-test-key",
                )
            else:
                store.clear()
        except BaseException as exc:
            thread_errors.append(exc)
        finally:
            mutation_done.set()

    resolver = Thread(target=resolve, daemon=True)
    mutator = Thread(target=mutate, daemon=True)
    resolver.start()
    mutator.start()
    resolver.join(3)
    mutator.join(3)

    assert resolver.is_alive() is False
    assert mutator.is_alive() is False
    if thread_errors:
        raise thread_errors[0]
    assert resolution_outcome == [
        ("error", "provider_settings_changed"),
    ]
