#!/usr/bin/env python3
"""Safe cross-platform wrappers for common file/shell operations.

Use these instead of generating shell commands when the operation is fragile or
error-prone across different Windows shells.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable


def safe_rm(path: str | Path) -> None:
    """Remove a file or directory recursively.

    Raises OSError listing failed paths if any entries cannot be removed
    (e.g. permission denied, file in use).
    """
    target = Path(path)
    if not target.exists():
        return
    if target.is_dir():
        failures: list[str] = []

        def _on_error(_func, _path, exc_info):
            failures.append(f"{_path}: {exc_info[1]}")

        shutil.rmtree(target, onerror=_on_error)
        if failures:
            raise OSError(
                f"Failed to remove {len(failures)} item(s):\n" + "\n".join(failures)
            )
    else:
        target.unlink(missing_ok=True)


def safe_mkdir(path: str | Path) -> Path:
    """Create a directory and all parents."""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_copy(src: str | Path, dst: str | Path) -> None:
    """Copy a file or directory recursively."""
    source = Path(src)
    dest = Path(dst)
    if source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(source, dest)


def safe_move(src: str | Path, dst: str | Path) -> None:
    """Move a file or directory."""
    shutil.move(str(src), str(dst))


def safe_read(path: str | Path, encoding: str = "utf-8") -> str:
    """Read a text file robustly."""
    target = Path(path)
    try:
        return target.read_text(encoding=encoding, errors="replace")
    except UnicodeDecodeError:
        for enc in ("gbk", "cp1252"):
            try:
                return target.read_text(encoding=enc, errors="replace")
            except UnicodeDecodeError:
                continue
        raise


def safe_write(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """Write text to a file, creating parent directories as needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding, errors="replace")


