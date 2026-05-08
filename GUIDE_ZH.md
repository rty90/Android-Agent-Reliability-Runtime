# Android Agent Reliability Runtime 中文指南

这份指南面向想把项目跑起来、调试 Android GUI agent、以及复现长尾失败的人。
示例命令只使用占位符和环境变量，不包含任何开发者本机路径。

## 1. 项目定位

这个项目不再定位为“另一个全自动手机 Agent”，而是：

```text
Android Agent Reliability Runtime
```

它给 Android GUI agent 增加可靠性运行时：

- 判断屏幕是否 ready、loading、blocked、uncertain 或 complete
- 在 loading、blocked、uncertain 状态下阻止危险动作
- 把模型输出降级为 action proposal，而不是最终真理
- 每次执行后验证真实 UI 进展
- 失败时生成可读、可复现的诊断报告
- 用 chaos fixture 和 long-tail smoke 暴露边界情况

核心目标不是“让 agent 多点几下”，而是“知道什么时候应该停下来”。

## 2. 环境准备

建议使用 Python 3.10+。

```powershell
cd <repo-root>
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

准备 Android 侧环境：

- 安装 Android Studio
- 创建并启动一个 AVD 或连接真机
- 确认 `adb devices` 能看到设备

如果 `adb` 不在 `PATH` 里，可以通过环境变量指定：

```powershell
$env:ADB_PATH="<path-to-adb-executable>"
$env:DEVICE_ID="<adb-device-id>"
```

检查设备：

```powershell
& "$env:ADB_PATH" devices -l
```

如果这里看不到设备，问题还在 Android/ADB 层，不要急着调 agent。

## 3. 推理服务配置

本项目支持本地轻量 text reasoner、云端 reviewer、以及规则/策略兜底。
不要把 API key 写进代码、README 或提交记录，统一使用环境变量。

常用配置示例：

```powershell
$env:LOCAL_TEXT_REASONER_BASE_URL="http://127.0.0.1:9000/v1"
$env:LOCAL_TEXT_REASONER_MODEL="Qwen/Qwen3.5-0.8B"
$env:REASONING_REQUEST_TIMEOUT_SECONDS="30"
$env:REASONING_DISABLE_LOCAL_TEXT_AFTER_FAILURE="1"
$env:REASONING_ENABLE_LOCAL_VL="0"
```

如果使用云端 reviewer，请按自己的服务设置 key、base URL 和 model。

## 4. 常用 CLI

读取当前屏幕：

```powershell
python -m app.main --task "read the current screen and summarize it" --task-type read_current_screen --reasoner-backend stack --agent-mode interactive --max-steps 1 --auto-confirm
```

打开设置并检查当前页面：

```powershell
python -m app.main --task "open settings and inspect the current page" --task-type guided_ui_task --reasoner-backend stack --agent-mode interactive --max-steps 3 --auto-confirm
```

Coach 模式让人类操作、agent 给建议并记录状态：

```powershell
python -m app.main --task "open chrome and search for llm" --task-type guided_ui_task --agent-mode coach --reasoner-backend stack
```

## 5. Chaos Fixture

Chaos fixture 是一个专门给 ADB harness 使用的测试 APK，用来复现：

- 系统权限弹窗
- 阻塞弹窗
- onboarding overlay
- bottom sheet
- loading 状态
- error 状态
- 输入框和 IME/stylus overlay

先构建或下载 fixture APK，然后设置：

```powershell
$env:FIXTURE_APK="<path-to-chaos-fixture-apk>"
```

运行一个 dry-run case：

```powershell
python scripts\chaos_ui_harness.py --case fixture_input_surface --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
```

运行真实输入并验证的 E2E smoke：

```powershell
python scripts\chaos_ui_e2e_smoke.py --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
```

## 6. Long-Tail Smoke

长尾测试会混合 fixture、真实 Settings 页面、Chrome 搜索、复杂网页和 E2E 输入验证。

```powershell
python scripts\long_tail_agent_smoke.py --iterations 18 --seed 20260502 --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
```

Chrome torture profile 更适合暴露复杂网页、空白 WebView、JS challenge、captcha/cookie blocker 等问题：

```powershell
python scripts\long_tail_agent_smoke.py --iterations 8 --seed 20260506 --profile chrome_torture --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK" --skip-install
```

## 7. Capability Ladder

Capability ladder 用来测当前系统的稳定上限：

- `L1` readiness baseline
- `L2` blocker policy
- `L3` verified execution
- `L4` browser search surfaces and IME/stylus overlays
- `L5` complex mobile web pages
- `L6` mixed endurance

运行完整 ladder：

```powershell
python scripts\capability_ladder_smoke.py --level all --repeats 1 --seed 20260507 --endurance-iterations 8 --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK" --skip-install
```

只跑单层：

```powershell
python scripts\capability_ladder_smoke.py --level L4 --repeats 1 --seed 20260507 --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK" --skip-install
```

## 8. 查看报告

汇总最近的 chaos、E2E、long-tail、capability ladder 和 diagnostic 报告：

```powershell
python scripts\summarize_runs.py --latest 10
```

只看失败：

```powershell
python scripts\summarize_runs.py --latest 20 --failures-only
```

常见 artifact 位置：

```text
data/tmp/chaos/...
data/tmp/chaos_e2e/...
data/tmp/long_tail/...
data/tmp/capability_ladder/...
data/tmp/diagnostics/...
```

## 9. 维护原则

- 不要把一次性失败轨迹直接当成稳定 memory。
- 不要用 app-specific if/else 堆出“看起来能跑”的假泛化。
- 不要把 `tap executed` 当成 `task progressed`。
- 每个真实动作后都要验证 UI 是否发生了有意义变化。
- 失败时优先保留截图、XML、summary、decision 和 diagnostic。
- 新功能先写最小 smoke，再进入 long-tail。

## 10. 推荐日常流程

1. 启动设备并确认 `adb devices` 正常。
2. 设置 `ADB_PATH`、`DEVICE_ID`，需要 fixture 时设置 `FIXTURE_APK`。
3. 跑一个最小 read-screen smoke。
4. 跑目标相关的 chaos case。
5. 跑 capability ladder 单层。
6. 稳定后再跑 long-tail。
7. 用 `summarize_runs.py` 看报告，而不是只看控制台最后一行。
