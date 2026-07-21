# windows-agent-compat — 架构与实现机制

本文档说明技能的内部实现机制：调用流程、模块职责、翻译引擎原理、壳层抽象与编码处理。

## 一、整体架构

```
cli.py            ← 唯一入口（argparse 分发 5 个子命令）
  ├─ env_detect.py   环境探测
  ├─ cmd_adapter.py  命令翻译引擎（读 config/adapters.json）
  ├─ exec_runner.py  执行器（壳层选择 + 编码 + ANSI 处理）
  ├─ output_parser.py 错误模式识别
  ├─ prompt_gen.py   生成环境提示词
  └─ tool_wrap.py    与 shell 无关的安全文件操作
config/adapters.json  ← 翻译规则库（正则 → 模板）
assets/prompt-template.txt
```

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

## 六、wrap（安全包装，绕过 shell）

`wrap` 子命令不翻译、不调用 shell——直接用 Python 标准库做文件操作（`tool_wrap.py`）：`safe_rm/mkdir/copy/move/read/write/grep/find/env/path`。

- `write` 特别支持 `--from-file <src>`：多行内容经文件传入（CLI 参数传多行会被 PowerShell/bash 的引号规则吃掉——这是真实模拟才发现的坑）
- `grep` 用 `glob.glob(fstr, root_dir=os.getcwd())`，避开 Windows 上 `scandir('.')` 的权限错误

## 七、一句话总结

**翻译引擎 = 正则规则库命中 → 命名捕获组抽参 → 模板占位符替换 → 空捕获清理；未命中则路径归一化兜底。** 它把"Linux 习惯→Windows 行为"的映射做成**数据驱动**（加命令只需改 JSON，不动 Python），这正是该技能可维护的核心。
