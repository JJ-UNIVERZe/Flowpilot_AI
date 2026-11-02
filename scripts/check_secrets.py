"""Scan tracked/untracked project text and Git history for likely secrets.

The scanner reports only filenames, line numbers, or commit IDs; matching
secret text is never printed. It deliberately skips environment files,
virtual environments, databases, and generated binary artifacts.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".cfg", ".ini", ".json", ".md", ".py", ".sh", ".toml", ".ts",
    ".txt", ".yaml", ".yml",
}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(
        r"""(?i)\b(?:api[_-]?key|password)\b\s*[:=]\s*"""
        r"""(['"])(?!your_|placeholder|example|change_me|<)"""
        r"""[A-Za-z0-9_./+=-]{16,}\1"""
    ),
)


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        capture_output=True,
        check=False,
        text=True,
    )


def _is_scannable(path: str) -> bool:
    candidate = Path(path)
    if candidate.name == ".env" or candidate.name.startswith(".env."):
        return False
    if any(part in {".git", ".venv", "venv", "__pycache__", "site-packages"}
           for part in candidate.parts):
        return False
    return candidate.suffix.lower() in TEXT_SUFFIXES


def _matches(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def main() -> int:
    files = _git("ls-files", "-co", "--exclude-standard", "-z")
    if files.returncode:
        print("Secret scan could not list project files; run inside a Git repository.")
        return 2

    findings: list[str] = []
    for relative in files.stdout.split("\0"):
        if not relative or not _is_scannable(relative):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        findings.extend(
            f"working tree: {relative}:{line_number}"
            for line_number, line in enumerate(lines, start=1)
            if _matches(line)
        )

    commits = _git("rev-list", "--all")
    if commits.returncode:
        print("Secret scan could not inspect Git history.")
        return 2

    for commit in commits.stdout.splitlines():
        patch = _git("show", "--format=", "--no-ext-diff", commit)
        if patch.returncode:
            print(f"Secret scan could not inspect commit {commit}.")
            return 2
        if _matches(patch.stdout):
            findings.append(f"Git history: potential secret in commit {commit}")

    if findings:
        print("Potential secret patterns found (matching values suppressed):")
        for finding in findings:
            print(f" - {finding}")
        return 1

    print("Secret scan passed: no likely secret patterns found.")
    print("Environment files and binary/generated database files were not read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
