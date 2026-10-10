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

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        hasCompletedOnboarding = defaults.bool(forKey: "hasCompletedOnboarding")
        serverAddress = defaults.string(forKey: "serverAddress") ?? "http://localhost:8000"
        connectionEnabled = defaults.bool(forKey: "connectionEnabled")
    }

    var apiClient: APIClient? {
        guard let address = try? ServerAddress(serverAddress) else { return nil }
        return APIClient(address: address)
    }

    @discardableResult
    func connect(address text: String) -> Bool {
        do {
            let address = try ServerAddress(text)
            let changed = address.url.absoluteString != serverAddress
            serverAddress = address.url.absoluteString
            defaults.set(serverAddress, forKey: "serverAddress")
            connectionEnabled = true
            defaults.set(true, forKey: "connectionEnabled")
            configurationVersion = UUID()
            restStatus = nil
            configurationError = nil
            if foreground { liveSession.connect(to: address, reset: changed) }
            return true
        } catch {
            configurationError = error.localizedDescription
            return false
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
            liveSession.connect(to: address)
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
            _ = try await apiClient.calibrationStatus()
            guard version == configurationVersion else { return }
            restStatus = "REST OK · calibration status received"
        } catch {
            guard version == configurationVersion else { return }
            restStatus = "REST failed · \(error.localizedDescription)"
        }
    }
}
