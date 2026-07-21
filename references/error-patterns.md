# Common Error Patterns

Common errors when agents run Linux/Bash-style commands on Windows and how this skill handles them.

## 1. Permission denied / 访问被拒绝

**Cause:** Windows locks files that are open in VS Code, Chrome, Explorer, etc.

**Handling:**
- `ExecRunner` retries the command up to the configured retry count.
- `safe_rm` uses Python `shutil.rmtree(..., ignore_errors=True)` which is more tolerant than `rm -rf`.

## 2. Command not found / 不是内部或外部命令

**Cause:** Agent generated a Bash command on native Windows.

**Handling:**
- `CommandTranslator` maps known Bash commands to PowerShell/cmd equivalents.
- Unknown commands fall back to path/quote normalization.

## 3. 找不到路径 / 找不到文件

**Cause:** Forward slashes, missing drive letters, or WSL paths (`/mnt/c/...`).

**Handling:**
- Path normalization converts `/` to `\\` for cmd and preserves valid separators for pwsh.
- Tool wrappers use `pathlib.Path` to resolve absolute paths.

## 4. 命令语法不正确

**Cause:** Agent mixed shell syntax (e.g., `set` in PowerShell or `export` in cmd).

**Handling:**
- Translate `export` to `$env:` in PowerShell and `set` in cmd.
- Translate `echo $PATH` to `Write-Output $env:PATH` in PowerShell.

## 5. PowerShell exit code 0 but stderr warnings

**Cause:** PowerShell writes non-fatal warnings to stderr.

**Handling:**
- `OutputParser` treats exit code 0 as OK unless stderr contains fatal keywords (`error`, `exception`, `terminated`, `失败`).

## 6. PowerShell object output instead of string

**Cause:** `Get-Content` returns an array of objects.

**Handling:**
- `OutputParser._stringify` joins arrays into strings.
- `ExecRunner` runs with `-Command` and explicitly returns strings where possible.

## 7. chmod +x has no effect

**Cause:** Windows has no executable bit.

**Handling:**
- `chmod +x` is translated to a no-op comment in PowerShell/cmd.
- Use `safe_rm` / `safe_mkdir` instead of shell commands for file operations.

## 8. Long path errors (>260 chars)

**Cause:** Classic Windows path length limit.

**Handling:**
- `safe_*` wrappers use `pathlib` which supports long paths when the system enables it.
- `EnvDetect` reports `LongPathsEnabled` registry value.

## 9. UTF-8 / GBK encoding issues

**Cause:** cmd/PowerShell/Python may use different encodings.

**Handling:**
- `ExecRunner` captures raw bytes and decodes with fallback chain: UTF-8 → GBK → cp1252.
- `safe_read` / `safe_write` default to UTF-8 with fallback on read.

## 10. Recovery Engine category map (`recovery.py`)

After a failed `exec`, `RecoveryEngine.analyze()` classifies stderr into one of
**22 categories** and returns `suggestions` + an optional `auto_recovery` action.
Auto-recovery is deterministic (no LLM); when several rules match, the first rule
that yields an auto-recovery action wins.

| Category | Auto-recovery | Suggested cmd | Action |
|----------|:---:|:---:|--------|
| `command_not_found` | ✓ | | `where.exe` resolves full path (only if `fallback=True`; else no action) |
| `pip_not_found` | ✓ | | bare `pip`/`pip3` → `python -m pip` |
| `execution_policy_blocked` | ✓ | | process-scoped `Set-ExecutionPolicy Bypass` prefix |
| `encoding_mojibake` | ✓ | | re-run via `cmd`+GBK codepage |
| `python_not_found` | ✓ | | `python` → `python3` |
| `permission_denied` | | | suggest: elevate / close locking process / `icacls` |
| `path_not_found` | | ✓ | suggest: `Test-Path`/`dir` before retry |
| `syntax_error` | | | suggest: quote paths |
| `file_in_use` | | | suggest: close Chrome/VSCode/etc. |
| `git_not_available` | | | suggest: use `wrap` file ops |
| `node_not_found` | | | suggest: verify `where.exe node` |
| `module_not_found` | | | suggest: `pip install` / `npm install` |
| `disk_full` | | | suggest: free space / other drive |
| `network_unreachable` | | | suggest: check connectivity/proxy |
| `tls_cert_error` | | | suggest: check clock/CA store (never blindly disable verify) |
| `auth_failed` | | | suggest: refresh credentials/token |
| `path_too_long` | | | suggest: long paths / `\\?\` prefix / shorten |
| `already_exists` | | ✓ | suggest: force flag or remove first |
| `directory_not_empty` | | ✓ | suggest: recursive `wrap rm` |
| `argument_error` | | | suggest: check flag compatibility |
| `admin_required` | | | suggest: elevated terminal / `-Verb RunAs` |
| `timeout_or_hung` | | | suggest: reduce input / check interactive prompt |

**Substring-safety:** all-caps error codes (`ENOTFOUND`, `ENOSPC`, `EEXIST`,
`ENOTEMPTY`, `MODULE_NOT_FOUND`) are anchored with `\b` word boundaries so they
do not match inside unrelated words (e.g. `ModuleNotFoundError`).

### `suggested_command` field

Some suggestion-only categories also attach a deterministic `suggested_command`
to the suggestion — a corrected command ready to feed straight back into `exec`.
Unlike `auto_recovery`, it is **never auto-executed**: the recovery engine stays
deterministic and the agent decides whether to run it.

Currently produced for:

- `path_not_found` — the offending path is normalized via `resolve` (WSL/UNC/
  mixed-separator notations → canonical Windows path).
- `already_exists` — a force flag is added to the original command.
- `directory_not_empty` — rewritten as a recursive `wrap rm`.

Example suggestion shape:

```json
{
  "category": "path_not_found",
  "severity": "medium",
  "fix_hint": "Verify the path exists before retrying (Test-Path / dir).",
  "suggested_command": "Get-ChildItem -Path 'C:\\Users\\me\\foo'"
}
```
