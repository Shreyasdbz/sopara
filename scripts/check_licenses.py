from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "infra" / "policies" / "allowed-licenses.txt"
NODE_MODULES = ROOT / "web" / "node_modules"
PYTHON_LICENSE_OVERRIDES = {
    # google-crc32c 1.8.0 ships the Apache-2.0 text but reports UNKNOWN in
    # Core Metadata. Keep this package-specific so UNKNOWN never becomes allowed.
    "google-crc32c": "Apache-2.0",
}


def allowed_licenses() -> set[str]:
    return {
        line.strip()
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def python_licenses() -> dict[str, str]:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and module invocation
        [sys.executable, "-m", "piplicenses", "--format=json", "--with-system"],
        check=True,
        capture_output=True,
        text=True,
    )
    report = cast("list[dict[str, object]]", json.loads(result.stdout))
    licenses: dict[str, str] = {}
    for package in report:
        name = package.get("Name")
        license_name = package.get("License")
        if not isinstance(name, str) or not isinstance(license_name, str):
            continue
        licenses[name] = PYTHON_LICENSE_OVERRIDES.get(name, license_name.strip())
    return licenses


def is_package_root(path: Path) -> bool:
    relative = path.relative_to(NODE_MODULES)
    parts = relative.parts
    return len(parts) == 2 or (len(parts) == 3 and parts[0].startswith("@"))


def normalize_node_license(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        license_type = cast("dict[str, object]", value).get("type")
        if isinstance(license_type, str):
            return license_type.strip()
    return ""


def node_licenses() -> dict[str, str]:
    licenses: dict[str, str] = {}
    for package_json in NODE_MODULES.rglob("package.json"):
        if not is_package_root(package_json):
            continue
        package = json.loads(package_json.read_text(encoding="utf-8"))
        name = package.get("name")
        if isinstance(name, str):
            licenses[name] = normalize_node_license(package.get("license"))
    return licenses


def violations(ecosystem: str, packages: dict[str, str], allowed: set[str]) -> list[str]:
    failures: list[str] = []
    for package, license_name in sorted(packages.items(), key=lambda item: item[0].lower()):
        if not license_name:
            failures.append(f"{ecosystem}:{package}: missing license metadata")
        elif license_name not in allowed:
            failures.append(f"{ecosystem}:{package}: unapproved license {license_name!r}")
    return failures


def main() -> None:
    allowed = allowed_licenses()
    python = python_licenses()
    node = node_licenses()
    failures = violations("python", python, allowed) + violations("web", node, allowed)
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"license checks passed ({len(python)} Python, {len(node)} web packages)")


if __name__ == "__main__":
    main()
