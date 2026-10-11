import SwiftUI
import AVFoundation

struct CareView: View {
    @Environment(\.miraDesignPreview) private var preview
    @Environment(AppState.self) private var state

    private static let nextVisitFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEE, MMM d · h:mm a"
        return formatter
    }()

    private var care: CareResponse? { state.store.care }

    private var initials: String {
        if preview { return "SL" }
        guard let name = care?.therapist?.name, !name.isEmpty else { return "PT" }
        let letters = name.split(separator: " ").compactMap { $0.first }.prefix(2)
        return letters.isEmpty ? "PT" : String(letters).uppercased()
    }

    private var therapistName: String {
        preview ? "Dr. Sam Lee, PT" : (care?.therapist?.name ?? "Your therapist")
    }

    private var clinicLine: String {
        if preview { return "Your therapist · Lakeside Rehab" }
        guard let therapist = care?.therapist else { return "Link your therapist to see your care team." }
        if let clinic = therapist.clinic, !clinic.isEmpty { return "Your therapist · \(clinic)" }
        return "Your therapist"
    }

    private var nextVisitText: String {
        if preview { return "Tue, Oct 20 · 10:30 AM" }
        guard let raw = care?.nextVisitAt, let date = WireDates.parse(raw) else { return "Not scheduled yet" }
        return Self.nextVisitFormatter.string(from: date)
    }

    /// Digits (and a leading `+`) suitable for a `tel:` URL; `nil` disables Call,
    /// including in the design preview (no real phone to dial there).
    private var phoneDigits: String? {
        guard !preview, let phone = care?.therapist?.phone else { return nil }
        let digits = phone.filter { $0.isNumber || $0 == "+" }
        return digits.isEmpty ? nil : digits
    }

    var body: some View {
        MiraPage {
            MiraScreenHeading(title: "Your care", detail: "Your therapist and help in one place.", mascot: true)
            MiraCard {
                VStack(spacing: 20) {
                    HStack(spacing: 16) {
                        Text(initials).font(MiraUI.font(22, "SemiBold")).foregroundStyle(Color("MiraWelcomeTitleStart")).frame(width: 64, height: 64).background(Color(red: 0.93, green: 0.8, blue: 0.83), in: Circle())
                        VStack(alignment: .leading, spacing: 6) {
                            Text(therapistName).font(MiraUI.font(22, "SemiBold"))
                            Text(clinicLine).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                    HStack(spacing: 14) {
                        FigmaIcon(screen: "care", slot: "imgIconCalendar").padding(12).background(.white, in: Circle())
                        VStack(alignment: .leading, spacing: 4) { Text("Next visit").foregroundStyle(MiraUI.soft); Text(nextVisitText).font(MiraUI.font(20, "SemiBold")) }
                        Spacer(minLength: 0)
                    }.padding(16).background(MiraUI.mist, in: RoundedRectangle(cornerRadius: 20))
                    HStack(spacing: 12) {
                        MiraButton(title: "Call", secondary: true, icon: ("care", "imgIconPhone")) {
                            if let digits = phoneDigits, let url = URL(string: "tel:\(digits)") {
                                UIApplication.shared.open(url)
                            }
                        }.disabled(phoneDigits == nil)
                        NavigationLink { CareDetailView(screen: .messages) } label: { Text("Message").font(MiraUI.font(20, "SemiBold")).foregroundStyle(.white).frame(maxWidth: .infinity, minHeight: 56).background(MiraUI.charcoal, in: Capsule()) }.buttonStyle(.plain)
                    }
                    if phoneDigits == nil { Text("Calling is available after your therapist’s contact details are connected.").font(MiraUI.font(14)).foregroundStyle(MiraUI.soft) }
                }
            }
            MiraCard(padding: 16) {
                VStack(spacing: 0) {
                    ForEach(Array(CareDetailView.Screen.allCases.enumerated()), id: \.element) { index, screen in
                        NavigationLink { CareDetailView(screen: screen) } label: { MiraMenuRow(title: screen.rowTitle, screen: "care", icon: screen.icon, index: index) }.buttonStyle(.plain)
                        if index < 4 { Divider().padding(.leading, 58) }
                    }
                }
            }
        }
        .task {
            guard !preview else { return }
            await state.refreshMe()
            if let api = state.apiClient { await state.store.refreshMessages(api: api) }
        }
        .navigationTitle("").toolbar(.hidden, for: .navigationBar)
    }
}

struct CareDetailView: View {
    enum Screen: String, CaseIterable { case visit, notes, messages, videos, contact
        var title: String { switch self { case .visit: "For your next visit"; case .notes: "Notes from Dr. Lee"; case .messages: "Messages"; case .videos: "How-to videos"; case .contact: "Emergency contact" } }
        var rowTitle: String { self == .notes ? "Notes from your therapist" : title }
        var icon: String { switch self { case .visit: "imgIconStar"; case .notes: "imgIconNote"; case .messages: "imgIconMsg1"; case .videos: "imgIconVideo"; case .contact: "imgIconShield" } }
    }
    let screen: Screen
    @Environment(\.miraDesignPreview) private var preview
    @Environment(AppState.self) private var state
    @State private var message = ""
    @State private var sending = false
    @State private var speech = MiraSpeech()

    /// Highlight kinds that read as a warning (red border, alert color), matching
    /// the therapist-facing analysis vocabulary.
    private static let warningKinds: Set<String> = ["asymmetry_worse", "compensation", "pain"]
    private static let shortDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        return formatter
    }()

    var body: some View {
        Group {
            if screen == .contact { EmergencyContactView() }
            else {
                MiraPage {
                    MiraScreenHeading(title: screen == .notes && !preview ? "Notes from your therapist" : screen.title,
                                      detail: screen == .visit ? (preview ? "Tue, Oct 20 with Dr. Lee. Mira will share these with them." : "Your visit summary will appear after your care plan is connected.") : screen == .notes ? "Short notes after your therapist checks your sessions." : screen == .videos ? (preview ? "Short videos from Dr. Lee. All have captions." : "Exercise videos from your care team will appear here.") : nil)
                    if preview {
                        switch screen { case .visit: visit; case .notes: notes; case .messages: messages; case .videos: videos; case .contact: EmptyView() }
                    } else {
                        switch screen {
                        case .visit: realVisit
                        case .notes: realNotes
                        case .messages: realMessages
                        case .videos: MiraPendingCard(title: "Waiting for your care team", detail: "Link your therapist to receive videos.")
                        case .contact: EmptyView()
                        }
                    }
                }
            }
        }.navigationTitle("").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Text("Care").font(MiraUI.font(16)).foregroundStyle(MiraUI.soft) } }
            .safeAreaInset(edge: .bottom) {
                if screen == .messages {
                    VStack(alignment: .leading, spacing: 10) {
                        if preview { Text("Messaging isn’t connected yet. Your text stays on this screen.").font(MiraUI.font(14)).foregroundStyle(MiraUI.soft) }
                        HStack(spacing: 12) {
                            TextField("Write a message", text: $message, axis: .vertical).font(MiraUI.font(18)).lineLimit(1...4).padding(18).background(.white, in: Capsule()).overlay { Capsule().stroke(MiraUI.border) }
                            Button { send() } label: { FigmaIcon(screen: "messages", slot: "imgIconSend", size: 24) }.buttonStyle(.borderedProminent).buttonBorderShape(.circle).controlSize(.large).disabled(preview || sending || message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty).accessibilityLabel("Send message")
                        }
                    }.padding(24).background(Color("MiraWelcomeEdge"))
                }
            }.onDisappear { speech.stop() }
    }

    private func send() {
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending, let api = state.apiClient else { return }
        message = ""
        sending = true
        Task {
            defer { sending = false }
            try? await state.store.send(text, api: api)
        }
    }

    // MARK: - Preview content (unchanged)

    private var visit: some View {
        VStack(spacing: 12) {
            visitCard("Knee bend up 22°", detail: "From 62° on Sep 22 to 84° today.", icon: "imgIconUp")
            visitCard("Leaning onto your left leg", detail: "Mostly in the last set of squats. Ask Dr. Lee how to fix it.", icon: "imgIconWarn", warning: true)
            visitCard("Pain stayed low", detail: "3 out of 10 or less after every session.", icon: "imgIconCheck")
        }
    }
    private func visitCard(_ title: String, detail: String? = nil, icon: String, warning: Bool = false) -> some View {
        MiraCard {
            HStack(alignment: .top, spacing: 16) {
                FigmaIcon(screen: "visit", slot: icon).padding(10).background(warning ? .clear : MiraUI.mist, in: Circle())
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(MiraUI.font(20, "SemiBold")).foregroundStyle(warning ? MiraUI.alert : MiraUI.ink)
                    if let detail { Text(detail).font(MiraUI.font(18)).foregroundStyle(MiraUI.soft) }
                }
            }
        }.overlay { if warning { RoundedRectangle(cornerRadius: 28).stroke(MiraUI.alert, lineWidth: 1.5) } }
    }
    private var notes: some View {
        VStack(spacing: 14) {
            ForEach(Array([("Oct 8", "Great progress on your knee bend! This week, push down through your right heel so both legs share the work."), ("Oct 1", "Well done on 7 days in a row. Add a second set of heel slides."), ("Sep 24", "Start slowly. Stop if pain goes above 6.")].enumerated()), id: \.offset) { index, note in
                MiraCard {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack { Text(note.0).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft); Spacer(); if index == 0 { Text("New").font(MiraUI.font(16, "SemiBold")).foregroundStyle(.white).padding(.horizontal, 14).padding(.vertical, 4).background(MiraUI.charcoal, in: Capsule()) } }
                        Text("“\(note.1)”").font(MiraUI.font(20)).lineSpacing(4)
                        if index == 0 { Button { speech.read(note.1, preferences: state.preferences) } label: { HStack { FigmaIcon(screen: "notes", slot: "imgIconSpeaker"); Text("Mira reads it").font(MiraUI.font(19, "SemiBold")) }.foregroundStyle(MiraUI.accent).padding(16).background(MiraUI.mist, in: Capsule()) }.buttonStyle(.plain) }
                    }
                }
            }
        }
    }
    private var messages: some View {
        VStack(spacing: 14) {
            bubble("Hi Maya, I saw you leaned left on Tuesday. How is the knee feeling?", author: "Dr. Lee · Oct 9")
            bubble("A little stiff in the morning, but better after the exercises.", author: "You · Oct 9", mine: true)
            bubble("That is normal. Keep going, and try the heel tip in my note.", author: "Dr. Lee · Today")
        }
    }
    private func bubble(_ text: String, author: String, mine: Bool = false) -> some View {
        HStack { if mine { Spacer(minLength: 60) }; VStack(alignment: .leading, spacing: 8) { Text(text).font(MiraUI.font(20)); Text(author).font(MiraUI.font(16)).opacity(0.75) }.padding(18).foregroundStyle(mine ? .white : MiraUI.ink).background(mine ? MiraUI.charcoal : .white, in: RoundedRectangle(cornerRadius: 24)); if !mine { Spacer(minLength: 60) } }
    }
    private var videos: some View {
        VStack(spacing: 14) {
            ForEach(Array([("Keeping weight even in squats", "2 min"), ("Heel slides", "1 min"), ("Setting up your cameras", "3 min")].enumerated()), id: \.offset) { index, video in
                MiraCard(padding: 12) {
                    if index == 0 {
                        VStack(alignment: .leading, spacing: 14) {
                            videoThumbnail(index: index).frame(height: 186)
                            HStack(alignment: .top) { Text(video.0).font(MiraUI.font(20, "SemiBold")); Spacer(); Text(video.1).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
                            Text("From Dr. Lee’s note").font(MiraUI.font(17)).foregroundStyle(MiraUI.accent)
                        }
                    } else {
                        HStack(spacing: 16) {
                            videoThumbnail(index: index).frame(width: 128, height: 100)
                            VStack(alignment: .leading, spacing: 10) { Text(video.0).font(MiraUI.font(20, "SemiBold")); HStack { Text(video.1); Text("CC").padding(4).overlay { RoundedRectangle(cornerRadius: 6).stroke(MiraUI.border) } }.font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
                        }
                    }
                }
            }
            Text("Video playback is waiting for video links from your care team.").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft)
        }
    }
    private func videoThumbnail(index: Int) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 20).fill(index == 0 ? Color(red: 0.97, green: 0.88, blue: 0.8) : index == 1 ? MiraUI.mist : Color(red: 0.93, green: 0.8, blue: 0.83))
            GeometryReader { geometry in
                let width = geometry.size.width
                Image(FigmaAssets.name("videos", index == 0 ? "imgGlow" : "imgGlow1")).resizable().frame(width: width * 0.9, height: width * 0.9).offset(x: width * 0.3, y: -width * 0.35)
            }.clipShape(RoundedRectangle(cornerRadius: 20)).accessibilityHidden(true)
            FigmaIcon(screen: "videos", slot: "imgIconPlay", size: 24).padding(index == 0 ? 20 : 12).background(MiraUI.charcoal, in: Circle())
            if index == 0 { Text("CC").font(MiraUI.font(16, "SemiBold")).padding(8).background(.white, in: RoundedRectangle(cornerRadius: 8)).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading).padding(12) }
        }.accessibilityLabel("Video preview, playback unavailable")
    }

    // MARK: - Real content

    @ViewBuilder private var realVisit: some View {
        let highlights = state.store.care?.highlights ?? []
        if highlights.isEmpty {
            MiraPendingCard(title: "Waiting for your care team", detail: "Link your therapist to receive visit information.")
        } else {
            VStack(spacing: 12) {
                ForEach(Array(highlights.enumerated()), id: \.offset) { _, highlight in
                    let warning = Self.warningKinds.contains(highlight.kind ?? "")
                    let detail = highlight.time.flatMap(WireDates.parse).map(Self.shortDateFormatter.string)
                    visitCard(highlight.text, detail: detail, icon: warning ? "imgIconWarn" : "imgIconCheck", warning: warning)
                }
            }
        }
    }

    @ViewBuilder private var realNotes: some View {
        let notes = (state.store.care?.notes ?? []).sorted { (WireDates.parse($0.createdAt) ?? .distantPast) > (WireDates.parse($1.createdAt) ?? .distantPast) }
        if notes.isEmpty {
            MiraPendingCard(title: "Waiting for your care team", detail: "Link your therapist to receive notes.")
        } else {
            VStack(spacing: 14) {
                ForEach(Array(notes.enumerated()), id: \.element.id) { index, note in
                    MiraCard {
                        VStack(alignment: .leading, spacing: 14) {
                            HStack {
                                Text(WireDates.parse(note.createdAt).map(Self.shortDateFormatter.string) ?? "").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                                Spacer()
                                if note.readAt == nil { Text("New").font(MiraUI.font(16, "SemiBold")).foregroundStyle(.white).padding(.horizontal, 14).padding(.vertical, 4).background(MiraUI.charcoal, in: Capsule()) }
                            }
                            Text("“\(note.body)”").font(MiraUI.font(20)).lineSpacing(4)
                            if index == 0 {
                                Button { speech.read(note.body, preferences: state.preferences) } label: {
                                    HStack { FigmaIcon(screen: "notes", slot: "imgIconSpeaker"); Text("Mira reads it").font(MiraUI.font(19, "SemiBold")) }
                                        .foregroundStyle(MiraUI.accent).padding(16).background(MiraUI.mist, in: Capsule())
                                }.buttonStyle(.plain)
                            }
                        }
                    }
                }
            }
        }
    }

    private func authorLine(_ note: CareMessage) -> String {
        let date = WireDates.parse(note.createdAt).map(Self.shortDateFormatter.string) ?? ""
        let name = note.sender == "patient" ? "You" : (note.sender == "therapist" ? (state.store.care?.therapist?.name ?? "Your therapist") : "Mira")
        return date.isEmpty ? name : "\(name) · \(date)"
    }

    private var realMessages: some View {
        ScrollViewReader { proxy in
            VStack(spacing: 14) {
                ForEach(state.store.messages) { msg in
                    bubble(msg.body, author: authorLine(msg), mine: msg.sender == "patient").id(msg.id)
                }
            }
            .onChange(of: state.store.messages.count) { _, _ in
                if let last = state.store.messages.last?.id {
                    withAnimation { proxy.scrollTo(last, anchor: .bottom) }
                }
            }
            .onAppear {
                if let last = state.store.messages.last?.id { proxy.scrollTo(last, anchor: .bottom) }
            }
        }
    }
}

