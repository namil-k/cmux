import Foundation

/// What the embedded noVNC viewer reports about its own RFB session.
///
/// noVNC drops every pointer event while its session is not `connected`
/// (`RFB._sendMouse` returns early on `_rfbConnectionState !== 'connected'`),
/// but it keeps the last framebuffer painted. Reporting only `connected` and
/// `failed` therefore leaves a silently retrying viewer indistinguishable from
/// a working one: the desktop looks alive and swallows every click and drag.
enum CloudDesktopConnectionState: String, Sendable, Equatable, CaseIterable {
    case connected
    case reconnecting
    case disconnected
    case failed

    var isConnected: Bool { self == .connected }
}

/// Decides when a Cloud Desktop viewer that lost its session needs its
/// authenticated route rebound, rather than being left to noVNC's own retry.
///
/// The desktop page reaches its machine through a per-VM browser carrier. That
/// carrier's loopback port and WebSocket token are injected into the document
/// when it loads, and noVNC's built-in `reconnect=1` retry reuses whatever the
/// document was built with. Once the carrier is replaced, every retry dials a
/// port that no longer exists. The document went stale, not the URL: the page
/// URL is the machine's private address, which is identical across carrier
/// restarts, so URL-comparing navigation never re-navigates and the injected
/// credentials are never refreshed.
///
/// Recovery is therefore driven by an observed endpoint change, never by a
/// timer: at most one endpoint re-resolve per disconnected episode, and a
/// reload only when the resolved endpoint actually differs from the one the
/// live document carries.
struct CloudDesktopRecoveryPolicy: Equatable {
    enum Action: Equatable {
        /// Ask the shared route for its current carrier endpoint.
        case resolveEndpoint
        /// Re-apply the carrier credentials and reload the viewer once.
        case rebind
        /// Nothing to do. Named so it cannot be confused with `Optional.none`.
        case idle
    }

    /// Consecutive rebinds allowed without the viewer reaching `connected`.
    /// Past this the endpoint is demonstrably not what is broken, so the
    /// failure is surfaced instead of reloading the document again.
    static let rebindLimit = 3

    private(set) var boundEndpoint: CloudBrowserProxyEndpoint?
    private(set) var rebindsWithoutConnection = 0
    private var didRequestResolve = false

    /// True once repeated rebinds have failed to restore a session, so the
    /// pane reports a real failure instead of presenting another retry.
    var hasExhaustedRebinds: Bool { rebindsWithoutConnection >= Self.rebindLimit }

    /// The viewer committed a document built with `endpoint`.
    mutating func documentDidBind(to endpoint: CloudBrowserProxyEndpoint?) {
        boundEndpoint = endpoint
        didRequestResolve = false
    }

    mutating func viewerDidReport(_ state: CloudDesktopConnectionState) -> Action {
        guard !state.isConnected else {
            didRequestResolve = false
            rebindsWithoutConnection = 0
            return .idle
        }
        guard boundEndpoint != nil, !didRequestResolve, !hasExhaustedRebinds else { return .idle }
        didRequestResolve = true
        return .resolveEndpoint
    }

    /// `endpoint` is read from the shared route at call time, so a late
    /// callback cannot reintroduce an endpoint a recovered session replaced.
    mutating func routeDidChange(currentEndpoint endpoint: CloudBrowserProxyEndpoint?) -> Action {
        guard let endpoint, let bound = boundEndpoint, endpoint != bound,
              !hasExhaustedRebinds else { return .idle }
        boundEndpoint = endpoint
        didRequestResolve = false
        rebindsWithoutConnection += 1
        return .rebind
    }

    mutating func reset() { self = CloudDesktopRecoveryPolicy() }
}
