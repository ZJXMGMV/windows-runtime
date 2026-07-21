# windows-runtime — 架构与实现机制

本文档说明技能的内部实现机制：调用流程、模块职责、翻译引擎原理、壳层抽象与编码处理。

## 一、整体架构

```
cli.py            ← 唯一入口（argparse 分发 10 个子命令；dispatch() 为 serve/编程调用共用路由）
  ├─ env_detect.py     环境探测（含版本/能力检测；300s TTL 文件缓存，--force 绕过）
  ├─ cmd_adapter.py    命令翻译引擎（读 config/adapters.json）
  ├─ exec_runner.py    执行器（壳层选择 + 编码 + ANSI + 恢复闭环）
  ├─ recovery.py       错误恢复引擎（分类 + 确定性自恢复）
  ├─ path_resolver.py  路径规范化（~/UNC//mnt/c/长路径）
  ├─ tool_discovery.py 工具发现（多候选优选 exe > cmd/bat/ps1）
  ├─ capabilities.py   能力清单（agent 规划用）
  ├─ output_parser.py  错误模式识别
  ├─ prompt_gen.py     生成环境提示词
  └─ tool_wrap.py      与 shell 无关的安全文件操作
config/adapters.json  ← 翻译规则库（正则 → 模板）
assets/prompt-template.txt
```

子命令：`detect` `translate` `exec` `wrap` `prompt` `capabilities` `resolve` `discover` `recover` `serve`

机制本质：**把"Linux 习惯 → Windows 正确行为"的映射做成一个可数据驱动的规则库（`adapters.json`），配一套壳层抽象（自动选最优 shell + 编码/ANSI 处理）和一套 shell 无关的安全文件操作（`wrap`），对外暴露 5 个统一子命令，所有执行结果收敛成结构化 JSON 供 agent 稳定解析。**

## 二、调用流程图

### 1. 总览（5 个子命令 → 模块归属）

```
                        cli.py (argparse 分发)
                              │
   ┌──────────┬──────────┬────┴────┬──────────┬──────────┐
   ▼          ▼          ▼         ▼          ▼          │
detect    translate    exec      wrap      prompt     (内部)
   │          │          │         │          │
   ▼          │          ▼         ▼          ▼
env_detect ───┘     exec_runner ─┤    prompt_gen ─┐
                  (翻译+执行)     │    (读模板)    │
                       │          │         │      │
                       ▼          ▼         ▼      ▼
                 cmd_adapter ──► tool_wrap  assets/   output_parser
                  (规则引擎)      (Python   prompt-    (错误识别)
                       │          IO)       template
                       ▼                     │
                 config/                     ▼
                 adapters.json          DEFAULT_TEMPLATE
```

### 2. `exec` 的详细管道（最核心链路）

```
agent 输入: exec "cat a | grep b" --json
   │
   ▼
[1] _default_shell()  ──► env_detect 探测 → 首选 shell (pwsh)
   │
   ▼
[2] cmd_adapter.translate(raw, shell)
   │     ├─ strip 首尾空白
   │     ├─ 遍历 rules 正则匹配 (re.match, IGNORECASE)
   │     │     ├─ 命中: 取 {{capture}} 填模板 → translated
   │     │     └─ 未命中: _normalize() 路径分隔符 → fallback=True
   │     └─ 返回 {translated, matched_rule, fallback}
   │
   ▼
[3] exec_runner.select_shell()  ──► 回退链 pwsh→powershell→cmd→bash
   │                              (bash 过滤 .CMD 包装器)
   ▼
[4] _execute(): 拼 PowerShell 前缀
   │     prefix = "chcp 65001 > $null;
   │              [Console]::OutputEncoding = UTF8;
   │              $OutputEncoding = UTF8;
   │              $PSStyle.OutputRendering = 'PlainText';"
   │     full = [pwsh.exe, -NoProfile, -Command, prefix + translated]
   │
   ▼
[5] subprocess.run(full, capture_output=True, encoding=None)  ← 拿原始字节
   │
   ▼
[6] _decode(stdout/stderr)  ──► utf-8 → gbk → cp1252 → utf-16
   │
   ▼
[7] 组装统一 JSON:
   { ok, stdout, stderr, exit_code, shell_used,
     translated_cmd, matched_rule, fallback }
   │
   ▼
[8] print(json.dumps(result, ensure_ascii=False))
```

