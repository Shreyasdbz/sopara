from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "src" / "sopara" / "domain"
FORBIDDEN_ROOTS = {
    "alembic",
    "fastapi",
    "google",
    "http",
    "os",
    "pathlib",
    "psycopg",
    "random",
    "socket",
    "sqlalchemy",
    "sopara.adapters",
    "sopara.api",
    "sopara.application",
    "sopara.jobs",
    "sopara.settings",
    "subprocess",
    "time",
    "urllib",
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_does_not_import_frameworks_or_outer_layers() -> None:
    violations = [
        f"{path.relative_to(ROOT)} imports {module}"
        for path in sorted(DOMAIN.rglob("*.py"))
        for module in imported_modules(path)
        if any(module == root or module.startswith(f"{root}.") for root in FORBIDDEN_ROOTS)
    ]
    assert violations == []


def test_required_architecture_directories_exist() -> None:
    required = [
        ROOT / "contracts" / "events",
        ROOT / "contracts" / "openapi",
        ROOT / "contracts" / "reason-codes",
        ROOT / "fixtures" / "contracts",
        ROOT / "fixtures" / "failures",
        ROOT / "fixtures" / "market-data",
        ROOT / "fixtures" / "replay",
        ROOT / "infra" / "terraform",
        ROOT / "migrations" / "versions",
        ROOT / "web",
    ]
    assert [str(path.relative_to(ROOT)) for path in required if not path.is_dir()] == []