struct EmergencyContactView: View {
    var onboarding = false
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    @State private var name = ""
    @State private var relationship = ""
    @State private var phone = ""
    @State private var alertTherapist = false
    @State private var editing: String?
    @State private var saved = false
    @State private var loaded = false
    @State private var testAlertNote: String?
    @FocusState private var focus: String?
    var body: some View {
        MiraPage(inset: onboarding ? 32 : 24, top: onboarding ? 28 : 12) {
            if onboarding {
                MiraIntro(eyebrow: "YOUR SAFETY", title: "Emergency contact", detail: preview ? "If you fall and do not answer, Mira\ncalls this person and tells Dr. Lee." : "Choose who Mira should contact in an emergency.")
            } else {
                MiraScreenHeading(title: "Emergency contact", detail: state.isLinked ? "Choose who Mira should contact in an emergency. Mira calls this person if you fall and don’t respond." : "Choose who Mira should contact in an emergency. Calls and alerts are not active yet.")
            }
            contactField("Name", value: $name)
            contactField("Relationship", value: $relationship)
            contactField("Phone number", value: $phone)
            MiraCard {
                Toggle(isOn: $alertTherapist) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(preview ? "Also alert Dr. Lee" : "Also alert your therapist").font(MiraUI.font(20, "SemiBold"))
                        Text(preview ? "Mira sends them a message too." : (state.isLinked ? "Your therapist is notified when you need help." : "Your choice is saved. Alerts will work once connected."))
                            .font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    }
                }
                .tint(MiraUI.accent)
                .accessibilityIdentifier("emergencyAlertTherapist")
            }
            if onboarding && !preview { Text(hrLimitText).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
            if !onboarding {
                MiraButton(title: "Save contact") { focus = nil; save(); saved = true; syncRemote() }.disabled(!valid)
                MiraButton(title: "Send a test alert", secondary: true) { sendTestAlert() }.disabled(preview || !state.isLinked)
                if saved { Text(state.isLinked ? "Contact saved and sent to your care team." : "Contact saved on this phone. Alerts are not active yet.").font(MiraUI.font(16)).foregroundStyle(MiraUI.accent) }
                if let testAlertNote { Text(testAlertNote).font(MiraUI.font(16)).foregroundStyle(MiraUI.accent) }
            }
        }
        .overlay(alignment: .topTrailing) {
            if onboarding {
                VStack(alignment: .trailing, spacing: 18) {
                    FigmaIcon(screen: "profile", slot: "imgLcdHiFiSparkle", size: 20)
                    FigmaIcon(screen: "profile", slot: "imgLcdHiFiSparkle1", size: 12).padding(.trailing, 24)
                }.padding(.trailing, 58).padding(.top, 18).allowsHitTesting(false)
            }
        }.onAppear {
            let draft = state.onboarding
            if state.isLinked, let care = state.store.care {
                name = care.emergencyContact?.name ?? draft.emergencyContactName
                phone = care.emergencyContact?.phone ?? draft.emergencyContactPhone
                relationship = care.emergencyContact?.relationship ?? state.preferences.contactRelationship
                alertTherapist = care.alertTherapist
            } else {
                name = draft.emergencyContactName
                relationship = state.preferences.contactRelationship
                phone = draft.emergencyContactPhone
                alertTherapist = state.preferences.alertTherapist
            }
            loaded = true
        }.onChange(of: name) { _, _ in if onboarding && loaded { save(); syncRemote() } }.onChange(of: phone) { _, _ in if onboarding && loaded { save(); syncRemote() } }.onChange(of: relationship) { _, _ in if onboarding && loaded { save(); syncRemote() } }
            .onChange(of: alertTherapist) { _, value in
                if loaded { state.preferences.alertTherapist = value; if onboarding { syncRemote() } }
            }
    }
    private var hrLimitText: String {
        if let limit = state.store.care?.hrLimit { return "Heart-rate limit from your care team: \(limit) bpm" }
        return "Heart-rate limit: not provided by your care team yet."
    }
    private func contactField(_ label: String, value: Binding<String>) -> some View {
        MiraField(label: label) {
            HStack {
                if editing == label { TextField(label, text: value).focused($focus, equals: label).keyboardType(label == "Phone number" ? .phonePad : .default).textContentType(label == "Phone number" ? .telephoneNumber : label == "Name" ? .name : nil).onSubmit { editing = nil; focus = nil } }
                else { Text(value.wrappedValue.isEmpty ? "Add \(label.lowercased())" : value.wrappedValue).frame(maxWidth: .infinity, alignment: .leading) }
                Button { if editing == label { editing = nil; focus = nil } else { editing = label; focus = label } } label: {
                    if editing == label { Text("Done") }
                    else { FigmaIcon(screen: "contactPencil", slot: "imgVector", size: 16).frame(minWidth: 44, minHeight: 44) }
                }.font(MiraUI.font(17, "Medium")).foregroundStyle(MiraUI.accent).accessibilityLabel("\(editing == label ? "Finish editing" : "Edit") \(label.lowercased())")
            }
        }
    }
    private var valid: Bool { var draft = state.onboarding; draft.emergencyContactName = name; draft.emergencyContactPhone = phone; return draft.contactIsValid }
    private func save() { state.onboarding.emergencyContactName = name; state.onboarding.emergencyContactPhone = phone; state.preferences.contactRelationship = relationship; state.preferences.alertTherapist = alertTherapist }
    /// Pushes the contact/alert choice to the server once paired, matching `save()`'s
    /// local persistence; a best-effort background sync (errors stay silent here —
    /// the next `refreshMe()` will reconcile).
    private func syncRemote() {
        guard state.isLinked, valid else { return }
        let contact = PairContact(name: name, phone: phone, relationship: relationship.isEmpty ? nil : relationship)
        let body = MeUpdate(emergencyContact: contact, alertTherapist: alertTherapist)
        Task { try? await state.updateMe(body) }
    }
    private func sendTestAlert() {
        Task {
            do { testAlertNote = try await state.sendTestAlert().note }
            catch { testAlertNote = nil }
        }
    }
}

@MainActor final class MiraSpeech {
    private let synthesizer = AVSpeechSynthesizer()
    func read(_ text: String, preferences: MiraPreferences) {
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.volume = Float(preferences.volume)
        utterance.rate = preferences.slowerSpeech ? 0.4 : AVSpeechUtteranceDefaultSpeechRate
        utterance.pitchMultiplier = preferences.voice == "Bright" ? 1.15 : preferences.voice == "Calm" ? 0.9 : 1
        synthesizer.speak(utterance)
    }
    func stop() { synthesizer.stopSpeaking(at: .immediate) }
}
