"""Skill ZIP upload path: /api/review/skill with archive_format=zip.

Mirrors the multipart folder-upload tests in test_web_mvp.py but exercises
the new server-side ZIP-extraction branch (verity.web.app._extract_skill_zip)
-- same path-safety guarantees (zip-slip rejection, per-file/total size
budgets, zip-bomb guard via incremental decompression) applied to archive
entries instead of individually-posted multipart files.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from starlette.testclient import TestClient

from verity.web import create_app


class _EmptyWebCredentials:
    """See test_web_mvp.py's identical class for the full rationale: never
    let a test app instance fall back to the real macOS Keychain."""

    def save_key(self, value):
        raise AssertionError("this test credential store must remain empty")

    def load_key(self):
        return None

    def has_key(self):
        return False

    def delete_key(self):
        return None


@pytest.fixture
def client(tmp_path):
    from verity.web.provider_settings import (
        ProviderPreferenceStore, ProviderSettingsStore)
    provider_settings = ProviderSettingsStore(
        ProviderPreferenceStore(tmp_path / "provider"), _EmptyWebCredentials())
    app = create_app(store_capacity=8, store_ttl_seconds=60,
                     history_root=tmp_path / "history",
                     provider_settings_store=provider_settings)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _zip_bytes(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _post_zip(client, entries: dict, *, filename="skill.zip"):
    data = _zip_bytes(entries)
    fields = [
        ("profile", (None, "standard")),
        ("archive_format", (None, "zip")),
        ("files", (filename, data, "application/zip")),
    ]
    return client.post("/api/review/skill", files=fields)


SKILL_MD = "---\nname: demo-skill\ndescription: a demo skill\n---\n# Demo\n"


class TestSkillZipUpload:
    def test_wrapped_zip_extracts_and_reviews(self, client):
        r = _post_zip(client, {
            "demo-skill/SKILL.md": SKILL_MD,
            "demo-skill/scripts/run.py": "print('hi')\n",
        })
        assert r.status_code == 200, r.text
        view = r.json()
        assert view["engine"] == "skill"
        # The client has no per-entry File objects for a ZIP upload, so the
        # server must echo back decoded text for original-text evidence.
        assert "SKILL.md" in view["sourceFiles"]
        assert view["sourceFiles"]["SKILL.md"] == SKILL_MD

    def test_flat_zip_with_no_wrapping_folder_extracts(self, client):
        r = _post_zip(client, {
            "SKILL.md": SKILL_MD,
            "scripts/run.py": "print('hi')\n",
        })
        assert r.status_code == 200, r.text
        view = r.json()
        assert view["engine"] == "skill"
        assert view["sourceFiles"]["SKILL.md"] == SKILL_MD

    def test_zip_slip_entry_rejected(self, client):
        r = _post_zip(client, {
            "demo-skill/SKILL.md": SKILL_MD,
            "../../etc/passwd": "malicious",
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_path"

    def test_absolute_path_entry_rejected(self, client):
        r = _post_zip(client, {
            "demo-skill/SKILL.md": SKILL_MD,
            "/etc/passwd": "malicious",
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_path"

    def test_not_a_real_zip_rejected(self, client):
        fields = [
            ("profile", (None, "standard")),
            ("archive_format", (None, "zip")),
            ("files", ("skill.zip", b"not a zip file", "application/zip")),
        ]
        r = client.post("/api/review/skill", files=fields)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_zip"

    def test_non_zip_filename_rejected(self, client):
        data = _zip_bytes({"demo-skill/SKILL.md": SKILL_MD})
        fields = [
            ("profile", (None, "standard")),
            ("archive_format", (None, "zip")),
            ("files", ("skill.txt", data, "application/octet-stream")),
        ]
        r = client.post("/api/review/skill", files=fields)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_archive"

    def test_multiple_files_with_zip_archive_format_rejected(self, client):
        data = _zip_bytes({"demo-skill/SKILL.md": SKILL_MD})
        fields = [
            ("profile", (None, "standard")),
            ("archive_format", (None, "zip")),
            ("files", ("skill.zip", data, "application/zip")),
            ("files", ("extra.zip", data, "application/zip")),
        ]
        r = client.post("/api/review/skill", files=fields)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_archive"

    def test_oversized_entry_rejected_without_decompression_bomb(self, client):
        # A single entry declaring far more than the per-file budget --
        # the incremental read must bail before buffering the whole thing.
        from verity.web.app import MAX_SKILL_FILE_BYTES
        big = "x" * (MAX_SKILL_FILE_BYTES + 1024)
        r = _post_zip(client, {
            "demo-skill/SKILL.md": SKILL_MD,
            "demo-skill/big.txt": big,
        })
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "file_too_large"

    def test_empty_zip_rejected(self, client):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass
        fields = [
            ("profile", (None, "standard")),
            ("archive_format", (None, "zip")),
            ("files", ("skill.zip", buf.getvalue(), "application/zip")),
        ]
        r = client.post("/api/review/skill", files=fields)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "no_files"

    def test_bad_archive_format_value_rejected(self, client):
        data = _zip_bytes({"demo-skill/SKILL.md": SKILL_MD})
        fields = [
            ("profile", (None, "standard")),
            ("archive_format", (None, "tar")),
            ("files", ("skill.zip", data, "application/zip")),
        ]
        r = client.post("/api/review/skill", files=fields)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_archive_format"

    def test_folder_upload_unaffected_no_source_files_key(self, client):
        fields = [
            ("profile", (None, "standard")),
            ("files", ("demo-skill/SKILL.md", SKILL_MD.encode("utf-8"),
                       "application/octet-stream")),
        ]
        r = client.post("/api/review/skill", files=fields)
        assert r.status_code == 200, r.text
        assert "sourceFiles" not in r.json()
