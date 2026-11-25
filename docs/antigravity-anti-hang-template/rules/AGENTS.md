# Google Antigravity 编辑器专属规则：代理基线、高阈值自愈与超大型任务协同

> 适用范围：仅针对 **Google Antigravity** 编辑器/Agent。部署时请将本目录（`plugin.json` 与 `rules/AGENTS.md`）复制到 `~/.gemini/config/plugins/antigravity-anti-hang/`（Windows 为 `%USERPROFILE%\.gemini\config\plugins\antigravity-anti-hang\`），并把第 3 节中的代理端口与日志路径替换为本机实测值。

## 1. AI 自主评估超大型任务，不把判断甩给用户
- 普通与中等任务直接拆解执行，不打扰用户。
- 仅当遇到**超大型/高风险任务**（如同时包含 ≥2 个独立重交付物、大范围跨模块重构、批量写入 ≥15 条结构化记录，或当前对话上下文已过长）时，**动手前主动简明询问用户偏好**：
  1. 在当前对话按阶段分步落地；
  2. 新开干净对话接力（本地落盘交接文件）；
  3. 启用多 Agent 协同（`subagents` / `swarm` / `/teamwork`）并行分片完成。

## 2. 高阈值不中断正常重试 + 联动本机 `AI NetGuard` 静默自愈
- 写代码、跑测试、修 Bug 过程中的普通报错与单次抖动，**绝不中止任务**，照常重试推进。
- 仅当连续出现多次 `stream reading error: unexpected EOF` 疑似代理或出口节点卡死时，先静默排查本机代理并联动后台服务恢复：
  1. 核对本机系统代理与环境变量是否指向预期的本地稳定代理端口（macOS 检查 `scutil --proxy` 是否为 Clash Verge `<MAC_PROXY_PORT>`；Windows 检查注册表 `Internet Settings` 与 `HTTP_PROXY`/`HTTPS_PROXY` 是否指向 v2rayN `<WINDOWS_PROXY_PORT>`）；
  2. 直接调用本机自愈接口静默切换最优节点后继续当前任务，全程无需打扰用户：
     - macOS / Bash:
       ```bash
       curl -s -X POST http://127.0.0.1:18888/api/antigravity/recover -H "Content-Type: application/json" -d '{"reason":"Antigravity 连续流式断流请求换线"}'
       ```
     - Windows PowerShell:
       ```powershell
       curl.exe -s -X POST http://127.0.0.1:18888/api/antigravity/recover -H "Content-Type: application/json" -d "{\"reason\":\"Antigravity 连续流式断流请求换线\"}"
       ```
  3. 若切换节点并调整做法后仍连续失败，再停止向用户汇报清晰断点与原因。

## 3. 本机代理基线与后台守护
- **macOS 基线模板**：统一走原生 **Clash Verge Rev（端口 `7897`）** 搭配固定干净 AI 节点（避开香港与高频自动切节点），避免在 45–65 秒长静默 SSE 流下掐断连接；日志路径通常为 `~/Library/Logs/Antigravity/language_server.log`。
- **Windows + v2rayN 基线模板**（请按实测替换占位符）：
  - 代理客户端：**v2rayN**（核心：`<V2RAYN_CORE>`，如 `sing-box` / `xray`；本地混合/HTTP 端口：`<WINDOWS_PROXY_PORT>`，如 `10808` / `10809`）；
  - 分流策略：国内 AI（DeepSeek/Kimi/豆包/千问）与国内站点走 `DIRECT`，Antigravity / Gemini / OpenAI / Claude / Cursor / GitHub 走非受限海外固定主节点（避开 HK/CN/MO/RU）；
  - Antigravity 日志路径：`<WINDOWS_ANTIGRAVITY_LOG_PATH>`。
- 本机 **AI NetGuard**（`http://127.0.0.1:18888`）已在后台实时巡检 Antigravity 日志：遵循宽容高阈值（7 分钟内 ≥ 4 次断流或出现终态 EOF 才出手），自动静默切换最优非受限节点且不弹窗打扰。
