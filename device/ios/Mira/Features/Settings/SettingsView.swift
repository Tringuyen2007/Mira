import SwiftUI
import UIKit

/// A temp-file export ready to hand to the system share sheet.
private struct ExportFile: Identifiable {
    let url: URL
    var id: String { url.path }
}

/// `UIActivityViewController` wrapper; SwiftUI's `ShareLink` needs the item ready
/// before the view renders, but the export is fetched from the server on tap.
private struct ActivityShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

struct SettingsView: View {
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    var body: some View {
        MiraPage {
            MiraScreenHeading(title: "Settings", detail: "Change how Mira looks, sounds and helps.", mascot: true)
            MiraCard(padding: 16) {
                VStack(spacing: 0) {
                    ForEach(Array(SettingsDetailView.Screen.allCases.filter { $0 != .delete }.enumerated()), id: \.element) { index, screen in
                        NavigationLink { SettingsDetailView(screen: screen) } label: { MiraMenuRow(title: screen.rowTitle, detail: detail(screen), screen: "settings", icon: screen.icon, index: index) }.buttonStyle(.plain)
                        if index < 4 { Divider().padding(.leading, 62) }
                    }
                }
            }
            Button("Review my details") { state.revisitOnboarding(at: .review) }.font(MiraUI.font(16, "Medium")).foregroundStyle(MiraUI.accent)
            #if DEBUG
            if ProcessInfo.processInfo.arguments.contains("--network-debug") {
                NavigationLink("Developer connection tools") { NetworkDiagnosticsView() }.font(MiraUI.font(16))
            }
            #endif
        }.navigationTitle("").toolbar(.hidden, for: .navigationBar)
            .task {
                // Wires local-notification syncing in once per app run; Reminders.swift
                // (outside the MiraNetworking package) is only reachable from here/the
                // app target, not from AppState itself.
                if state.onPreferencesChanged == nil {
                    state.onPreferencesChanged = { prefs in Task { await Reminders.sync(prefs) } }
                }
            }
    }
    private func detail(_ screen: SettingsDetailView.Screen) -> String {
        switch screen {
        case .rerun: preview ? "Last done Oct 1" : "Set up your mat and cameras"
        case .voice: "\(state.preferences.voice) voice · Volume \(Int((state.preferences.volume * 100).rounded()))%"
        case .captions: "\(state.preferences.showCaptions ? "On" : "Off") · \(state.preferences.extraLargeCaptions ? "Extra large" : "Large") text"
        case .reminders: state.preferences.dailyReminder ? "Daily at \(reminderTimeLabel)" : "Off"
        default: "No video leaves your home"
        }
    }
    private var reminderTimeLabel: String {
        (Calendar.current.date(from: DateComponents(hour: state.preferences.reminderHour, minute: state.preferences.reminderMinute)) ?? Date())
            .formatted(date: .omitted, time: .shortened)
    }
}

