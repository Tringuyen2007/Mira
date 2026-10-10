import Foundation
import Observation

@MainActor
@Observable
final class AppState {
    let liveSession = LiveSessionStore()
    private(set) var serverAddress: String
    private(set) var connectionEnabled: Bool
    private(set) var restStatus: String?
    private(set) var isTestingREST = false
    private(set) var configurationError: String?
    @ObservationIgnored private var foreground = true
    @ObservationIgnored private var configurationVersion = UUID()

    var hasCompletedOnboarding: Bool {
        didSet { defaults.set(hasCompletedOnboarding, forKey: "hasCompletedOnboarding") }
    }

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let tokenStore: TokenStore
    /// The server's MIRA_API_TOKEN, from the Keychain. Never logged or shown in full.
    @ObservationIgnored private var token: String?
    private(set) var tokenHint: String?

    var isPaired: Bool { token != nil }

    init(defaults: UserDefaults = .standard, tokenStore: TokenStore = KeychainTokenStore()) {
        self.defaults = defaults
        self.tokenStore = tokenStore
        hasCompletedOnboarding = defaults.bool(forKey: "hasCompletedOnboarding")
        serverAddress = defaults.string(forKey: "serverAddress") ?? "http://localhost:8000"
        connectionEnabled = defaults.bool(forKey: "connectionEnabled")
        token = tokenStore.load()
        tokenHint = token.map(PairingToken.masked)
    }

    var apiClient: APIClient? {
        guard let address = try? ServerAddress(serverAddress) else { return nil }
        return APIClient(address: address, token: token)
    }

    /// Saves the address and, when one is entered, a new token (an empty token field
    /// keeps the saved one). Without any token the app shows "not paired" instead of
    /// opening sockets the server would refuse.
    @discardableResult
    func connect(address text: String, token tokenText: String = "") -> Bool {
        do {
            let address = try ServerAddress(text)
            let newToken = PairingToken.normalized(tokenText)
            if let newToken {
                try tokenStore.save(newToken)
                token = newToken
                tokenHint = PairingToken.masked(newToken)
            }
            let changed = address.url.absoluteString != serverAddress || newToken != nil
            serverAddress = address.url.absoluteString
            defaults.set(serverAddress, forKey: "serverAddress")
            connectionEnabled = true
            defaults.set(true, forKey: "connectionEnabled")
            configurationVersion = UUID()
            restStatus = nil
            configurationError = token == nil ? APIError.notPaired.localizedDescription : nil
            if foreground { liveSession.connect(to: address, token: token, reset: changed) }
            return true
        } catch {
            configurationError = error.localizedDescription
            return false
        }
    }

    func forgetToken() {
        tokenStore.delete()
        token = nil
        tokenHint = nil
        configurationVersion = UUID()
        restStatus = nil
        if connectionEnabled, let address = try? ServerAddress(serverAddress) {
            liveSession.connect(to: address, token: nil, reset: true)
        }
    }

    func disconnect() {
        connectionEnabled = false
        defaults.set(false, forKey: "connectionEnabled")
        configurationVersion = UUID()
        liveSession.disconnect()
    }

    func setForeground(_ active: Bool) {
        foreground = active
        if active, connectionEnabled, let address = try? ServerAddress(serverAddress) {
            liveSession.connect(to: address, token: token)
        } else if !active {
            liveSession.disconnect()
        }
    }

    func testREST() async {
        guard !isTestingREST, let apiClient else { return }
        let version = configurationVersion
        isTestingREST = true
        restStatus = nil
        defer { isTestingREST = false }
        do {
            // Reachability first (public), then a call that needs the token.
            _ = try await apiClient.health()
            _ = try await apiClient.calibrationStatus()
            guard version == configurationVersion else { return }
            restStatus = "REST OK · server online, token accepted"
        } catch APIError.unauthorized {
            guard version == configurationVersion else { return }
            restStatus = "Token rejected (HTTP 401) · check the token in Settings"
        } catch APIError.notPaired {
            guard version == configurationVersion else { return }
            restStatus = "Server online · not paired (no token saved)"
        } catch {
            guard version == configurationVersion else { return }
            restStatus = "REST failed · \(error.localizedDescription)"
        }
    }
}
