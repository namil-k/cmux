import Foundation
import WebKit

/// Adapts noVNC's connection status into native recovery UI. Observing its
/// status elements also catches RFB failures after a successful HTTP page load.
///
/// noVNC keeps the last framebuffer painted while it retries, so a viewer that
/// lost its session looks identical to a working one. Reporting every state,
/// not only `connected` and `failed`, is what lets the pane tell the user the
/// desktop is not accepting input and lets the route rebind itself.
@MainActor
final class CloudDesktopConnectionObserver: NSObject, WKScriptMessageHandler {
    static let name = "cmuxCloudDesktopConnection"
    static let contentWorld = WKContentWorld.world(name: "cmux.cloud.desktop-connection")
    static let userScript = WKUserScript(
        source: """
        (() => {
          if (location.pathname !== '/vnc.html') return;
          const status = document.getElementById('noVNC_status');
          if (!status || !document.getElementById('noVNC_container')) return;
          let last;
          let everConnected = false;
          const report = () => {
            const root = document.documentElement.classList;
            const connected = root.contains('noVNC_connected');
            const reconnecting = root.contains('noVNC_reconnecting');
            const failed = status.classList.contains('noVNC_status_error') &&
                           status.classList.contains('noVNC_open');
            if (connected) everConnected = true;
            // Before the first successful RFB handshake the pane is already
            // showing its native connecting state, so only a real error is
            // worth reporting. Losing an established session is what the
            // native side cannot otherwise see.
            const value = connected ? 'connected'
              : failed ? 'failed'
              : !everConnected ? null
              : reconnecting ? 'reconnecting'
              : 'disconnected';
            if (value && value !== last) {
              last = value;
              window.webkit.messageHandlers['\(name)'].postMessage(value);
            }
          };
          const observer = new MutationObserver(report);
          observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
          observer.observe(status, { attributes: true, attributeFilter: ['class'] });
          report();
        })();
        """,
        injectionTime: .atDocumentEnd,
        forMainFrameOnly: true,
        in: contentWorld
    )

    private weak var webView: WKWebView?
    private let onChange: @MainActor (URL, CloudDesktopConnectionState) -> Void

    init(webView: WKWebView, onChange: @escaping @MainActor (URL, CloudDesktopConnectionState) -> Void) {
        self.webView = webView
        self.onChange = onChange
    }

    static func install(
        on webView: WKWebView,
        onChange: @escaping @MainActor (URL, CloudDesktopConnectionState) -> Void
    ) {
        let controller = webView.configuration.userContentController
        controller.removeScriptMessageHandler(forName: name, contentWorld: contentWorld)
        controller.add(CloudDesktopConnectionObserver(webView: webView, onChange: onChange), contentWorld: contentWorld, name: name)
        if !controller.userScripts.contains(where: { $0.source == userScript.source }) {
            controller.addUserScript(userScript)
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == Self.name, message.frameInfo.isMainFrame,
              message.webView === webView, let url = message.frameInfo.request.url,
              let raw = message.body as? String,
              let state = CloudDesktopConnectionState(rawValue: raw) else { return }
        onChange(url, state)
    }
}
