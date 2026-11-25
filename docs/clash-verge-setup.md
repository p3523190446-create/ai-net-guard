# 从 Shadowrocket 迁移到 Clash Verge Rev（2026-11-23）

## 为什么要换

Shadowrocket 的 macOS 网络扩展（`MacPacketTunnel`）长时间跑 AI 流式连接后会"假死"：

- 1082 端口仍在监听，系统代理也指向它；
- 但转发到海外节点的连接只握手不回数据（`CONNECT` 返回 200 后无响应），
  表现为 `ConnectError` / `SSL_ERROR_SYSCALL`；
- 同时把 Fake-IP DNS（198.18.0.2）塞进系统解析器，一旦假死会连带国内站点一起卡住。

Antigravity 编辑器挂掉的直接原因就在这里：它的语言服务器反复请求
`daily-cloudcode-pa.googleapis.com`、`aicode.googleapis.com` 全部失败，
UI 层看起来"又挂了"。

## 现在的结构

| 组件 | 说明 |
| --- | --- |
| 代理内核 | Clash Verge Rev 2.5.10 + verge-mihomo v1.19.33 |
| 订阅 | 同一份机场订阅，UA 适配为 `clash-verge/v2.5.10` 时返回标准 Clash YAML |
| 混合端口 | `127.0.0.1:7897`（系统代理指向这里） |
| 控制端口 | `127.0.0.1:9097`，secret `set-your-secret` |
| 代理模式 | 规则模式，系统代理开启（TUN 未启用，见下） |
| 出站分组 | `🤖 AI` → `📍 指定节点` → `🇸🇬 新加坡 · 01`（实测 50–300 ms） |

配置文件位置：

```
~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/
├── profiles.yaml            # current 指向远程订阅 profile
├── profiles/<uid>.yaml      # 订阅快照（Verge 每次更新会覆盖）
└── clash-verge.yaml         # 运行时配置（由 profile + Merge 生成）
```

`profiles/Merge.yaml` 里保留了 `profile.store-selected: true`，
所以手动选中的节点在重启后依然生效。

## 为什么还需要 HTTP(S)_PROXY 环境变量

Verge 的"系统代理"只影响遵守 macOS 系统代理的程序（Chromium/浏览器等）。
Antigravity 的语言服务器是个 Go 进程，只认 `HTTP_PROXY` / `HTTPS_PROXY`，
原来能工作是因为 Shadowrocket 用 TUN 做了透明代理。

现在由 LaunchAgent 把代理变量写进 launchd 用户域，Finder/Dock 启动的 App 都能继承：

```
~/Library/LaunchAgents/com.ainetguard.proxyenv.plist
└── ~/.local/bin/set-proxy-env.sh   # launchctl setenv HTTP_PROXY/HTTPS_PROXY/NO_PROXY
```

想彻底摆脱环境变量，可以在 Clash Verge 里打开「虚拟网卡模式」——
需要管理员密码安装 `clash-verge-service`，装好后 TUN 接管全局流量，
不再依赖系统代理和环境变量（本机目前没装服务）。

## AI NetGuard 联动

`~/.config/ai-net-guard/config.json` 已经指向 Clash：

```json
{
  "proxy_url": "auto",
  "mihomo_api": "http://127.0.0.1:9097",
  "mihomo_secret": "set-your-secret",
  "mihomo_group": "🤖 AI",
  "auto_heal": true
}
```

于是仪表盘的「一键切换最优节点」和自动自愈可以真正调用 mihomo API 切节点，
不再像用小火箭时那样只能提示"检测不到控制端口"。

## 日常维护

刷新订阅（等价于 Verge 界面里的「更新」）：

```bash
UID=$(grep '^current:' ~/Library/Application\ Support/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml | awk '{print $2}')
curl -sS -A "clash-verge/v2.5.10" \
  -o ~/Library/Application\ Support/io.github.clash-verge-rev.clash-verge-rev/profiles/$UID.yaml \
  "<YOUR_SUBSCRIPTION_URL>"
```

排查顺序：

1. `curl -x http://127.0.0.1:7897 https://antigravity.google -o /dev/null -w '%{http_code}'`
   —— 200 说明链路通。
2. 不通就先看 `🤖 AI` 分组选的节点是不是挂了，换一个（或改选 `AI 自动`）。
3. 还不行就更新订阅，机场节点会轮换。
4. `curl -s http://127.0.0.1:18888/api/status | jq .report.overall_status` 看 NetGuard 的判断。

## 注意

- 不要再同时启动 Shadowrocket：它会抢走系统代理（1082）并把 DNS 换回 Fake-IP。
- 开机自启：`com.ainetguard.clash-verge`（Clash Verge）与 `com.ainetguard.proxyenv`（代理变量）两个 LaunchAgent。
- AI NetGuard 自身由 `com.panyuanhao.ai-net-guard` 这个 LaunchAgent 托管（KeepAlive）。
