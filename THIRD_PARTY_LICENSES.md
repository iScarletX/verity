# Third-party dependency licenses

Verity itself is Apache-2.0 (see `LICENSE`).

The following runtime and development dependencies are pinned in
`requirements.lock` / `requirements-dev.lock`. All licenses below have
been checked to be compatible with Apache-2.0 distribution.

## Runtime

| Package | Version | License | Source |
|---|---|---|---|
| jsonschema | 4.25.1 | MIT | https://github.com/python-jsonschema/jsonschema |
| PyYAML | 6.0.3 | MIT | https://github.com/yaml/pyyaml |
| bandit | 1.7.10 | Apache-2.0 | https://github.com/PyCQA/bandit |
| stevedore | 5.5.0 | Apache-2.0 | https://opendev.org/openstack/stevedore |
| rich | 15.0.0 | MIT | https://github.com/Textualize/rich |
| markdown-it-py | 3.0.0 | MIT | https://github.com/executablebooks/markdown-it-py |
| mdurl | 0.1.2 | MIT | https://github.com/executablebooks/mdurl |
| Pygments | 2.20.0 | BSD-2-Clause | https://github.com/pygments/pygments |
| jsonschema-specifications | 2025.9.1 | MIT | https://github.com/python-jsonschema/jsonschema-specifications |
| referencing | 0.36.2 | MIT | https://github.com/python-jsonschema/referencing |
| rpds-py | 0.27.1 | MIT | https://github.com/crate-py/rpds |
| attrs | 26.1.0 | MIT | https://github.com/python-attrs/attrs |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| starlette | 0.41.3 | BSD-3-Clause | https://github.com/encode/starlette |
| python-multipart | 0.0.20 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| anyio | 4.12.1 | MIT | https://github.com/agronholm/anyio |
| sniffio | 1.3.1 | MIT-0 / Apache-2.0 | https://github.com/python-trio/sniffio |
| uvicorn | 0.32.1 | BSD-3-Clause | https://github.com/encode/uvicorn |
| click | 8.1.8 | BSD-3-Clause | https://github.com/pallets/click |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |

## Development / test only

| Package | Version | License | Source |
|---|---|---|---|
| pytest | 8.4.2 | MIT | https://github.com/pytest-dev/pytest |
| iniconfig | 2.1.0 | MIT | https://github.com/pytest-dev/iniconfig |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| pluggy | 1.6.0 | MIT | https://github.com/pytest-dev/pluggy |
| tomli | 2.4.1 | MIT | https://github.com/hukkin/tomli (Python < 3.11 only) |
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx (Starlette TestClient) |
| httpcore | 1.0.9 | BSD-3-Clause | https://github.com/encode/httpcore |
| certifi | 2026.6.17 | MPL-2.0 | https://github.com/certifi/python-certifi |
| idna | 3.18 | BSD-3-Clause | https://github.com/kjd/idna |
| exceptiongroup | 1.3.1 | MIT | https://github.com/agronholm/exceptiongroup (Python < 3.11 only) |

No network calls are made at runtime. No dependencies bundle native
binaries that require additional notices.

## External binaries (not vendored)

| Tool | Version | License | Source |
|---|---|---|---|
| gitleaks | 8.28.0 | MIT | https://github.com/gitleaks/gitleaks |

The gitleaks binary is **not** committed to this repository.
``tools/install_gitleaks.py`` downloads it once from the official
GitHub Release page (URL and SHA-256 pinned in
``tools/gitleaks_release.json``) and installs into
``.tools/gitleaks/<version>/`` (gitignored). A per-install manifest
(``.tools/gitleaks/<version>/manifest.json``) records both the archive
SHA-256 (upstream) and the extracted binary SHA-256 (computed on this
machine). Verity's runtime re-verifies the binary SHA-256 on every
invocation. A missing / mismatched / mis-versioned binary is an
Analyzer failure with Coverage insufficient (see
``verity/gitleaks_runner.py``).