def safe_append(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """Append text to a file, creating parent directories as needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding=encoding, errors="replace") as f:
        f.write(content)


def safe_grep(
    pattern: str,
    files: Iterable[str | Path],
    case_sensitive: bool = False,
    context: int = 0,
    exclude_dir: str | list[str] | None = None,
    include: str | list[str] | None = None,
) -> list[str]:
    """Grep-like search across files.

    ``files`` can be glob patterns (e.g. '*.py') or explicit paths.
    Patterns are expanded relative to cwd.

    Args:
        pattern: Regex pattern to search for.
        files: File paths or glob patterns to search.
        case_sensitive: If False (default), search is case-insensitive.
        context: Number of lines before/after each match to include (like grep -C).
        exclude_dir: Directory name(s) to skip during traversal (e.g. 'node_modules').
        include: File extension(s) to include (e.g. '.py' or 'py'). Only matching files are searched.
    """
    import glob as _glob

    if isinstance(files, (str, Path)):
        files = [str(files)]
    flags = 0 if case_sensitive else re.IGNORECASE
    regex = re.compile(pattern, flags)

    # Normalize exclude_dir to a set of lowercase names
    excluded_dirs: set[str] = set()
    if exclude_dir:
        if isinstance(exclude_dir, str):
            exclude_dir = [exclude_dir]
        excluded_dirs = {d.lower().strip("/\\").rstrip("/\\") for d in exclude_dir}

    # Normalize include to a set of lowercase extensions (with leading dot)
    include_exts: set[str] | None = None
    if include:
        if isinstance(include, str):
            include = [include]
        include_exts = set()
        for ext in include:
            ext = ext.lower().strip()
            if not ext.startswith("."):
                ext = "." + ext
            include_exts.add(ext)

    matches: list[str] = []
    expanded_files: list[str] = []
    for f in files:
        if not isinstance(f, (str, Path)):
            continue
        fstr = os.path.normpath(str(f))
        if any(ch in fstr for ch in '*?[]'):
            try:
                expanded_files.extend(
                    _glob.glob(fstr, recursive=True, root_dir=os.getcwd())
                )
            except (OSError, PermissionError):
                expanded_files.extend(_glob.glob(fstr, recursive=True))
        else:
            expanded_files.append(fstr)

    # Deduplicate while preserving order
    seen: set[str] = set()
    for fp in expanded_files:
        if fp in seen:
            continue
        seen.add(fp)

        # Skip excluded directories
        if excluded_dirs:
            parts = Path(fp).parts
            if any(p.lower() in excluded_dirs for p in parts):
                continue

        # Filter by extension
        if include_exts:
            if Path(fp).suffix.lower() not in include_exts:
                continue

        text = safe_read(fp)
        lines = text.splitlines()
        matched_indices: set[int] = set()
        for idx, line in enumerate(lines):
            if regex.search(line):
                matched_indices.add(idx)

        if not matched_indices:
            continue

        # Expand context around matches
        output_indices: set[int] = set()
        for idx in matched_indices:
            for offset in range(-context, context + 1):
                target = idx + offset
                if 0 <= target < len(lines):
                    output_indices.add(target)

        for idx in sorted(output_indices):
            separator = ":" if idx in matched_indices else "-"
            matches.append(f"{fp}:{idx + 1}{separator}{lines[idx]}")

    return matches


def safe_find(root: str | Path, pattern: str) -> list[str]:
    """Find files matching a glob pattern under a root."""
    root_path = Path(root)
    return [str(p) for p in root_path.rglob(pattern) if p.is_file()]


def safe_env(name: str, value: str | None = None) -> str | None:
    """Get or set an environment variable."""
    if value is None:
        return os.environ.get(name)
    os.environ[name] = value
    return value


def safe_path(path: str) -> str:
    """Return a normalized absolute path for the current OS."""
    return str(Path(path).expanduser().resolve())


OPERATIONS = {
    "rm": safe_rm,
    "mkdir": safe_mkdir,
    "copy": safe_copy,
    "move": safe_move,
    "read": safe_read,
    "write": safe_write,
    "append": safe_append,
    "grep": safe_grep,
    "find": safe_find,
    "env": safe_env,
    "path": safe_path,
}


def grep_from_args(args: list[str]) -> list[str]:
    """Parse mixed positional + flag args and run :func:`safe_grep`.

    The enhanced ``safe_grep`` parameters are keyword-only, but the ``wrap grep``
    CLI subcommand and the ``dispatch("wrap")`` path both pass arguments
    positionally. This bridge accepts the following syntax so those entry points
    can reach the full feature set:

    Positional:
        pattern   first positional argument (regex)
        files     remaining positional arguments (paths or globs; default ``*``)

    Flags:
        --context N        lines of context before/after each match (grep -C)
        --exclude-dir X    directory name to skip (repeatable)
        --include X        file extension to include, e.g. ``py``/``.py`` (repeatable)
        --case-sensitive   match case-sensitively (default is case-insensitive)
    """
    positional: list[str] = []
    context = 0
    exclude_dir: list[str] = []
    include: list[str] = []
    case_sensitive = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--context":
            context = int(args[i + 1])
            i += 2
        elif arg == "--exclude-dir":
            exclude_dir.append(args[i + 1])
            i += 2
        elif arg == "--include":
            include.append(args[i + 1])
            i += 2
        elif arg == "--case-sensitive":
            case_sensitive = True
            i += 1
        else:
            positional.append(arg)
            i += 1

    if not positional:
        raise ValueError("grep requires a pattern and at least one file/glob")

    pattern = positional[0]
    files = positional[1:] or ["*"]
    return safe_grep(
        pattern,
        files,
        case_sensitive=case_sensitive,
        context=context,
        exclude_dir=exclude_dir or None,
        include=include or None,
    )


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: tool_wrap.py <operation> [args...]")
        print("Operations: " + ", ".join(OPERATIONS.keys()))
        return 1

    op = sys.argv[1]
    if op not in OPERATIONS:
        print(f"Unknown operation: {op}")
        return 1

    if op in ("write", "append"):
        # Multi-line content cannot be passed reliably via CLI args (shell quoting
        # mangles newlines). Accept --from-file <path> to read content from a file.
        args = sys.argv[2:]
        content = ""
        target = None
        i = 0
        while i < len(args):
            if args[i] == "--from-file":
                content = Path(args[i + 1]).read_text(encoding="utf-8", errors="replace")
                i += 2
            else:
                if target is None:
                    target = args[i]
                i += 1
        if target is None:
            print(f"Error: {op} requires a target path", file=sys.stderr)
            return 1
        OPERATIONS[op](target, content)
        return 0

    if op == "grep":
        # Route through grep_from_args so the enhanced flags
        # (--context/--exclude-dir/--include/--case-sensitive) reach safe_grep.
        try:
            matches = grep_from_args(sys.argv[2:])
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        for line in matches:
            print(line)
        return 0

    func = OPERATIONS[op]
    try:
        result = func(*sys.argv[2:])  # type: ignore[arg-type]
        if result is not None:
            print(result)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
