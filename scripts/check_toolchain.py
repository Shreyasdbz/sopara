from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def output(command: list[str]) -> str:
    # Paths come only from explicit Make variables controlled by the repository operator.
    return subprocess.run(  # noqa: S603
        command, check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--bun", default="bun")
    parser.add_argument("--terraform", default="terraform")
    args = parser.parse_args()

    expected = {
        "uv": (ROOT / ".uv-version").read_text(encoding="utf-8").strip(),
        "bun": (ROOT / ".bun-version").read_text(encoding="utf-8").strip(),
        "terraform": (ROOT / ".terraform-version").read_text(encoding="utf-8").strip(),
        "python": (ROOT / ".python-version").read_text(encoding="utf-8").strip(),
    }
    actual = {
        "uv": output([args.uv, "--version"]).removeprefix("uv ").split()[0],
        "bun": output([args.bun, "--version"]),
        "terraform": json.loads(output([args.terraform, "version", "-json"]))["terraform_version"],
        "python": output([args.uv, "run", "--locked", "python", "--version"]).removeprefix(
            "Python "
        ),
    }
    mismatches = {
        tool: {"expected": expected[tool], "actual": actual[tool]}
        for tool in expected
        if actual[tool] != expected[tool]
    }
    if mismatches:
        raise SystemExit(json.dumps(mismatches, indent=2, sort_keys=True))

    print(json.dumps(actual, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
