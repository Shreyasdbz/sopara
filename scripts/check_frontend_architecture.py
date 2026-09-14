from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
SOURCE = WEB / "src"

FORBIDDEN_SOURCE_TOKENS = {
    "createServerFn": "TanStack server functions",
    "createServerFileRoute": "TanStack server routes",
    "@tanstack/react-start/server": "TanStack server runtime imports",
    "@tanstack/start-server": "TanStack server runtime imports",
    "@radix-ui/": "Radix component primitives",
    "react-aria": "React Aria component primitives",
}
FORBIDDEN_PACKAGES = {
    "@prisma/client",
    "@radix-ui/react-dialog",
    "@tanstack/react-router-ssr-query",
    "drizzle-orm",
    "pg",
    "postgres",
    "react-aria-components",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> None:
    package = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    dependencies = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    forbidden = sorted(dependencies & FORBIDDEN_PACKAGES)
    if forbidden:
        fail(f"forbidden frontend packages: {', '.join(forbidden)}")

    if package.get("packageManager") != "bun@1.4.2":
        fail("packageManager must be bun@1.4.2")

    components = json.loads((WEB / "components.json").read_text(encoding="utf-8"))
    if not str(components.get("style", "")).startswith("base-"):
        fail("components.json must resolve a Base UI style")
    if components.get("iconLibrary") != "hugeicons":
        fail("components.json must use Hugeicons")

    violations: list[str] = []
    for path in sorted(SOURCE.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        content = path.read_text(encoding="utf-8")
        for token, label in FORBIDDEN_SOURCE_TOKENS.items():
            if token in content:
                violations.append(f"{path.relative_to(ROOT)}: {label} ({token})")
    if violations:
        fail("\n".join(violations))

    vite = (WEB / "vite.config.ts").read_text(encoding="utf-8")
    if "spa:" not in vite or "enabled: true" not in vite:
        fail("TanStack Start SPA mode is not enabled")

    lock = (WEB / "bun.lock").read_text(encoding="utf-8")
    if '"react": ["react@19.2.7"' not in lock:
        fail("React 19.2.7 is not locked")
    if '"react-dom": ["react-dom@19.2.7"' not in lock:
        fail("React DOM 19.2.7 is not locked")

    print("frontend architecture checks passed")


if __name__ == "__main__":
    main()
