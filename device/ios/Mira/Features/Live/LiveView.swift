import SwiftUI

struct LiveView: View {
    @Environment(AppState.self) private var state
    @State private var commandError: String?
    @State private var isSending = false

    var body: some View {
        List {
            if state.liveSession.connectionState != .connected {
                Section {
                    Text("Mira is unavailable right now.")
                    if state.liveSession.live != nil {
                        Text("Live updates are paused.").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if let live = state.liveSession.live {
                Section(live.exerciseId) {
                    LabeledContent("Reps", value: "\(live.rep) / \(live.repsTarget)")
                    LabeledContent("Phase", value: live.paused ? "Paused" : live.phase)
                    if let angle = live.mainAngle { LabeledContent("Angle", value: "\(angle.formatted())°") }
                    LabeledContent("Left / right balance", value: "\(live.loadLPct.formatted())% / \((100 - live.loadLPct).formatted())%")
                    if let cue = state.liveSession.cue { Text(cue) }
                }
            } else {
                Text("Your session will appear here when Mira is ready.")
            }
            Section("Session controls") {
                ForEach([CommandName.start, .pause, .skip, .stop], id: \.rawValue) { command in
                    Button(command.rawValue.capitalized) { send(command) }
                }
                .disabled(state.liveSession.eventSocket.state != .connected || isSending)
                if let commandError { Text(commandError).foregroundStyle(.red) }
            }
            if let alert = state.liveSession.alert {
                Section("Mira alert") {
                    Text("\(alert.kind): \(alert.step)")
                    Button("I'm OK") { send(.imOK) }
                        .disabled(state.liveSession.eventSocket.state != .connected || isSending)
                }
            }
        }
        .navigationTitle("Live session")
    }

    private func send(_ command: CommandName) {
        isSending = true
        commandError = nil
        Task {
            defer { isSending = false }
            do { try await state.liveSession.send(command) }
            catch {
                commandError = "Mira couldn’t confirm that action. Please try again when Mira is available."
            }
        }
    }
}
