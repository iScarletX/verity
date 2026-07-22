"""Compatibility build metadata for older offline setuptools.

Modern installers use ``pyproject.toml``. macOS system Python may ship an old
setuptools that silently ignores PEP 621 and builds an empty UNKNOWN wheel when
``--no-build-isolation`` is required offline. Keep this small fallback aligned
with pyproject.toml; Round-20 tests verify version and installed package assets.
"""
from setuptools import find_packages, setup

setup(
    name="verity",
    version="0.1.0",
    description="Verity — local read-only Prompt & Skill auditor (V1 engineering preview)",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    license="Apache-2.0",
    python_requires=">=3.9,<3.14",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"verity.web": ["static/*.css", "static/*.html", "static/*.js"]},
    include_package_data=True,
    install_requires=[
        "jsonschema>=4.20,<5",
        "PyYAML>=6,<7",
        "bandit>=1.7.10,<1.8",
        "starlette>=0.41,<0.42",
        "python-multipart>=0.0.20,<0.1",
        "anyio>=4,<5",
    ],
    extras_require={
        "dev": ["pytest>=7,<9", "httpx>=0.27,<1"],
        "web": ["uvicorn>=0.30,<0.34"],
    },
    entry_points={"console_scripts": ["verity=verity.cli:main"]},
)
