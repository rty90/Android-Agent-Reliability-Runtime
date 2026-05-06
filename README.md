# Mobile-Agent- v0.3

## New Direction: Android Agent Reliability Runtime

This project is being repositioned from "another autonomous mobile agent" into
a reliability runtime and copilot for Android GUI agents.

Mobile GUI agents often fail silently: they tap while pages are still loading,
mistake blocked pages for success, repeat useless actions, store one-off
failures as memory, and leave no reproducible trace. This project adds the
safety, observability, and recovery layer around an Android execution kernel.

Runtime loop:

```text
Read screen
-> Normalize state
-> Classify readiness
-> Detect blocker / loop / risk
-> Propose action
-> Policy gate
-> Execute or ask human
-> Verify progress
-> Diagnose failure
-> Store trace
-> Promote reusable lessons only when validated
```

The goal is not to make agents act more. The goal is to know when they should
stop, wait, diagnose, or ask for human handoff.

`Mobile-Agent-` 是一个面向 `Android Studio Emulator + ADB` 的实用型 Android GUI Agent。

它不是“任意 App 任意操作”的全能代理，而是一个强调可维护、可测试、可调试的 emulator-first MVP。当前版本重点是：

- 保留 3 条稳定的有边界任务流
- 新增前台页面读取与半开放 UI 理解能力
- 继续坚持受限技能执行
- 保留日志、截图、SQLite memory 和确认机制

## 当前支持的任务类型

### 1. `send_message`

给记忆中的联系人发送模板消息，发送前会确认。

示例：

```bash
python -m app.main --task "send message to Dave Zhu \"hello from emulator\"" --task-type send_message
```

专用 demo：

```bash
python -m app.demo_runner --message-text "hello from emulator"
```

### 2. `extract_and_copy`

读取当前前台页面，抽取一个有边界的字段，再写入 Google Keep。

当前主要支持：

- `order_number`
- `check_in_time`

示例：

```bash
python -m app.main --task "extract the order number and copy it into notes" --task-type extract_and_copy
python -m app.main --task "extract the hotel check-in time and copy it into notes" --task-type extract_and_copy
```

专用 demo：

```bash
python -m app.extract_demo_runner --field order_number
python -m app.extract_demo_runner --field check_in_time
```

### 3. `create_reminder`

打开 Google Calendar 的提醒/事件编辑页，预填标题和可选时间，保存前确认。

示例：

```bash
python -m app.main --task "create a reminder for buy milk at 7pm" --task-type create_reminder
python -m app.main --task "create a reminder for call Zhang San tomorrow" --task-type create_reminder
```

### 4. `read_current_screen`

读取当前前台页面，并给出结构化页面理解结果。

这条 flow 不直接执行点击，重点是：

- 页面摘要
- 可见事实
- 结构化抽取

示例：

```bash
python -m app.main --task "read the current screen and summarize it" --task-type read_current_screen
python -m app.main --task "extract the visible order number from the current page" --task-type read_current_screen
```

### 5. `guided_ui_task`

打开指定 App，读取当前页面，并给出下一步受限动作建议；executor 最多只执行少量合法动作。

这是一条“半开放”任务流，不是任意自由代理。当前只允许落到受限 skill 集里。

示例：

```bash
python -m app.main --task "open keep and tell me what is on the current page" --task-type guided_ui_task
python -m app.main --task "open messages and inspect the current screen" --task-type guided_ui_task
```

## 当前架构

- [app/main.py](app/main.py)
  统一 CLI 入口和 runtime bootstrap
- [app/planner.py](app/planner.py)
  rule-based planner，保留可选 OpenAI planning
- [app/router.py](app/router.py)
  小型确定性路由：`execute / replan / confirm-first / unsupported-task`
- [app/executor.py](app/executor.py)
  执行 plan，记录步骤、截图、日志、memory，并支持 `guided_ui_task` 的受限循环
- [app/context_builder.py](app/context_builder.py)
  按 task type 裁剪上下文，不做大段历史硬塞
- [app/page_reader.py](app/page_reader.py)
  融合 UI tree 与截图补充占位
- [app/page_reasoner.py](app/page_reasoner.py)
  页面理解层，输出严格 JSON：
  - `page_type`
  - `summary`
  - `facts`
  - `targets`
  - `next_action`
  - `confidence`
  - `requires_confirmation`
- [app/memory.py](app/memory.py)
  SQLite memory
- [app/skills/](app/skills)
  原子技能集合
