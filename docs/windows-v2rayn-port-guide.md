# Windows + v2rayN 移植与 1:1 前端一致性规范

本文档供在 Windows 电脑上复刻 **AI NetGuard v2.0** 时对照使用，确保 **前端视觉与交互体验 1:1 保持一致**，同时把底层网络诊断、日志看门狗、代理控制器与桌面自启完全适配 **Windows + v2rayN**。

---

## 一、前端 UI 与交互 100% 一致性契约（必须遵守）

请**直接复用并微调**本仓库的 [index.html](../src/ai_net_guard/static/index.html)（以及后端 `/api/*` 数据结构），不要推翻重做另一套界面。

### 1. 保留的视觉设计令牌（CSS Design Tokens）
- **全局暗黑主题配色**：
  - `--bg: #080c14`
  - `--bg-grad: radial-gradient(circle at 12% 0%, rgba(59,130,246,.14), transparent 40%), radial-gradient(circle at 88% 8%, rgba(99,102,241,.12), transparent 38%)`
  - `--panel: #111827`、`--panel-2: #172033`、`--panel-3: #0d1320`
  - `--border: #243149`、`--border-hi: #364a6e`
  - `--text: #eaf0fa`、`--muted: #8fa3c4`
  - `--ok: #22c55e`、`--warn: #f59e0b`、`--fail: #ef4444`、`--accent: #3b82f6`、`--accent-2: #6366f1`
- **字体栈**：Windows 下可保留无衬线现代栈并补充 `"Microsoft YaHei UI", "Segoe UI"`，保持等宽字体用于 IP / 端口 / 域名展示。
- **布局尺寸**：最大宽度 `1260px` 居中容器（`.wrap`），圆角卡片 `16px–18px`，15 秒自动静默刷新 `GET /api/status` + `localStorage` 记住当前选中的 Tab。
- **应用图标风格**：延续 `scripts/build_macos_app.sh` 的蓝紫渐变圆角底座（`#3b82f6 -> #6d28d9`）+ 白色半透明盾牌描边 + 亮绿色对勾（`#4ade80`），在 Windows 生成 `.ico` 用于桌面快捷方式和任务栏/托盘图标。

### 2. 必须保留的页面结构与 5 大 Tab 交互
1. **顶部 Hero 区**：
   - 左侧：盾牌对勾 Logo + `AI NetGuard v2.0` + 总体状态徽章（`全部健康` / `存在隐患` / `发现故障`）+ 一句话体检总结论。
   - 右侧 3 个主操作按钮：`🔍 全面体检`、`⚡ 一键切最优 AI 节点`、`自动自愈：已开启/已关闭`。
2. **5 个核心 Tab 页**：
   - `🩺 全链路总览`：
     - 5 步横向状态流水线卡片（Step 01 ~ Step 05，顶部绿/黄/红状态条 + 呼吸光点 + 单行指标 pill）；
     - `🛡️ Antigravity 静默防空转守护` 状态条（显示 `近 7 分钟断流：X / 4 次`、状态徽章、累计自愈次数，以及右侧 `⚡ 立即帮 Antigravity 换节点` 按钮）；
     - 双栏：左侧 `🩺 大白话病因诊断与一键修复建议`；右侧 `📈 实时延迟趋势`（SVG 三色折线图：蓝 Google / 绿 Antigravity / 紫 DeepSeek）+ `📜 巡检与自愈事件日志`。
   - `🌍 IP 与出口体检`：
     - 上方双视角对比卡：左侧「国内直连视角」vs 右侧「代理对外暴露出口」（含国旗 Emoji、超大号 IP、S/A/B/C/F 评级方块、IP 固定/跳变徽章、属性标签）；
     - 下方双栏：左侧 `🔎 DNS 真实解析防泄露透视` 表格；右侧 `🕒 出口 IP 变更历史记录` 表格。
   - `📡 软件与网站连通性`：
     - 分组卡片网格（海外 AI 编程主力 / 国内 AI 服务 / 常用海外网站与代码包源 / 自定义检测站点），每张卡含内联 SVG 品牌图标、HTTP 状态徽章、横向延迟进度条、以及 `复测` / `删` 按钮；
     - 底部 `➕ 添加自定义检测网站` 表单。
   - `🎛️ 代理控制中心`（Windows 版可将标题与引导文案本地化为 **v2rayN 控制中心**）：
     - 顶部新手引导卡（适配 Windows + v2rayN 分流配置与一键复制/导出分流规则按钮）；
     - 控制器状态、防封控频率安全锁横幅（`🛡️ 防封控频率安全锁`）与参数表单（API 地址、密钥、连续失败阈值、巡检间隔、冷却分钟、每小时上限、每日上限）；
     - 策略组下拉框 + `🚀 批量测速当前组` + `⚡ 自动切到最快 AI 节点` + 4 列响应式节点卡片墙（国旗、延迟徽章、推荐/受限/说明/当前使用标签，点击即切）；
     - `🔀 实时流量分流透视（Live Connections）` 表格（若使用 sing-box/mihomo Clash API 可实时展示；若为纯 Xray 无连接接口则显示清晰说明）。
   - `📚 网络原理学院 & 指南`：
     - 4 格高频场景操作速查 + 5 课可折叠 `<details>` 原理学院（底部蓝色 callout 实时注入本机当前诊断数据）+ 开源参考项目卡片。

