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
| jsonschema-specifications | 2025.9.1 | MIT | https://github.com/python-jsonschema/jsonschema-specifications |
| referencing | 0.36.2 | MIT | https://github.com/python-jsonschema/referencing |
| rpds-py | 0.27.1 | MIT | https://github.com/crate-py/rpds |
| attrs | 26.1.0 | MIT | https://github.com/python-attrs/attrs |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |

## Development / test only

| Package | Version | License | Source |
|---|---|---|---|
| pytest | 8.4.2 | MIT | https://github.com/pytest-dev/pytest |
| iniconfig | 2.1.0 | MIT | https://github.com/pytest-dev/iniconfig |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| pluggy | 1.6.0 | MIT | https://github.com/pytest-dev/pluggy |
| Pygments | 2.20.0 | BSD-2-Clause | https://github.com/pygments/pygments |
| tomli | 2.4.1 | MIT | https://github.com/hukkin/tomli (Python < 3.11 only) |
| exceptiongroup | 1.3.1 | MIT | https://github.com/agronholm/exceptiongroup (Python < 3.11 only) |

No network calls are made at runtime. No dependencies bundle native
binaries that require additional notices.