- [app/procedural_skills.py](app/procedural_skills.py)
  Hermes-style procedural skill layer. It maps normalized UI state into reusable, validated action proposals before model fallback. Keep this layer generic: blocker handling, focused text entry, and search-surface submission belong here; app-specific click scripts should not.

Guided UI reasoning order:

```text
read_screen -> ui_facts/ui_policy/ui_state -> procedural_skills -> memory/model fallback -> executor -> verifier/diagnostics
```

## 页面读取与推理策略

当前 v0.3 默认路线是：

- `UI 树优先`
- `截图补充占位`
- `受限动作执行`

也就是说，系统当前读取的是：

- 前台可见 UI 节点
- 当前截图路径和轻量截图上下文

它当前不承诺：

- 读取整个手机所有 App 的内部数据库
- 后台未渲染内容
- 任意 App 的完整历史

## 轻量模型接入策略

当前默认 `page reasoner` backend：

- `rule`
- `local`
- `openai`

推荐路线：

- 首版默认用 `rule`
- 如果你在电脑端部署了轻量本地模型，可以切到 `local`
- 本地模型推荐从 `Qwen3.5-2B` 开始

`local` backend 当前走 OpenAI-compatible 接口，便于后面接 `llama.cpp` 类服务。

相关环境变量：

```bash
set LOCAL_REASONER_BASE_URL=http://127.0.0.1:8000/v1
set LOCAL_REASONER_MODEL=Qwen3.5-2B-Instruct
set LOCAL_REASONER_API_KEY=local
```

OpenAI backend：

```bash
set OPENAI_API_KEY=...
set OPENAI_REASONER_MODEL=gpt-4.1-mini
```

说明：

- 当前本地/远程 reasoner 只负责页面理解和动作建议
- 不直接输出 adb 命令
- 不做在线改权重
- 不做自由工具调用

## 环境要求

- Python 3.8+
- Android Studio Emulator 正在运行
- `adb devices` 能看到模拟器
- Google Messages 可用于短信 flow
- Google Keep 可用于 `extract_and_copy`
- Google Calendar 可用于 `create_reminder`

基本检查：

```bash
adb devices
python -m unittest discover -s tests -v
```

### Chaos UI Regression Harness

The chaos harness is a deterministic ADB smoke/regression tool for testing UI blockers
such as permission dialogs, onboarding overlays, bottom sheets, loading states, error
states, and stylus/IME overlays.

Default fixture APK path:

```powershell
F:\virtualver\app\build\outputs\apk\debug\app-debug.apk
```

Common adb path on this machine:

```powershell
C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe
```

Run one dry-run decision case:

```powershell
python scripts\chaos_ui_harness.py --case fixture_input_surface --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
```

Recommended dry-run smoke cases:

```powershell
python scripts\chaos_ui_harness.py --case fixture_notification_permission --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
python scripts\chaos_ui_harness.py --case fixture_input_surface --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
python scripts\chaos_ui_harness.py --case fixture_loading_state --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
python scripts\chaos_ui_harness.py --case fixture_error_state --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
python scripts\chaos_ui_harness.py --case chrome_search_stylus_overlay --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe"
```

Run the minimal execute-and-verify E2E smoke:

```powershell
python scripts\chaos_ui_e2e_smoke.py --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
```

Artifacts are written under:

```powershell
data\tmp\chaos\...
data\tmp\chaos_e2e\...
```

### Long-Tail Agent Smoke

Use this when you want a longer mixed run that asks real-ish and randomized goals instead of only one deterministic case. It mixes chaos fixture blockers, real Settings read-only inspection, Chrome random search questions, and one execute-and-verify input E2E.

```powershell
python scripts\long_tail_agent_smoke.py --iterations 18 --seed 20260502 --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
```

Use the Chrome torture profile when you specifically want messy real web pages
that can expose blank WebView loads, JS challenges, cookie/captcha blockers,
and repeated-search mistakes:

```powershell
python scripts\long_tail_agent_smoke.py --iterations 8 --seed 20260506 --profile chrome_torture --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk" --skip-install
```

Artifacts are written under:

```powershell
data\tmp\long_tail\long_tail_<timestamp>_seed_<seed>\long_tail_report.json
```

The long-tail runner intentionally keeps screenshots, XML, summaries, decisions, and diagnostics for every round. It is meant to expose slow-start windows, transient `uiautomator dump` failures, stale app state, bad target-preparation assumptions, visually unloaded Chrome pages, and web-level blockers such as JS challenges.

