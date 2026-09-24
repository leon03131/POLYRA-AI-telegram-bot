#!/usr/bin/env python3
"""Preview or install POLYRA OpenCode profiles; never replace existing files.

Python 3.10+. Standard library only. No network calls, subprocesses or model calls.
This installs instructions only; it neither launches nor validates OpenCode agents.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

NAMES = (
    "polyra-telegram",
    "polyra-miniapp",
    "polyra-gemini",
    "polyra-alibaba",
    "polyra-context",
    "polyra-search-security",
    "polyra-db-api",
    "polyra-qa-release",
)


def plan(project_arg: str) -> tuple[Path, list[tuple[Path, bytes]], int]:
    root = Path(project_arg).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project root is not a directory.")
    if not (root / ".git").exists() and not (
        (root / "app").is_dir() and (root / "pyproject.toml").is_file()
    ):
        raise ValueError("Expected an existing repository or POLYRA app/pyproject.toml.")
    source = Path(__file__).resolve().parent / "opencode-overlay" / ".opencode" / "agents"
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Bundled agent profiles are missing or symlinked.")
    for parent in (root / ".opencode", root / ".opencode" / "agents"):
        if parent.is_symlink():
            raise ValueError(f"Refusing a symlinked destination directory: {parent}")
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"Destination parent is not a directory: {parent}")

    pending: list[tuple[Path, bytes]] = []
    unchanged = 0
    for name in NAMES:
        src = source / f"{name}.md"
        dst = root / ".opencode" / "agents" / f"{name}.md"
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"Missing or unsafe source: {src.name}")
        content = src.read_bytes()
        if not content.startswith(b"---\n") or b"mode: subagent\n" not in content:
            raise ValueError(f"Invalid bundled profile: {src.name}")
        if dst.is_symlink():
            raise ValueError(f"Refusing symlinked destination file: {dst}")
        if dst.exists():
            if not dst.is_file() or dst.read_bytes() != content:
                raise ValueError(
                    f"Conflict: {dst}\nNothing overwritten. Review/merge manually."
                )
            unchanged += 1
        else:
            pending.append((dst, content))
    return root, pending, unchanged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="Existing project directory.")
    parser.add_argument("--apply", action="store_true", help="Create missing profiles.")
    args = parser.parse_args()
    created: list[Path] = []
    try:
        root, pending, unchanged = plan(args.project_root)
        print(f"Project: {root}")
        print(f"New profiles: {len(pending)}; identical profiles: {unchanged}")
        for dst, _ in pending:
            print(f"  {dst.relative_to(root).as_posix()}")
        if not args.apply:
            print("PREVIEW ONLY. No files or directories created. Use --apply to install.")
            return 0
        if pending:
            (root / ".opencode" / "agents").mkdir(parents=True, exist_ok=True)
        for dst, content in pending:
            # Exclusive creation also protects files added after the preview check.
            with dst.open("xb") as out:
                created.append(dst)
                out.write(content)
        print(f"Installed {len(created)} profile(s). No existing files overwritten.")
        print("No provider/model/permissions/AGENTS.md/production configuration changed.")
        print("Check discovery and perform real child calls in your OpenCode session.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if created:
            print(
                "A partial install occurred; these new files remain:\n"
                + "\n".join(str(p) for p in created),
                file=sys.stderr,
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