struct SettingsDetailView: View {
    enum Screen: String, CaseIterable { case rerun, voice, captions, reminders, privacy, delete
        var title: String { switch self { case .rerun: "Re-run setup"; case .voice: "Mira’s voice"; case .captions: "Captions"; case .reminders: "Reminders"; case .privacy: "Privacy and data"; case .delete: "Delete all your data?" } }
        var rowTitle: String { self == .voice ? "Mira’s voice and volume" : title }
        var icon: String { switch self { case .rerun: "imgIconRedo"; case .voice: "imgIconSpeaker"; case .captions: "imgIconCc"; case .reminders: "imgIconBell"; default: "imgIconLock" } }
    }
    let screen: Screen
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    @Environment(\.dismiss) private var dismiss
    @State private var camerasOnly = true
    @State private var showSetup = false
    @State private var changeTime = false
    @State private var speech = MiraSpeech()
    @State private var devicePrefsTask: Task<Void, Never>?
    @State private var exportFile: ExportFile?
    /// Mirrors `exportFile.url` so the sheet's `onDismiss` can delete the temp file
    /// even though SwiftUI nils the `item` binding before calling `onDismiss`.
    @State private var exportFileURLPendingCleanup: URL?
    @State private var exportError: String?
    @State private var isExporting = false
    @State private var showDeleteConfirm = false
    @State private var isDeleting = false
    @State private var deleteError: String?
    /// Optimistic local mirror of `me.patient.shareByDefault` (I11): the toggle
    /// flips immediately on tap, and reverts with an error line if the `PUT /me`
    /// fails, instead of waiting on the round trip or silently snapping back.
    @State private var shareOverride: Bool?
    @State private var shareError: String?
    var body: some View {
        MiraPage {
            if screen == .delete { Image("MascotWorried").resizable().scaledToFit().frame(width: 120, height: 134).accessibilityHidden(true) }
            MiraScreenHeading(title: screen.title, detail: screen == .rerun ? "Do this if you moved your mat or cameras, or Mira has trouble seeing you." : nil)
            switch screen { case .rerun: rerun; case .voice: voice; case .captions: captions; case .reminders: reminders; case .privacy: privacy; case .delete: delete }
        }.navigationTitle("").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Text("Settings").font(MiraUI.font(16)).foregroundStyle(MiraUI.soft) } }
            .sheet(isPresented: $showSetup) { NavigationStack { SetupView(initialStep: camerasOnly ? 1 : 0) } }
            .sheet(item: $exportFile, onDismiss: {
                // The share sheet is done with the temp file either way (shared,
                // saved elsewhere, or cancelled); delete it so it doesn't linger.
                if let url = exportFileURLPendingCleanup { try? FileManager.default.removeItem(at: url) }
                exportFileURLPendingCleanup = nil
            }) { file in ActivityShareSheet(items: [file.url]) }
            .alert("Delete everything?", isPresented: $showDeleteConfirm) {
                Button("Cancel", role: .cancel) {}
                Button("Delete everything", role: .destructive) { Task { await deleteEverything() } }
            } message: {
                Text("This removes your sessions, scores and notes from Mira's server and this phone. It cannot be undone.")
            }
            .onDisappear { speech.stop() }
    }
    private var rerun: some View {
        VStack(spacing: 24) {
            choice("Fix cameras only", detail: "About 2 minutes", selected: camerasOnly) { camerasOnly = true }
            choice("Full setup", detail: "Mat, cameras and body check · about 5 minutes", selected: !camerasOnly) { camerasOnly = false }
            Image("MascotIdle").resizable().scaledToFit().frame(width: 150, height: 167).padding(.top, 20).accessibilityHidden(true)
            Text("Mira will guide you one step at a time.").font(MiraUI.font(18)).foregroundStyle(MiraUI.soft)
            MiraButton(title: "Start setup") { showSetup = true }.padding(.top, 40)
        }
    }
    private var voice: some View {
        @Bindable var state = state
        return VStack(alignment: .leading, spacing: 20) {
            ForEach(["Warm", "Calm", "Bright"], id: \.self) { name in
                MiraCard {
                    HStack(spacing: 14) {
                        Button { state.preferences.voice = name } label: {
                            HStack(spacing: 14) {
                                radio(state.preferences.voice == name)
                                VStack(alignment: .leading, spacing: 4) { Text(name).font(MiraUI.font(20, "SemiBold")); Text(name == "Warm" ? "Friendly and gentle" : name == "Calm" ? "Slow and quiet" : "Cheerful and clear").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }.buttonStyle(.plain).accessibilityAddTraits(state.preferences.voice == name ? .isSelected : [])
                        Button { var preferences = state.preferences; preferences.voice = name; speech.read("Hi, I’m Mira. We’ll take this one step at a time.", preferences: preferences) } label: { HStack(spacing: 8) { FigmaIcon(screen: "voice", slot: "imgIconPlay", size: 16); Text("Hear").font(MiraUI.font(18, "SemiBold")) }.padding(12).overlay { Capsule().stroke(MiraUI.border, lineWidth: 1.5) } }.buttonStyle(.plain).accessibilityLabel("Hear \(name) voice")
                    }
                }.overlay { if state.preferences.voice == name { RoundedRectangle(cornerRadius: 28).stroke(MiraUI.ink, lineWidth: 2) } }
            }
            HStack { Text("Volume"); Spacer(); Text("\(Int((state.preferences.volume * 100).rounded()))%") }.font(MiraUI.font(20, "SemiBold")).padding(.top, 12)
            HStack { FigmaIcon(screen: "voice", slot: "imgIconLow"); MiraVolumeSlider(value: $state.preferences.volume); FigmaIcon(screen: "voice", slot: "imgIconSpeaker") }
            Text("Speaking speed").font(MiraUI.font(20, "SemiBold")).padding(.top, 12)
            MiraSegments(labels: ["Slower", "Normal"], selection: Binding(get: { state.preferences.slowerSpeech ? "Slower" : "Normal" }, set: { state.preferences.slowerSpeech = $0 == "Slower" }))
            Text("Applies to your phone and Mira's screen volume. Mira's device uses the clinic voice.").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
        }
        .onChange(of: state.preferences.voice) { _, _ in syncDevicePrefs() }
        .onChange(of: state.preferences.volume) { _, _ in syncDevicePrefs() }
        .onChange(of: state.preferences.slowerSpeech) { _, _ in syncDevicePrefs() }
    }
    private var captions: some View {
        @Bindable var state = state
        return VStack(alignment: .leading, spacing: 24) {
            MiraCard {
                VStack(spacing: 14) {
                    settingToggle("Show captions", detail: "Words appear for everything Mira says", value: $state.preferences.showCaptions)
                    Divider()
                    settingToggle("On Mira’s screen too", detail: "Shown under the mascot", value: $state.preferences.deviceCaptions)
                }
            }
            Text("Text size").font(MiraUI.font(20, "SemiBold"))
            HStack(spacing: 8) {
                captionSize("Large", large: false)
                captionSize("Extra large", large: true)
            }
            MiraCard {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Preview").font(MiraUI.font(17)).foregroundStyle(MiraUI.accent)
                    if state.preferences.showCaptions { Text("“Push down through your right heel.”").font(MiraUI.font(state.preferences.extraLargeCaptions ? 26 : 20, "Medium")).foregroundStyle(.white).padding(18).frame(maxWidth: .infinity, alignment: .leading).background(MiraUI.charcoal, in: RoundedRectangle(cornerRadius: 20)) }
                    else { Text("Captions are off").font(MiraUI.font(18)).foregroundStyle(MiraUI.soft) }
                }
            }
        }
        .onChange(of: state.preferences.deviceCaptions) { _, _ in syncDevicePrefs() }
    }
    private func captionSize(_ label: String, large: Bool) -> some View {
        let selected = state.preferences.extraLargeCaptions == large
        return Button { state.preferences.extraLargeCaptions = large } label: {
            VStack(spacing: 10) { Text("Aa").font(MiraUI.font(large ? 36 : 28, "SemiBold")); Text(label).font(MiraUI.font(17)) }.frame(maxWidth: .infinity, minHeight: 100).foregroundStyle(selected ? .white : MiraUI.ink).background(selected ? MiraUI.charcoal : .white, in: RoundedRectangle(cornerRadius: 22)).overlay { RoundedRectangle(cornerRadius: 22).stroke(selected ? MiraUI.charcoal : MiraUI.border, lineWidth: 1.5) }
        }.buttonStyle(.plain).accessibilityAddTraits(selected ? .isSelected : [])
    }
    private var reminders: some View {
        @Bindable var state = state
        return VStack(alignment: .leading, spacing: 22) {
            MiraCard { settingToggle("Daily reminder", detail: "Mira reminds you to do your exercises", value: $state.preferences.dailyReminder) }
            MiraField(label: "Time") {
                if changeTime {
                    DatePicker("Reminder time", selection: Binding(get: { Calendar.current.date(from: DateComponents(hour: state.preferences.reminderHour, minute: state.preferences.reminderMinute)) ?? Date() }, set: { let parts = Calendar.current.dateComponents([.hour, .minute], from: $0); state.preferences.reminderHour = parts.hour ?? 9; state.preferences.reminderMinute = parts.minute ?? 0 }), displayedComponents: .hourAndMinute).labelsHidden()
                    Button("Done") { changeTime = false }.font(MiraUI.font(17)).foregroundStyle(MiraUI.accent)
                } else {
                    HStack { FigmaIcon(screen: "reminders", slot: "imgIconClock"); Text(timeLabel); Spacer(); Button("Change") { changeTime = true }.font(MiraUI.font(17, "Medium")).foregroundStyle(MiraUI.accent) }
                }
            }
            Text("Days").font(MiraUI.font(20, "SemiBold"))
            HStack(spacing: 6) {
                ForEach(Array(["M", "T", "W", "T", "F", "S", "S"].enumerated()), id: \.offset) { index, label in
                    let weekday = (index + 1) % 7 + 1
                    let selected = state.preferences.reminderDays.contains(weekday)
                    Button { if selected { state.preferences.reminderDays.removeAll { $0 == weekday } } else { state.preferences.reminderDays.append(weekday) } } label: { Text(label).font(MiraUI.font(18, "SemiBold")).frame(maxWidth: .infinity, minHeight: 44).foregroundStyle(selected ? .white : MiraUI.ink).background(selected ? MiraUI.charcoal : .white, in: Capsule()).overlay { Capsule().stroke(selected ? MiraUI.charcoal : MiraUI.border) } }.buttonStyle(.plain).accessibilityLabel(Calendar.current.weekdaySymbols[weekday-1]).accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            Text("Remind me with").font(MiraUI.font(20, "SemiBold"))
            MiraCard {
                VStack(spacing: 16) {
                    settingToggle("Phone notification", value: $state.preferences.phoneReminder)
                    Divider(); settingToggle("Mira says hello", detail: "Coming with the next Mira device update.", value: $state.preferences.deviceReminder).disabled(true)
                    Divider(); settingToggle("Apple Watch tap", value: $state.preferences.watchReminder).disabled(!state.watchPaired)
                }
            }
            Text("Reminders arrive on this phone at the time you choose.").font(MiraUI.font(16)).foregroundStyle(MiraUI.soft)
        }
    }
    private var timeLabel: String { (Calendar.current.date(from: DateComponents(hour: state.preferences.reminderHour, minute: state.preferences.reminderMinute)) ?? Date()).formatted(date: .omitted, time: .shortened) }
    private var privacy: some View {
        VStack(spacing: 18) {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 12) { FigmaIcon(screen: "privacy", slot: "imgIconLock").padding(10).background(.white, in: Circle()); Text("No video leaves your home").font(MiraUI.font(20, "SemiBold")) }
                Text("Mira is designed to share skeleton numbers: angles, reps, balance and your ratings. Never your video.").font(MiraUI.font(18)).foregroundStyle(MiraUI.soft)
            }.padding(20).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 28))
            MiraCard { Toggle(isOn: preview ? .constant(true) : shareBinding) { VStack(alignment: .leading, spacing: 6) { Text(preview ? "Share with Dr. Lee" : "Share with your therapist").font(MiraUI.font(20, "SemiBold")); Text("After each session").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) } }.tint(MiraUI.accent).disabled(preview) }
            if let shareError { Text(shareError).font(MiraUI.font(15)).foregroundStyle(.red) }
            MiraButton(title: isExporting ? "Preparing…" : "Download my data", secondary: true, icon: ("privacy", "imgIconDownload")) { Task { await downloadData() } }.disabled(isExporting)
            if let exportError { Text(exportError).font(MiraUI.font(15)).foregroundStyle(.red) }
            NavigationLink { SettingsDetailView(screen: .delete) } label: { HStack { FigmaIcon(screen: "privacy", slot: "imgIconTrash"); Text("Delete all my data").font(MiraUI.font(19, "SemiBold")) }.foregroundStyle(MiraUI.ink).frame(maxWidth: .infinity, minHeight: 64).background(.white, in: Capsule()).overlay { Capsule().stroke(MiraUI.border, lineWidth: 1.5) } }.buttonStyle(.plain)
        }
    }
    private var shareBinding: Binding<Bool> {
        Binding(get: { shareOverride ?? state.me?.patient.shareByDefault ?? false },
                set: { newValue in
                    let previous = shareOverride ?? state.me?.patient.shareByDefault ?? false
                    shareOverride = newValue
                    shareError = nil
                    Task {
                        do {
                            try await state.updateMe(MeUpdate(shareByDefault: newValue))
                            shareOverride = nil
                        } catch {
                            shareOverride = previous
                            shareError = "Couldn't save. Try again."
                        }
                    }
                })
    }
    private func downloadData() async {
        guard let client = state.apiClient else { exportError = APIError.invalidAddress.localizedDescription; return }
        isExporting = true
        exportError = nil
        defer { isExporting = false }
        do {
            let data = try await client.exportMe()
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd"
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("mira-export-\(formatter.string(from: Date())).json")
            try data.write(to: url, options: .atomic)
            exportFileURLPendingCleanup = url
            exportFile = ExportFile(url: url)
        } catch {
            exportError = error.localizedDescription
        }
    }
    private var delete: some View {
        VStack(alignment: .leading, spacing: 24) {
            Text("Deleting removes your sessions, scores and notes from Mira's server and this phone.").font(MiraUI.font(20)).lineSpacing(4)
            Text("Your therapist keeps the plan they prescribed.").font(MiraUI.font(18)).foregroundStyle(MiraUI.soft)
            if let deleteError { Text(deleteError).font(MiraUI.font(16)).foregroundStyle(.red) }
            MiraButton(title: "Keep my data") { dismiss() }.padding(.top, 80)
            MiraButton(title: isDeleting ? "Deleting…" : "Delete everything", secondary: true, icon: ("delete", "imgIconWarn")) { showDeleteConfirm = true }.disabled(isDeleting)
        }
    }
    private func deleteEverything() async {
        guard let client = state.apiClient else { deleteError = APIError.invalidAddress.localizedDescription; return }
        isDeleting = true
        deleteError = nil
        defer { isDeleting = false }
        do {
            try await client.deleteMe()
            state.wipeLocalData()
            dismiss()
        } catch {
            deleteError = error.localizedDescription
        }
    }
    /// Debounces voice/caption edits before pushing them to `/me`'s `device_prefs`.
    private func syncDevicePrefs() {
        devicePrefsTask?.cancel()
        let prefs = state.preferences
        devicePrefsTask = Task {
            try? await Task.sleep(for: .milliseconds(600))
            guard !Task.isCancelled else { return }
            let body = MeUpdate(devicePrefs: .object([
                "voice": .string(prefs.voice),
                "volume": .number(prefs.volume),
                "slower_speech": .bool(prefs.slowerSpeech),
                "captions": .bool(prefs.deviceCaptions)
            ]))
            try? await state.updateMe(body)
        }
    }
    private func settingToggle(_ title: String, detail: String? = nil, value: Binding<Bool>) -> some View {
        Toggle(isOn: value) { VStack(alignment: .leading, spacing: 6) { Text(title).font(MiraUI.font(20, "SemiBold")); if let detail { Text(detail).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) } } }.tint(MiraUI.accent)
    }
    private func radio(_ selected: Bool) -> some View { Circle().stroke(selected ? MiraUI.ink : MiraUI.border, lineWidth: 2).frame(width: 26, height: 26).overlay { if selected { Circle().fill(MiraUI.accent).padding(6) } }.accessibilityHidden(true) }
    private func choice(_ title: String, detail: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) { MiraCard { HStack(spacing: 14) { radio(selected); VStack(alignment: .leading, spacing: 6) { Text(title).font(MiraUI.font(20, "SemiBold")); Text(detail).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) } } }.overlay { if selected { RoundedRectangle(cornerRadius: 28).stroke(MiraUI.ink, lineWidth: 2) } } }.buttonStyle(.plain).accessibilityAddTraits(selected ? .isSelected : [])
    }
}