### 3. `wrap` 的详细管道（绕过 shell）

```
agent 输入: wrap write ./f.md --from-file ./content.txt
   │
   ▼
cmd_wrap(args)  ──► 拦截 operation=="write"
   │     ├─ 解析 --from-file → 读源文件内容 (UTF-8)
   │     └─ 调 OPERATIONS["write"](target, content)
   │              │
   │              ▼
   │        tool_wrap.safe_write()
   │              └─ Path(target).parent.mkdir(parents=True)
   │                 .write_text(content, encoding='utf-8')
   │
   ▼
return 0 (无 shell 调用, 无翻译, 无编码坑)
```

> 关键点：**`wrap` 完全不经过 `cmd_adapter` 和 `exec_runner`**——它走 Python 标准库，从根上消灭 shell 转义问题。

## 三、翻译引擎深解（cmd_adapter.py + adapters.json）

### 数据模型

规则库是 `commands` 字典，每条规则 3 类字段：

```jsonc
"rm -rf": {
  "pattern": "^\\s*rm\\s+-rf\\s+(?P<path>\\S+)$",   // 入站正则
  "pwsh":   "Remove-Item -Path '{{path}}' ...",     // 目标壳层模板
  "cmd":    "rmdir /S /Q \"{{path}}\" 2>nul ...",
  "bash":   "rm -rf '{{path}}'",
  "description": "..."                              // 可选
}
```

- `pattern` 用**命名捕获组** `(?P<name>...)` 抽参数
- `pwsh`/`powershell`/`cmd`/`bash` 是壳层键；`powershell` 在代码里 alias 成 `pwsh`（`SHELL_ALIASES`）
- 命中后模板里的 `{{name}}` 被捕获值替换

### 翻译算法（translate 方法）

```
1. cmd.strip()
2. shell_key = _resolve_shell_key(shell)   # powershell→pwsh
3. for rule_name, rule in rules.items():
       m = re.match(rule["pattern"], cmd, IGNORECASE)
       if m:
           template = rule.get(shell_key) or rule.get("bash") or cmd
           translated = _render(template, m.groupdict())
           return {..., matched_rule=rule_name, fallback=False}
4. # 没命中 → 兜底
   return {translated=_normalize(cmd, shell), matched_rule=None, fallback=True}
```

注意三点：
- **`re.match` 不是 `re.search`**——正则必须从头匹配（`^...$` 锚定），避免中间片段误命中
- **壳层降级**：`rule.get(shell_key)` 取不到时回退 `rule.get("bash")`，再不行原样返回
- **`fallback=True` 是安全网**：未知命令不翻译，交给 shell 原样跑（由 exec_runner 的退出码上报成败）

### 模板渲染（_render）

```python
result = template
for key, value in groupdict.items():
    result = result.replace("{{"+key+"}}", value)
# 清理空捕获留下的 -Path ''
result = re.sub(r"\s+-Path\s+''(\s|$)", r"\1", result)
result = re.sub(r"\s+-Path\s+\"\"(\s|$)", r"\1", result)
result = re.sub(r"  +", " ", result)   # 挤压双空格
return result.strip()
```

这是为了处理 `ls -la` 这种无路径命令：捕获组为空 → `{{path}}` 变 `''` → 正则删掉 `-Path ''`，输出干净的 `Get-ChildItem -Force`。

### 兜底归一化（_normalize）

```python
if shell == "cmd":      return cmd.replace("/", "\\")   # cmd 不认 /
if shell in (pwsh,...): return cmd.replace("\\\\","\\") # 压双反斜杠
return cmd
```

只对**未命中规则**的命令做最小路径分隔符修正，不臆测语义。

### 规则库规模

当前 **23 条命令规则**，覆盖：`rm -rf/-f`、`mkdir -p`、`cp -r`、`mv`、`touch`、`cat`、`export`、`unset`、`echo $VAR`/`echo literal`、`chmod +x`、`ls`、`pwd`、`which`、`head`、`tail`、`wc -l`、`find -name`、`cat | grep`、`grep -i`。

## 四、壳层选择与回退链（exec_runner）

`select_shell()` 实现可用性探测 + 优先级回退：

