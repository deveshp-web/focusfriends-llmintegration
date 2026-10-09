import UIKit
import WebKit

/// The one screen: a web view showing the site, laid out inside the safe area.
///
/// - Pages of the site stay in the app. A link anywhere else opens in Safari,
///   and only past the grown-up check (`ParentalGate`).
/// - The configuration limits navigation to the "app-bound domains" listed in
///   Config/Info.plist. Besides keeping the app on its own site, that is what
///   lets WebKit run the site's service worker inside an app, so the app
///   keeps working offline after its first visit.
/// - The first launch without internet shows `OfflineView` instead of a
///   blank page.
final class WebViewController: UIViewController, WKNavigationDelegate, WKUIDelegate {
    private var webView: WKWebView!
    private let bridge = NativeBridge()
    private let offlineView = OfflineView()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = SiteConfig.pageColor   // shows behind the status bar and home indicator

        let configuration = WKWebViewConfiguration()
        configuration.limitsNavigationsToAppBoundDomains = true
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []   // the app's sound effects
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1"
        configuration.applicationNameForUserAgent =
            [configuration.applicationNameForUserAgent, "FocusBridgeiOS/\(version)"]
                .compactMap { $0 }.joined(separator: " ")
        configuration.userContentController.addUserScript(NativeBridge.userScript)
        configuration.userContentController.add(bridge, name: NativeBridge.handlerName)
        bridge.presenter = self

        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.isOpaque = false
        webView.backgroundColor = SiteConfig.pageColor
        #if DEBUG
        if #available(iOS 16.4, *) {
            webView.isInspectable = true   // Safari > Develop > (device) > Focus Bridge
        }
        #endif

        offlineView.isHidden = true
        offlineView.onRetry = { [weak self] in self?.loadSite() }

        for child in [webView!, offlineView] as [UIView] {
            child.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(child)
            let safe = view.safeAreaLayoutGuide
            NSLayoutConstraint.activate([
                child.topAnchor.constraint(equalTo: safe.topAnchor),
                child.bottomAnchor.constraint(equalTo: safe.bottomAnchor),
                child.leadingAnchor.constraint(equalTo: safe.leadingAnchor),
                child.trailingAnchor.constraint(equalTo: safe.trailingAnchor),
            ])
        }

        loadSite()
    }

    private func loadSite() {
        offlineView.isHidden = true
        webView.load(URLRequest(url: SiteConfig.startURL))
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        bridge.stopSpeaking()
    }

    // MARK: - Where pages may go

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard let url = navigationAction.request.url else { return .cancel }
        if navigationAction.shouldPerformDownload {
            return .cancel   // the bridge saves files; see NativeBridge
        }
        let scheme = url.scheme?.lowercased() ?? ""
        if SiteConfig.isSitePage(url) || ["about", "blob", "data"].contains(scheme) {
            return .allow
        }
        if navigationAction.navigationType == .linkActivated || navigationAction.targetFrame == nil {
            openOutside(url)
        }
        return .cancel
    }

    /// Links that ask for a new window (target="_blank").
    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            if SiteConfig.isSitePage(url) {
                webView.load(navigationAction.request)
            } else {
                openOutside(url)
            }
        }
        return nil
    }

    private func openOutside(_ url: URL) {
        guard ["http", "https", "mailto", "tel"].contains(url.scheme?.lowercased() ?? "") else { return }
        ParentalGate.ask(from: self, reason: "This opens a page outside Focus Bridge.") {
            UIApplication.shared.open(url)
        }
    }

    // MARK: - Loading, failing, recovering

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        offlineView.isHidden = true
    }

    /// The site could not be reached at all. Once the site has loaded once,
    /// its service worker answers offline and this is not reached.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let error = error as NSError
        guard error.domain == NSURLErrorDomain, error.code != NSURLErrorCancelled else { return }
        offlineView.isHidden = false
    }

    /// iOS may end the web view's process under memory pressure; start again.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }

    // MARK: - JavaScript alert() and confirm()

    // A web view shows nothing for these unless the app does. The site doesn't
    // use them today; this keeps a future use from failing silently.

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo) async {
        guard presentedViewController == nil else { return }
        await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in done.resume() })
            present(alert, animated: true)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo) async -> Bool {
        guard presentedViewController == nil else { return false }
        return await withCheckedContinuation { (done: CheckedContinuation<Bool, Never>) in
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in done.resume(returning: false) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in done.resume(returning: true) })
            present(alert, animated: true)
        }
    }
}
