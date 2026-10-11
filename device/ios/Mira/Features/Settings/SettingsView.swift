import SwiftUI

struct SettingsView: View {
    @Environment(AppState.self) private var state
    #if DEBUG
    @State private var address = ""
    @State private var token = ""
    #endif

    var body: some View {
        Form {
            #if DEBUG
            Section("Developer · Mira server") {
                TextField("http://laptop.local:8000", text: $address)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .accessibilityIdentifier("serverAddress")
                SecureField(state.isPaired ? "Token saved — enter to replace" : "Pairing token (MIRA_API_TOKEN)",
                            text: $token)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("serverToken")
                if let hint = state.tokenHint {
                    LabeledContent("Token", value: hint)
                }
                Button("Save & connect") {
                    if state.connect(address: address, token: token) { address = state.serverAddress }
                    token = ""
                }
                Text(state.liveSession.connectionState.label)
                    .accessibilityIdentifier("connectionStatus")
                if let error = state.configurationError { Text(error).foregroundStyle(.red) }
                if state.connectionEnabled {
                    Button("Disconnect") { state.disconnect() }
                }
                if state.isPaired {
                    Button("Forget token", role: .destructive) { state.forgetToken() }
                }
            }
            Section("Connection check") {
                Button(state.isTestingREST ? "Testing…" : "Test REST connection") {
                    Task { await state.testREST() }
                }
                .disabled(state.isTestingREST || address != state.serverAddress)
                if let status = state.restStatus { Text(status) }
                LabeledContent("Live stream", value: state.liveSession.liveSocket.state.label)
                LabeledContent("Events", value: state.liveSession.eventSocket.state.label)
                LabeledContent("Pose", value: state.liveSession.poseSocket.state.label)
                LabeledContent("Messages received", value: "\(state.liveSession.receivedMessages)")
                if let error = state.liveSession.decodingError {
                    Text(error).font(.caption).foregroundStyle(.red)
                }
            }
            #endif
            Section("About") { LabeledContent("App", value: "Mira") }
            Section {
                Button("Show onboarding") { state.hasCompletedOnboarding = false }
            }
        }
        .navigationTitle("Settings")
        #if DEBUG
        .onAppear { address = state.serverAddress }
        #endif
    }
}
