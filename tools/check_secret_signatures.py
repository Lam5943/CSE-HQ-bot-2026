from __future__ import annotations

import re
import subprocess  # nosec B404
from pathlib import Path

# The scanner constrains subprocess use to the fixed Git executable without a shell.

SECRET_PATTERNS = (
    re.compile(rb"ghp_" + rb"[A-Za-z0-9]{20,}"),
    re.compile(rb"github_pat_" + rb"[A-Za-z0-9_]{20,}"),
    re.compile(rb"AIza" + rb"[0-9A-Za-z_-]{20,}"),
    re.compile(rb"sk-" + rb"[A-Za-z0-9_-]{20,}"),
    re.compile(rb"-----BEGIN " + rb"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _contains_secret(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in SECRET_PATTERNS)


def _git_output(*args: str) -> bytes:
    # Callers supply only the internal Git subcommands defined below.
    result = subprocess.run(  # nosec B603
        ("git", *args),
        check=True,
        capture_output=True,
    )
    return result.stdout


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    tracked_paths = _git_output("-C", str(repository_root), "ls-files", "-z")
    for raw_path in tracked_paths.split(b"\0"):
        if not raw_path:
            continue
        path = repository_root / raw_path.decode("utf-8", errors="surrogateescape")
        if path.is_file() and _contains_secret(path.read_bytes()):
            print("Secret-like credential signature found in a tracked file.")
            return 1

    history = _git_output(
        "-C",
        str(repository_root),
        "log",
        "-p",
        "--all",
        "--no-ext-diff",
        "--no-textconv",
        "--",
        ".",
    )
    if _contains_secret(history):
        print("Secret-like credential signature found in Git history.")
        return 1

    print("Secret signature scan passed (tracked files and Git history).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