### 3. 允许且应该本地化的地方（仅改平台相关文案）
- 把界面和诊断文案中提到的 `macOS` / `Shadowrocket` / `Clash Verge Rev` / `scutil` / `net-ui` 等字眼，替换为对应的 **Windows** 与 **v2rayN** 术语与真实探测结果。
- 保持所有 API 路径、JSON 字段名与前端渲染逻辑兼容，避免拆碎现有数据结构。

---

## 二、Windows + v2rayN 后端适配规范

### 1. L1–L5 诊断引擎适配（`diagnostics.py`）
- **本机网络 (`collect_local_network_info`)**：通过 Windows 路由表/网卡信息（如 `route print` / PowerShell `Get-NetRoute` / `ipconfig` / `socket`）获取活跃网卡名称与局域网 IP。
- **系统代理 (`collect_local_proxy_state`)**：
  - 读取注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings` 中的 `ProxyEnable`、`ProxyServer`、`ProxyOverride`，以及用户环境变量 `HTTP_PROXY` / `HTTPS_PROXY`；
  - 检测运行进程（`v2rayN.exe`、`xray.exe`、`sing-box.exe`、`mihomo.exe`、`Clash Verge.exe` 等）；
  - 解析本机活跃 DNS 服务器（`Get-DnsClientServerAddress` / `ipconfig /all`），检测是否被 `198.18.x` Fake-IP 劫持；
  - 对有效代理端口执行真实 TCP 握手存活检测，识破「界面已连接但端口假死」。
- **双视角出口 IP 与站点矩阵**：直接复用 `probe_domestic_ip`、`probe_exit_stability` 与 `PROBE_SPECS`（均为纯异步 `httpx` + 标准库实现，跨平台通用）。

### 2. Antigravity Windows 看门狗（`antigravity_watchdog.py`）
- 启动前先在 Windows 本机实测定位 Antigravity `language_server` 日志路径（常见于 `%APPDATA%\Antigravity\logs\`、`%LOCALAPPDATA%\Antigravity\` 或 `%USERPROFILE%\.gemini\` 下的最新语言服务器日志，支持通过 `ag_log_path` 配置与动态匹配最新会话日志）。
- **Windows 文件共享读取**：读取日志时不要独占锁定正在被 Antigravity 写入的日志文件，处理好文件轮转、截断或新建会话目录切换。
- 沿用宽容高阈值逻辑：默认 `window_sec=420`（7 分钟）内累计 `eof_threshold=4` 次 `unexpected EOF` 或出现终态 `agent executor error`，且不在 `cooldown_sec=300` 冷却期内时，触发静默自愈。

### 3. v2rayN 节点控制与自动切换适配器
在保持 `monitor.py` 状态机和防封控安全锁（5 次连续失败阈值、30 分钟冷却、每小时 ≤ 2 次、每天 ≤ 5 次）不变的前提下，按优先级适配 v2rayN：
- **模式 A（推荐，热切换体验最好）**：如果 v2rayN 使用 `sing-box`（或 `mihomo`）核心并开启了 Clash Experimental API（如 `127.0.0.1:9090` / `9097`），直接复用 `MihomoClient` REST 接口，实现免重启切节点、并发真延迟测速与实时连接透视。
- **模式 B（纯 v2rayN / Xray 兼容模式）**：如果用户继续使用 `xray` 核心，适配器需：
  1. 在首次写入前自动备份 `guiNDB.db` 和 `guiNConfig.json`；
  2. 解析节点池并复用 `filter_clean_ai_nodes` 过滤规则（剔除公告/流量说明节点与 HK/CN/MO/RU 受限区，优先 JP/SG/TW/US/KR）；
  3. 探测候选节点连通性与延迟后，安全更新当前出站配置并平滑触发 v2rayN/核心重载。

### 4. Windows 桌面端、CLI 与无黑框开机自启
- **路径规范**：配置与状态目录改用 Windows 规范路径（如 `%LOCALAPPDATA%\ai-net-guard\` 或 `%USERPROFILE%\.config\ai-net-guard\` + `%USERPROFILE%\.local\state\ai-net-guard\`），保持 `AI_NET_GUARD_CONFIG_DIR` / `AI_NET_GUARD_STATE_DIR` 环境变量可覆盖。
- **桌面壳**：提供与 Mac 原生 WKWebView 窗口等价的 Windows 桌面应用体验（`pywebview` Edge WebView2 窗口、系统托盘图标或 Edge `--app=http://127.0.0.1:18888` 启动器 + 桌面快捷方式），启动时若后端未运行则无黑框拉起 `127.0.0.1:18888`。
- **开机静默自启**：通过 Windows 任务计划程序或 Startup `.vbs` + `pythonw.exe` 实现开机后台静默常驻，绝不弹出黑色控制台窗口。
