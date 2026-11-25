# AI NetGuard（AI 网络医生与自动自愈管家）

专门解决在国内跑 Antigravity / Codex / Claude / Gemini 等 AI 编辑器和后台 Agent 时频繁遇到的：
- 代理 UI 显示已连接但实际端口不通（假连接/死端口）
- 出口 IP 高频跳变、节点落在香港等受限地区导致长会话中断并反复倒计时重试
- Fake-IP DNS 劫持导致打开代理时连国内 DeepSeek 也一起卡死
- Antigravity 语言服务器长流 `stream reading error: unexpected EOF` 空转卡死
- 无人值守跑任务时中途网络波动无自动切节点与防封控保护机制

## 核心界面与功能（5 大面板 + 5 步全链路）

Web 仪表盘默认运行在 `http://127.0.0.1:18888`（[前端源码](src/ai_net_guard/static/index.html)）：
1. **🩺 全链路总览**：5 步状态流水线、Antigravity 静默防空转守护状态条、大白话诊断与修复建议、实时延迟折线图、事件日志。
2. **🌍 IP 与出口体检**：国内直连 IP vs 代理出口 IP 双视角卡片（含 AI 节点 S/A/B/C/F 评级）、DNS 防泄露透视表、持久化 IP 变更历史。
3. **📡 软件与网站连通性**：海外 AI / 国内直连 AI / 开发包源分组测速矩阵 + 自定义检测站点与单点复测。
4. **🎛️ 控制中心**：代理控制器接管、防封控频率安全锁（默认 30 分钟冷却 / 每小时最多 2 次 / 每天最多 5 次）、节点卡片墙与批量测速、实时流量连接透视。
5. **📚 网络原理学院 & 指南**：结合本机实时数据的 5 课排障原理与操作速查。

## 常用命令

```bash
ai-net doctor        # 终端输出五步全链路体检报告（支持 --json）
ai-net open          # 自动拉起后台服务并打开仪表盘 http://127.0.0.1:18888
ai-net heal          # 立即检测并通过控制器切换最优 AI 节点
ai-net wait-healthy  # 阻塞等待直到核心 AI 网络健康（用于自动化脚本门禁）
ai-net start|stop|status
ai-net export-override  # 打印防断连分流覆写脚本路径
```

## 目录与跨平台文档

- `src/ai_net_guard/`：FastAPI 服务、五步诊断引擎、Antigravity 日志看门狗、Mihomo/Clash 控制器与自愈状态机
- `src/ai_net_guard/static/index.html`：暗黑风格中文可视化单页仪表盘（跨平台可直接复用）
- `scripts/AINetGuardApp.swift` & `scripts/build_macos_app.sh`：macOS 原生 WKWebView 桌面套壳与盾牌对勾图标构建脚本
- `docs/windows-v2rayn-port-guide.md`：**Windows + v2rayN 移植与 1:1 前端一致性规范**
- `docs/antigravity-anti-hang-template/`：**Antigravity 专属防断流规则插件模板**
- `docs/clash-verge-setup.md` & `clash-ai-override.js`：macOS Clash Verge Rev 配置记录与扩展覆写脚本