```
pwsh(7+) → powershell(5.1) → cmd → bash
```

- 每个 shell 用 `shutil.which` 探测真实可执行文件
- **bash 特判**：过滤掉 QClaw 的 `bash.CMD` 包装器（只认真 `bash.exe`），且即使有 bash，也只是 WSL stub（没装发行版会如实报 `ok:false`）
- 用户可用 `--shell` 强制指定，但前提是探测到它真的存在

## 五、编码与 ANSI 处理（exec_runner）

- **PowerShell 前缀强制 UTF-8**：`chcp 65001 > $null`、`[Console]::OutputEncoding = UTF8`、`$OutputEncoding = UTF8`
- **去 ANSI 颜色**：`$PSStyle.OutputRendering = 'PlainText'`（否则 `ls`/`grep` 的彩色码污染 JSON 解析）
- **手动解码**：`subprocess.run(..., encoding=None)` 拿原始字节，再走回退链：

```
utf-8 → gbk → cp1252 → utf-16
```

**为什么 UTF-16 必须最后**：UTF-16 解码器"太宽容"，GBK 中文字节会被它成功"解码"成一堆乱码，所以只有前面三种都失败才试它（留给真正的 WSL UTF-16 输出）。

## 五点五、错误恢复闭环（recovery.py + exec_runner）

`exec` 现在是**两阶段管道**：

```
ExecRunner.run(cmd, recover=True)
   │
   ▼
[Stage 1] _run_with_naive_retries()
   │   OSError / TimeoutExpired → sleep(0.5) 重试 (retries 次)
   │   拿到 result（含 ok/stderr/original/fallback/matched_rule）
   ▼
[Stage 2] if not ok and recover: _try_recovery(result)
   │   RecoveryEngine.analyze(result)
   │     ├─ 遍历 22 条 recovery rules（正则匹配 stderr）
   │     │     → 生成 suggestions[{category,severity,fix_hint}]
   │     └─ auto_recovery builder（可选）→ 自恢复动作 or None
   │   多条命中时：首个产出 auto_recovery 的规则胜出（确定性）
   │   有 auto_recovery → _execute() 重跑一次 → 合并 recovered_via
   │   无 → 原样返回，附 result["recovery"]=analysis
   ▼
返回 result（失败时带 recovery 元数据）
```

**设计铁律：恢复动作必须确定性、窄口径，绝不 LLM 猜测。** 当前 **22 类**，5 类带自恢复：
- `command_not_found` → `_find_exe_and_retry`：仅当 `fallback=True` 时，用 `where.exe` 找真实路径重写命令；找不到就返回 None（如实失败）。
- `pip_not_found` → `_try_pip_module`：裸 `pip`/`pip3` → `python -m pip`。
- `execution_policy_blocked` → `_try_execution_policy_bypass`：加**进程级** `Set-ExecutionPolicy Bypass` 前缀（不改机器/用户策略）。
- `encoding_mojibake` → `_try_gbk_redecode`：检测 Latin-1 区 mojibake 字符 → 切 `cmd`+GBK 重跑。
- `python_not_found` → `_try_python_module_path`：`python` → `python3`。
- 其余 17 类（permission_denied / path_not_found / syntax_error / file_in_use / git_not_available / node_not_found / module_not_found / disk_full / network_unreachable / tls_cert_error / auth_failed / path_too_long / already_exists / directory_not_empty / argument_error / admin_required / timeout_or_hung）只给 `fix_hint` 建议。

**防子串误匹配**：全大写错误码（`ENOTFOUND`/`ENOSPC`/`EEXIST`/`ENOTEMPTY`/`MODULE_NOT_FOUND`）用 `\b` 词边界锁定，避免在 `ModuleNotFoundError` 等单词内部误命中（曾让 `ModuleNotFoundError` 误触 `network_unreachable`）。

`command_not_found` 正则同时匹配 cmd.exe（"not recognized as an internal or external command"）与 PowerShell（"is not recognized as a name of a cmdlet"）两种措辞。

## 五点六、路径解析（path_resolver.py）

`PathResolver.resolve()` 把任意路径记法归一成规范绝对 Windows 路径，按序处理：

