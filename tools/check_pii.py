#!/usr/bin/env python3
"""Reject real device and account data before it reaches the repository.

This project learns identifiers while it is being built — IEEE addresses, serial numbers,
account, company, location and device identifiers. None of them may be committed. This check
runs in CI and fails the build on the first real value it finds.

Placeholders are allowed through a small built-in list plus an optional
``tools/pii_allowlist.txt`` with one exact string per line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST_FILE = Path(__file__).resolve().parent / "pii_allowlist.txt"

SKIP_DIRS = {".git", "__pycache__", ".ruff_cache", ".mypy_cache", "node_modules", "build"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".whl"}

# Obvious placeholders that are fine to ship.
BUILTIN_ALLOW = {
    "00:00:00:00:00:00",
    "11:22:33:44:55:66",
    "aa:bb:cc:dd:ee:ff",
    "ff:ff:ff:ff:ff:ff",
    "de:ad:be:ef:00:01",
    "0000000000000000",
    "0123456789abcdef",
    "0123456789ABCDEF",
    "0011223344556677",
}

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # A MAC with exactly six octets: the lookarounds keep it from matching part of a
    # longer colon-separated sequence such as an eight-octet IEEE address.
    ("MAC/IEEE address", re.compile(
        r"(?<![0-9a-fA-F:])(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}(?![0-9a-fA-F:])")),
    ("16-hex serial number", re.compile(r"\b[0-9a-fA-F]{16}\b")),
    ("UUID", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("token assignment", re.compile(
        r"\b(?:access_token|refresh_token|id_token)\b\s*[:=]\s*[\"'][^\"']{12,}[\"']")),
]

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".cfg", ".ini", ".sh",
    ".c", ".h", ".csv", ".toml", ".example", "", ".jsonc",
}


def ignored_dirs(rel: Path) -> bool:
    return bool(SKIP_DIRS.intersection(rel.parts))


def load_allowlist() -> set[str]:
    allow = set(BUILTIN_ALLOW)
    if ALLOWLIST_FILE.exists():
        for line in ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                allow.add(line)
    return allow


def main() -> int:
    allow = {value.lower() for value in load_allowlist()}
    findings: list[str] = []
    scanned = 0

    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if ignored_dirs(rel) or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group(0)
                    if value.lower() in allow:
                        continue
                    if label == "MAC/IEEE address" and len(set(value.lower().split(":"))) == 1:
                        continue
                    findings.append(f"{rel}:{lineno}: {label}: {value}")

    if findings:
        print("Real-world identifiers or secrets found — refusing to continue:\n")
        for item in findings:
            print(f"  {item}")
        print(f"\n{len(findings)} finding(s). Replace with a placeholder, or add an exact"
              " placeholder value to tools/pii_allowlist.txt if it is intentional.")
        return 1

    print(f"OK — {scanned} files scanned, no real identifiers or secrets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