### Failure Diagnostics

Agent and harness failures write a stable diagnostic JSON report using schema
`agent.diagnostic.v1`. The report is designed to be readable first and debuggable
second:

```json
{
  "schema_version": "agent.diagnostic.v1",
  "status": "fail",
  "kind": "adb_error | unhandled_exception | agent_result_failure | chaos_harness_failure | chaos_e2e_failure",
  "human_summary": "Short explanation for humans",
  "error": {"type": "...", "message": "...", "traceback": "..."},
  "device": {
    "requested_device": "emulator-5554",
    "connected": true,
    "current_focus": "...",
    "foreground_package": "...",
    "top_activity": "...",
    "crash_log_tail": "..."
  },
  "artifacts": {
    "diagnostic_report_path": "...",
    "screenshot_path": "...",
    "ui_dump_path": "...",
    "screen_summary_path": "..."
  }
}
```

Default diagnostic locations:

```powershell
data\tmp\diagnostics\...
data\tmp\chaos\...\diagnostics\diagnostic.json
data\tmp\chaos_e2e\...\diagnostics\diagnostic.json
```

`device.crash_log_tail` is the tail of `adb logcat -b crash`; it is useful for
emulator/app crash clues, but it may include earlier crashes if the buffer was
not cleared before the run.

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 常用 CLI 参数

```bash
python -m app.main --help
```

关键参数：

- `--task-type`
  指定任务类型：
  - `send_message`
  - `extract_and_copy`
  - `create_reminder`
  - `read_current_screen`
  - `guided_ui_task`
  - `unsupported`
- `--planner-backend`
  `rule` 或 `openai`
- `--reasoner-backend`
  `rule`、`local` 或 `openai`
- `--agent-mode`
  `bounded` 或 `interactive`
- `--max-steps`
  `guided_ui_task` 的最大交互轮数，默认 `3`
- `--dry-run`
  只输出 route + plan，不操作模拟器
- `--auto-confirm`
  非交互执行时自动跳过确认
- `--device-id`
  指定 ADB 设备序列号

## 输出位置

- 日志：`data/logs/agent.log`
- 截图：`data/screenshots/<task_name>/`
- SQLite memory：`data/memory.db`

memory 表：

- `user_preferences`
- `known_contacts`
- `successful_trajectories`
- `failure_patterns`

memory 使用原则：

- 只把 verified success 放进可信成功经验
- 不存 raw chain-of-thought
- 联系人检索与 task-aware success/failure retrieval 都已接入

## 安全行为

高风险动作仍然必须确认。

典型高风险场景：

- 付款 / 转账
- 删除类操作
- 正式对外消息
- 改别人日历

另外，部分稳定 flow 本身也会保留确认点：

- `send_message`：发送前确认
- `create_reminder`：保存前确认

## 测试

运行单测：

```bash
python -m unittest discover -s tests -v
```

可选语法检查：

```bash
python -m compileall app tests
```

当前覆盖方向包括：

- planner 对当前支持任务类型的输出
- router 的 supported / unsupported / high-risk / replan
- executor 的稳定 flow 与受限 interactive mode
- context builder 的 task-aware shaping
- page reasoner 的规则输出
- memory helper retrieval
- targeting / contact discovery

## 已知限制

- 当前仍然是 emulator-first，不是为真机细调的版本
- `read_current_screen` 和 `guided_ui_task` 当前读取的是“前台可见 UI”，不是整个手机内部数据
- 截图补充目前还是轻量占位，没有正式接 OCR 或视觉模型
- 某些 emulator 状态下，`uiautomator dump` 仍可能偶发失败；当前已加轻量重试，但还不是完全免疫
- `guided_ui_task` 现在是受限动作代理，不是任意 App 任意操作的通用代理
- `extract_and_copy` 目前默认写入 Google Keep
- `create_reminder` 目前主要围绕 Google Calendar 编辑页路径
- 不同 emulator 镜像的 UI 可能仍需微调 [app/demo_config.py](app/demo_config.py) 里的关键词和 fallback target

## 下一步路线

- 继续增强 `guided_ui_task` 的页面判断与恢复能力
- 后续再接电脑端本地小模型，优先做页面理解而不是全局重规划
- 后续再增强 richer memory 与页面策略记忆
- 后续再考虑 selective replan / looped LM
- 后续再考虑兼容更接近 OpenClaw / AgentSkills 的 skill layer
