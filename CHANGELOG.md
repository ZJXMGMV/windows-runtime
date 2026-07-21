# Changelog

All notable changes to this skill are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-07-21

### Added
- **Daemon mode (`serve` subcommand)** — a long-lived JSON-line process (one JSON
  object per line in each direction) that removes per-invocation Python startup
  cost when running many commands. Emits a ready banner
  (`{"ok": true, "ready": true, "actions": [...]}`), routes every request through
  the same `dispatch(action, params)` the CLI uses (so output is field-identical
  to one-shot subcommands), stays alive across bad requests
  (`{"ok": false, "error": ...}`), and shuts down gracefully on
  `{"action": "quit"}`.

- **`detect` TTL caching + `--force`** — `detect` probes many tool versions and is
  comparatively slow, so the result is cached for 300s (TTL) in the system temp
  dir (`%TEMP%\windows-runtime-detect.json`). Repeat calls are near-instant;
  `detect --force` (or `detect(force=True)`) bypasses the cache and re-probes.

- **`suggested_command` on suggestion-only recovery categories** — a deterministic
  corrected command attached to `path_not_found` (path normalized via `resolve`),
  `already_exists` (force flag added), and `directory_not_empty` (recursive
  remove). Never auto-executed — the agent decides whether to feed it back into
  `exec`.

- **Enhanced `safe_grep` parameters, wired through every entry point** —
  `safe_grep` gained keyword-only `case_sensitive`, `context`, `exclude_dir`, and
  `include`. A single `grep_from_args` bridge parses mixed positional + flag args
  (`--context N`, `--exclude-dir X`, `--include X`, `--case-sensitive`) so the
  options are reachable from the Python API, CLI `wrap grep`, and
  `dispatch("wrap")` alike.

- **Parser/path self-test harness** (`tests/test_parser_rules.py` +
  `parser_fixtures.json`): covers the two pure deterministic string layers �?  `path_resolver` notation mapping (cwd/home pinned for host-independence,
  guards the historic f-string-backslash bug class) and `output_parser`
  ok-gate + error classification. Wired into `run_all.py`. Meta-verified to
  catch both a broken path mapping and a disabled fatal-keyword gate.
  30 checks green; no new bugs surfaced (layers were already clean).

- **Command-translation self-test harness** (`tests/test_adapter_rules.py` +
  `adapter_fixtures.json`): data-driven over all 23 translation rules. Asserts
  routing (**anti-shadowing net** �?ordering-sensitive rules like `cat` vs
  `cat | grep`, `echo env` vs `echo literal`, `ls`/`ls simple`/`ls bare` keep
  matching the intended rule), exact per-shell output, and fallback behavior.
  Coverage gate blocks any new rule shipped without a fixture. Wired into
  `run_all.py`. Meta-verified: catches injected rule shadowing. 77 checks green.

### Fixed (surfaced by the adapter harness)
- `ls -la` (no path) produced a stray trailing empty-quote pair in the bash
  output (`ls -la ''`). `_render` now strips a trailing `''` �?anchored to
  end-of-string so it never touches an intentional mid-template empty arg
  (e.g. cmd `find /C /V ""` in the `wc -l` rule).

- **Recovery rule self-test harness** (`tests/`): `run_all.py` entry point +
  `test_recovery_rules.py` data-driven over `recovery_fixtures.json`. For every
  rule it asserts positive-match, **anti-interference** (positives must not trip
  undeclared categories), negative-non-match, and expected auto-recovery action.
  Coverage gate fails if any registered rule lacks fixtures. 92 checks green.

