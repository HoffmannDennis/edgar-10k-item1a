"""Finite source/artifact policy checks, not a release-readiness benchmark."""

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_portable_runtime_source_has_no_private_path_or_dynamic_loader():
    forbidden = re.compile(
        r"/home/|/Users/|/Volumes/|sys\.path|"
        r"setrecursionlimit|threading\.stack_size|\bimport polars\b"
    )
    violations = []
    for path in (ROOT / "src").rglob("*"):
        if path.suffix in {".py", ".json"}:
            for line, text in enumerate(path.read_text().splitlines(), 1):
                if forbidden.search(text):
                    violations.append(f"{path.relative_to(ROOT)}:{line}")
    assert violations == []


def test_build_and_runtime_dependencies_are_separate():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["dependencies"] == ["pydantic>=2.13,<3"]
    assert "hatchling" in project["build-system"]["requires"][0]
    assert "pytest" in project["project"]["optional-dependencies"]["dev"][0]
    allowed = project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert "/src" in allowed
    assert not list((ROOT / ".github").glob("workflows/*"))


def test_license_metadata_and_copyright_notice():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"] == "MIT" and project["license-files"] == ["LICENSE"]
    license_text = (ROOT / "LICENSE").read_text()
    assert license_text.startswith("MIT License\n") and "Copyright (c) 2026 Dennis Hoffmann" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert not (ROOT / "NOTICE").exists()
