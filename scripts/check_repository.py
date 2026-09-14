from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    violations: list[str] = []

    nested_git = sorted(path for path in ROOT.rglob(".git") if path != ROOT / ".git")
    violations.extend(f"nested Git repository: {path.relative_to(ROOT)}" for path in nested_git)

    tracked_candidates = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and "node_modules" not in path.parts
        and ".venv" not in path.parts
        and ".output" not in path.parts
    ]
    secret_pattern = re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
        r"AIza[0-9A-Za-z_-]{35}|"
        r"(?:password|secret|token)\s*=\s*['\"][^'\"]{8,}",
        re.IGNORECASE,
    )
    for path in tracked_candidates:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if secret_pattern.search(content):
            violations.append(f"possible credential material: {path.relative_to(ROOT)}")
        if path.suffix in {".py", ".ts", ".tsx", ".tf", ".yaml", ".yml"}:
            for number, line in enumerate(content.splitlines(), start=1):
                if line.rstrip() != line:
                    violations.append(f"trailing whitespace: {path.relative_to(ROOT)}:{number}")

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    final_stage = dockerfile.rsplit("\nFROM ", maxsplit=1)[-1]
    if re.search(r"\b(?:node|bun|bunx|npm|pnpm|yarn)\b", final_stage, re.IGNORECASE):
        violations.append("JavaScript runtime reference in final Docker stage")

    terraform = list((ROOT / "infra" / "terraform").glob("*.tf"))
    terraform_text = "\n".join(path.read_text(encoding="utf-8") for path in terraform)
    if re.search(r'^\s*(resource|data|provider|backend)\s+"', terraform_text, re.MULTILINE):
        violations.append(
            "Work Package 0 Terraform must not define resources, data, providers, or backends"
        )

    if violations:
        raise SystemExit("\n".join(violations))
    print("repository foundation checks passed")


if __name__ == "__main__":
    main()
