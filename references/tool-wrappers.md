# Tool Wrappers

This skill provides Python-based safe wrappers for common file and shell operations. Use them instead of generating raw shell commands when possible, especially on Windows where shell differences are large.

## Operations

| Function | Usage | Notes |
|----------|-------|-------|
| `safe_rm(path)` | Remove file or directory recursively | Uses `shutil.rmtree(..., ignore_errors=True)` |
| `safe_mkdir(path)` | Create directory recursively | Returns `Path` object |
| `safe_copy(src, dst)` | Copy file or directory recursively | Uses `shutil.copy2` / `shutil.copytree` |
| `safe_move(src, dst)` | Move file or directory | |
| `safe_read(path)` | Read text file | Tries UTF-8, then GBK, then cp1252 |
| `safe_write(path, content)` | Write text file | Creates parent directories |
| `safe_grep(pattern, files, *, case_sensitive=False, context=0, exclude_dir=None, include=None)` | Search regex across files | Returns `file:line:match` strings; see [grep options](#grep-options) |
| `safe_find(root, pattern)` | Find files matching glob | Uses `pathlib.Path.rglob` |
| `safe_env(name, value=None)` | Get or set environment variable | |
| `safe_path(path)` | Normalize and return absolute path | |

## CLI usage

```powershell
python scripts/tool_wrap.py rm ./temp
python scripts/tool_wrap.py mkdir ./build/output
python scripts/tool_wrap.py read ./file.txt
python scripts/tool_wrap.py grep "class.*Agent" ./scripts/*.py
python scripts/tool_wrap.py find . "*.py"
```

### grep options

`safe_grep` accepts four optional keyword-only parameters:

| Parameter | Type | Effect |
|-----------|------|--------|
| `case_sensitive` | `bool` | Match case exactly (default: case-insensitive) |
| `context` | `int` | Emit N lines of context around each match |
| `exclude_dir` | `list[str]` | Skip any file whose path contains one of these dir names (e.g. `node_modules`) |
| `include` | `list[str]` | Only search files whose name ends with one of these extensions (e.g. `.py`) |

Python API (keyword-only):

```python
from tool_wrap import safe_grep

safe_grep("TODO", ["**/*"], context=2, exclude_dir=["node_modules", ".git"], include=[".py", ".md"])
```

The same options are reachable from the CLI and the `dispatch("wrap")` entry
point via flags parsed by `grep_from_args`. Note the CLI flag spelling differs
from the Python kwarg names:

```powershell
python scripts/cli.py wrap grep "TODO" "**/*" --context 2 --exclude-dir node_modules --exclude-dir .git --include .py --include .md --case-sensitive
```

Positional args come first (pattern, then one or more file globs; default `*`),
flags may follow in any order. `--exclude-dir` and `--include` are repeatable.
With `context > 0`, output uses grep-style separators: `:` after a matching
line number and `-` after a context line number.

## Why use wrappers

- Avoid shell quoting and escaping errors.
- Avoid path separator mismatches (`/` vs `\\`).
- Avoid encoding issues when reading/writing files.
- Avoid permissions/file-lock problems with Python's `ignore_errors` options.