```
\\?\ 长路径      → 原样透传
\\server\share  → UNC 反斜杠
//server/share  → UNC 正斜杠 → 反斜杠
/mnt/c/...       → C:\...（WSL 挂载）
~ / ~/x          → 展开 HOME
C:/x//y          → 折叠重复分隔符 + resolve()
relative/dir     → 相对 cwd → resolve()
```

坑点：`re.sub(r"[/\\]+", repl, s)` 的 replacement 里反斜杠需正确转义（`"\\\\"` = 单个字面反斜杠），否则触发 `bad escape`。

## 五点七、工具发现（tool_discovery.py）

`ToolDiscovery.resolve(tool)` 把逻辑工具名映射到最佳可执行文件：

- 候选表 `_TOOL_CANDIDATES`（如 `python → [python, python3, py]`）
- 扩展名优先级 `_EXT_PRIORITY = [.exe, '', .cmd, .bat, .ps1]`——**真 exe 优先于脚本包装器**
- 排除 QClaw 的 `bash.cmd`/`bash.bat` 假阳性（`_EXCLUDE_SUFFIXES`）
- 每个候选取第一个命中的扩展名即停

## 五点八、守护进程与共享分发器（serve / dispatch）

`serve` 子命令把 CLI 变成一个 **JSON-line 守护进程**，消灭每次调用的 Python
解释器启动开销。协议：每方向每行一个 JSON 对象。

```
agent 输入: serve
   │
   ▼
[1] 打印 ready 横幅:
   {"ok": true, "ready": true, "actions": [detect, translate, exec, wrap, ...]}
   │
   ▼
[2] for line in sys.stdin:           ← 阻塞读一行
       request = json.loads(line)
   │
   ▼
[3] dispatch(action, params)         ← 与一次性子命令共用的路由器
       ├─ detect    → {"ok":True,"env":{...}}
       ├─ translate → {translated, matched_rule, fallback, shell}
       ├─ wrap grep → {"ok":True,"matches":[...]}
       ├─ wrap 其它 → {"ok":True,"result":...}
       └─ ...
   │
   ▼
[4] print(json.dumps(result))        ← 一行回写；坏请求返回 {"ok":False,"error":...}
                                       进程不退出，继续读下一行
   │
   ▼
[5] {"action":"quit"} → {"ok":True,"bye":True} → 优雅退出
```

**关键点：`dispatch(action, params)` 是 serve 守护进程与编程调用的唯一共用
路由**——守护进程不重复实现任何子命令逻辑，只是把 stdin 的 JSON 请求转交给
`dispatch`，再把返回值序列化回 stdout。因此守护进程的输出与一次性 CLI 调用
逐字段一致。`wrap grep` 在 dispatch 里特判走 `grep_from_args`，增强参数
（`--context`/`--exclude-dir`/`--include`/`--case-sensitive`）由此可达。

### detect 缓存机制

`detect` 要探测大量工具版本，相对较慢，所以结果写入系统临时目录的
**TTL 文件缓存**（`%TEMP%\windows-runtime-detect.json`，TTL=300s）：

```
detect(force=False)
   ├─ 缓存存在 且 age < 300s → 直接返回缓存（近乎瞬时）
   └─ 否则 → 重新探测 → 写缓存 → 返回
detect(force=True) / detect --force → 跳过缓存读取，强制重新探测并刷新缓存
```

缓存只对 `detect` 生效；`exec`/`translate`/`wrap` 等无副作用查询不走缓存。

## 六、wrap（安全包装，绕过 shell）

`wrap` 子命令不翻译、不调用 shell——直接用 Python 标准库做文件操作（`tool_wrap.py`）：`safe_rm/mkdir/copy/move/read/write/grep/find/env/path`。

- `write` 特别支持 `--from-file <src>`：多行内容经文件传入（CLI 参数传多行会被 PowerShell/bash 的引号规则吃掉——这是真实模拟才发现的坑）
- `grep` 用 `glob.glob(fstr, root_dir=os.getcwd())`，避开 Windows 上 `scandir('.')` 的权限错误

## 七、一句话总结

**翻译引擎 = 正则规则库命中 → 命名捕获组抽参 → 模板占位符替换 → 空捕获清理；未命中则路径归一化兜底。** 它把"Linux 习惯→Windows 行为"的映射做成**数据驱动**（加命令只需改 JSON，不动 Python），这正是该技能可维护的核心。
