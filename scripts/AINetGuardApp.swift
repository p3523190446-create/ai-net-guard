import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let baseURL = URL(string: "http://127.0.0.1:18888/")!
    private let probeURL = URL(string: "http://127.0.0.1:18888/api/override")!
    private let aiNetBin = (NSHomeDirectory() as NSString).appendingPathComponent(".local/bin/ai-net")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        setupMenuBar()
        setupWindow()
        loadDashboardOrStartBackend()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    private func setupMenuBar() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(
            withTitle: "退出 AI NetGuard",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        appMenuItem.submenu = appMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu

        let viewMenuItem = NSMenuItem()
        mainMenu.addItem(viewMenuItem)
        let viewMenu = NSMenu(title: "视图")
        viewMenu.addItem(withTitle: "刷新面板", action: #selector(reloadPage), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        viewMenuItem.submenu = viewMenu

        NSApp.mainMenu = mainMenu
    }

    private func setupWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1280, height: 860)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "AI NetGuard · AI 网络医生与代理控制平台"
        window.center()
        window.minSize = NSSize(width: 900, height: 620)
        window.backgroundColor = NSColor(calibratedRed: 0.031, green: 0.047, blue: 0.078, alpha: 1.0)
        window.appearance = NSAppearance(named: .darkAqua)

        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")

        webView = WKWebView(frame: rect, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func reloadPage() {
        webView.reload()
    }

    private func showLoadingPage(message: String) {
        let html = """
        <!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><style>
        body{margin:0;background:#080c14;color:#eaf0fa;font-family:-apple-system,PingFang SC,sans-serif;
        display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;}
        .box{padding:28px 36px;background:#111827;border:1px solid #243149;border-radius:16px;}
        .t{font-size:18px;font-weight:700;margin-bottom:8px;}
        .s{font-size:13px;color:#8fa3c4;}
        </style></head><body><div class="box"><div class="t">🛡️ AI NetGuard</div><div class="s">\(message)</div></div></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func probeBackend(completion: @escaping (Bool) -> Void) {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        cfg.timeoutIntervalForRequest = 1.5
        let session = URLSession(configuration: cfg)
        let task = session.dataTask(with: probeURL) { _, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { completion(ok) }
        }
        task.resume()
    }

    private func loadDashboardOrStartBackend() {
        probeBackend { [weak self] alive in
            guard let self = self else { return }
            if alive {
                self.webView.load(URLRequest(url: self.baseURL))
                return
            }
            self.showLoadingPage(message: "正在拉起本机监控服务 (127.0.0.1:18888)...")
            DispatchQueue.global(qos: .userInitiated).async {
                if FileManager.default.isExecutableFile(atPath: self.aiNetBin) {
                    let proc = Process()
                    proc.executableURL = URL(fileURLWithPath: self.aiNetBin)
                    proc.arguments = ["start"]
                    try? proc.run()
                    proc.waitUntilExit()
                }
                self.waitAndLoad(attempt: 0)
            }
        }
    }

    private func waitAndLoad(attempt: Int) {
        probeBackend { [weak self] alive in
            guard let self = self else { return }
            if alive {
                self.webView.load(URLRequest(url: self.baseURL))
            } else if attempt < 24 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                    self.waitAndLoad(attempt: attempt + 1)
                }
            } else {
                self.showLoadingPage(message: "后台服务启动超时，请检查 ~/.local/state/ai-net-guard/launchd.err.log")
            }
        }
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        if let url = navigationAction.request.url,
           let scheme = url.scheme?.lowercased(),
           scheme == "http" || scheme == "https" {
            let host = url.host?.lowercased() ?? ""
            if host != "127.0.0.1" && host != "localhost" {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
        }
        decisionHandler(.allow)
    }

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