### Fixed (surfaced by the new harness)
- `path_not_found` missed the real Chinese wording `系统找不到指定的路径/文件`
  ("找不�? and "路径" are separated) and bash `No such file or directory`.
- `execution_policy_blocked` had no Chinese branch (`因为在此系统上禁止运行脚本`).
- `pip_not_found` failed to match bare `No module named pip` (pip token wasn't first).
- `tls_cert_error` missed `certificate verification failed` (only had verify/validation).
- `already_exists` missed bash `File exists`.
- `directory_not_empty` missed `Directory not empty` (no "is").

### Added
- **Recovery ruleset expanded 10 �?22 categories.** New: `execution_policy_blocked`
  (auto: process-scoped `Set-ExecutionPolicy Bypass`), `pip_not_found`
  (auto: `python -m pip`), `node_not_found`, `module_not_found`, `disk_full`,
  `network_unreachable`, `tls_cert_error`, `auth_failed`, `path_too_long`,
  `already_exists`, `directory_not_empty`, `argument_error`.
- Two new deterministic auto-recovery builders: `_try_pip_module`
  (bare `pip`/`pip3` �?`python -m pip`) and `_try_execution_policy_bypass`
  (process-scoped bypass only �?never touches machine/user policy).
- `_HIGH_SEVERITY` set drives severity classification
  (command_not_found / admin_required / disk_full / auth_failed /
  execution_policy_blocked / tls_cert_error �?high; rest �?medium).

### Fixed
- **Recovery false positive**: all-caps error codes (`ENOTFOUND`, `ENOSPC`,
  `EEXIST`, `ENOTEMPTY`, `MODULE_NOT_FOUND`) were substring-matching inside
  unrelated words �?e.g. `ModuleNotFoundError` matched `ENOTFOUND` (as the
  case-insensitive substring `eNotFound`) and wrongly tripped
  `network_unreachable`. Now anchored with `\b` word boundaries.
- **Auto-recovery determinism**: when multiple rules match, the *first* rule
  that produces an auto-recovery action now wins (no silent later override).

- **Error Recovery Engine** (`scripts/recovery.py`) �?deterministic,
  no-LLM recovery layer. Classifies a failed `exec` result into one of 10
  categories (`command_not_found`, `permission_denied`, `path_not_found`,
  `syntax_error`, `encoding_mojibake`, `file_in_use`, `python_not_found`,
  `git_not_available`, `timeout_or_hung`, `admin_required`), emits
  human-readable `fix_hint`s, and �?where safe �?a self-contained
  `auto_recovery` action the runner can retry once.
  - Auto-recovery builders: `where.exe` full-path re-resolution for missing
    commands, `cmd`+GBK re-decode for mojibake output, `python3` fallback.
  - Never guesses: if `where.exe` cannot resolve the tool, no action is
    produced (the failure surfaces honestly).
- **Retry / recovery closed loop** in `ExecRunner.run()` �?Stage 1 naive
  OS/timeout retries, Stage 2 deterministic recovery. `--recover` is on by
  default; `exec --no-recover` disables it. Recovery metadata is attached to
  the result (`recovery`, `recovered_via`).
- **Path Resolver** (`scripts/path_resolver.py` + `resolve` subcommand) �?  normalizes `~`, `.`/`..`, `//UNC`, `\\server\share`, `/mnt/c/...` (WSL),
  `C:/x//y` (mixed/duplicate separators), and `\\?\` long-path passthrough
  into a canonical absolute Windows path.
- **Tool Discovery** (`scripts/tool_discovery.py` + `discover` subcommand) �?  resolves a logical tool name (`git`/`python`/`node`/...) to the best
  concrete executable, preferring real `.exe` over `.cmd`/`.bat`/`.ps1`
  wrappers and excluding QClaw's `bash.cmd` false positive.
- `recover` subcommand �?analyze an `exec` result JSON and print recovery
  suggestions without executing anything.
- `capabilities` subcommand �?emits a declarative Capabilities Manifest for
  agent *planning*: which ops to route through `wrap` (native, no shell) vs
  `exec` (needs a shell), plus detected toolchain versions and platform facts.
- `env_detect` capability extension: detected versions for `python`, `node`,
  `npm`, `git`, `cargo`, `go`, `java`, `docker`; plus `wsl` (real distro
  installed?), `admin` (elevation), `network` (outbound probe).
- `scripts/capabilities.py` �?manifest builder (`build_manifest`) consumed by
  both `capabilities` and `detect`.
- SKILL.md section + Quick start entry for the `capabilities` manifest.

### Fixed
- `command_not_found` pattern now also matches PowerShell's wording
  ("is not recognized as a name of a cmdlet"), not only cmd.exe's phrasing.
- Path Resolver `re.sub` replacement escaping �?duplicate/mixed separators
  (`C:/x//y`, `relative/dir`) previously raised `bad escape`; fixed.

## [1.0.0] - 2026-07-21

### Added
- Initial release of the Windows Agent Compatibility layer.
- `detect` �?environment, shell availability (pwsh/powershell/cmd/bash), code
  page, and path-tool probing. `--json` for machine use.
- `translate` �?Bash-style command �?pwsh/cmd/bash syntax. Auto-selects the
  best available shell when `--shell` is omitted.
- `exec` �?translate + execute with encoding handling; returns a standard JSON
  result (`ok`, `stdout`, `stderr`, `exit_code`, `shell_used`,
  `translated_cmd`, `matched_rule`, `fallback`).
- `wrap` �?shell-independent safe file wrappers: `rm`, `mkdir`, `copy`, `move`,
  `read`, `write`, `grep`, `find`, `env`, `path`.
  - `write` supports `--from-file <src>` so multi-line content survives shell
    quoting (CLI args cannot carry newlines reliably).
- `prompt` �?renders an environment fragment (shell, encoding, path separator,
  available tools, bash intentionally omitted when only a WSL stub exists) to
  inject into a sub-agent's system prompt.
- Per-shell command registry and shell matrix references.
- ANSI color stripping via `$PSStyle.OutputRendering = 'PlainText'` in `exec`
  so PowerShell table/`Select-String` output stays machine-parseable.
- Encoding fallback chain `utf-8 �?gbk �?cp1252 �?utf-16` (UTF-16 last-resort)
  to avoid GBK mojibake on Chinese Windows.
- 71-assertion regression harness covering 8 scenarios (detect / translate /
  exec / prompt / wrap / output_parser / edge / structure).

### Fixed
- cmd templates used single quotes (invalid in `cmd.exe`) �?switched to double
  quotes; verified by real execution.
- `Select-String` emitted ANSI highlight codes �?added
  `| ForEach-Object { $_.Line }` to strip coloring.
- `safe_grep` iterated a `./glob` string character-by-character (caused
  `PermissionDenied` on `.`) �?normalize to list, use `glob.glob(root_dir=cwd)`.
- UTF-16 decode was attempted before GBK �?swapped order so GBK wins on
  Chinese Windows.
- `exec` produced ANSI-colored stdout on PowerShell �?disabled `OutputRendering`.
- `wrap write` could not receive multi-line content via CLI args �?added
  `--from-file`.

### Discovered via real task simulation (not unit tests)
- `wrap write` multi-line argument loss (all shells) �?`--from-file` fix.
- `exec ls` ANSI pollution �?`OutputRendering='PlainText'` fix.

[1.0.0]: https://github.com/ZJXMGMV/windows-runtime/releases/tag/v1.0.0