#if DEBUG
private struct NetworkDiagnosticsView: View {
    @Environment(AppState.self) private var state
    @State private var address = ""
    @State private var token = ""
    var body: some View {
        Form {
            Section("Developer · Mira server") {
                TextField("http://laptop.local:8000", text: $address).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    .accessibilityIdentifier("serverAddress")
                Button("Use demo server (Vultr)") { address = AppState.demoServerAddress }
                SecureField(state.isPaired ? "Token saved — enter to replace" : "Pairing token (MIRA_API_TOKEN)", text: $token)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("serverToken")
                if let hint = state.tokenHint { LabeledContent("Token", value: hint) }
                Text(state.patientID.map { "Patient: \($0)" } ?? "Not paired to a patient")
                Button("Save & connect") {
                    if state.connect(address: address, token: token) { address = state.serverAddress }
                    token = ""
                }
                Text(state.liveSession.connectionState.label).accessibilityIdentifier("connectionStatus")
                if let error = state.configurationError { Text(error).foregroundStyle(.red) }
                if state.connectionEnabled { Button("Disconnect") { state.disconnect() } }
                if state.isPaired { Button("Forget token", role: .destructive) { state.forgetToken() } }
            }
            Section("Connection check") {
                Button(state.isTestingREST ? "Testing…" : "Test REST connection") { Task { await state.testREST() } }.disabled(state.isTestingREST || address != state.serverAddress)
                if let status = state.restStatus { Text(status) }
                LabeledContent("Live stream", value: state.liveSession.liveSocket.state.label)
                LabeledContent("Events", value: state.liveSession.eventSocket.state.label)
                LabeledContent("Pose", value: state.liveSession.poseSocket.state.label)
                LabeledContent("Messages received", value: "\(state.liveSession.receivedMessages)")
                if let error = state.liveSession.decodingError { Text(error).foregroundStyle(.red) }
            }
        }.navigationTitle("Connection tools").onAppear { address = state.serverAddress }
    }
}
#endif

// Native-sized drag target with the track and thumb from the updated design.
private struct MiraVolumeSlider: View {
    @Binding var value: Double
    var body: some View {
        GeometryReader { geometry in
            let travel = max(1, geometry.size.width - 44)
            let position = CGFloat(min(1, max(0, value))) * travel
            ZStack(alignment: .leading) {
                Capsule().fill(MiraUI.mist).frame(height: 12)
                    .overlay { Capsule().stroke(MiraUI.border, lineWidth: 1.5) }
                Capsule().fill(MiraUI.accent).frame(width: position + 22, height: 10)
                Circle().fill(.white).overlay { Circle().stroke(MiraUI.ink, lineWidth: 2) }
                    .frame(width: 44, height: 44).offset(x: position)
            }.frame(height: 44).contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0).onChanged { gesture in
                    value = Double(min(1, max(0, (gesture.location.x - 22) / travel)))
                })
        }.frame(height: 44)
            .accessibilityElement().accessibilityLabel("Mira voice volume")
            .accessibilityValue("\(Int((value * 100).rounded())) percent")
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: value = min(1, value + 0.05)
                case .decrement: value = max(0, value - 0.05)
                @unknown default: break
                }
            }
    }
}
