import SwiftUI
import UIKit

struct OnboardingDesignedSteps: View {
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    @State private var editing: Measurement?
    @State private var customCondition = false
    @State private var editingName = false
    @FocusState private var focusedInput: Input?
    @ScaledMetric private var feetWidth = 30.0
    @ScaledMetric private var inchesWidth = 34.0
    @ScaledMetric private var poundsWidth = 72.0
    @State private var feet = ""
    @State private var inches = ""
    @State private var pounds = ""
    private enum Input: Hashable { case feet, inches, pounds, name }
    private enum Measurement: String, Identifiable { case height, weight; var id: String { rawValue } }

    var body: some View {
        MiraPage(inset: 32, top: 28) {
            switch state.onboarding.step {
            case .profile: profile
            case .therapist: therapist
            case .device: device
            default: EmptyView()
            }
        }
        .overlay(alignment: .topTrailing) {
            VStack(alignment: .trailing, spacing: 18) {
                FigmaIcon(screen: "profile", slot: "imgLcdHiFiSparkle", size: 20)
                FigmaIcon(screen: "profile", slot: "imgLcdHiFiSparkle1", size: 12).padding(.trailing, 24)
            }.padding(.trailing, 58).padding(.top, 18).allowsHitTesting(false)
        }
        .sheet(isPresented: $customCondition) {
            NavigationStack {
                Form {
                    @Bindable var state = state
                    TextField("Condition or recovery focus", text: $state.onboarding.condition, axis: .vertical).lineLimit(3...5)
                }
                .navigationTitle("Your condition")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { customCondition = false } } }
            }
        }
    }

    private var profile: some View {
        VStack(alignment: .leading, spacing: 26) {
            MiraIntro(eyebrow: "ABOUT YOU", title: "Tell Mira about you", detail: "So Mira can measure you correctly.")
            MiraField(label: "Your name") {
                HStack {
                    if editingName {
                        TextField("Your name", text: Binding(get: { state.onboarding.patientName ?? "" }, set: { state.onboarding.patientName = $0 }))
                            .textContentType(.givenName).textInputAutocapitalization(.words)
                            .focused($focusedInput, equals: .name)
                            .onSubmit { editingName = false; focusedInput = nil }
                            .accessibilityIdentifier("profileName")
                    } else {
                        Text((state.onboarding.patientName ?? "").isEmpty ? "Add your name" : state.onboarding.patientName!)
                        Spacer(minLength: 0)
                    }
                    Button {
                        editingName.toggle()
                        focusedInput = editingName ? .name : nil
                    } label: {
                        if editingName { Text("Done") }
                        else { FigmaIcon(screen: "profile", slot: "imgVector", size: 16).frame(minWidth: 44, minHeight: 44) }
                    }.font(MiraUI.font(17, "Medium")).foregroundStyle(MiraUI.accent)
                        .accessibilityLabel(editingName ? "Finish editing name" : "Edit name")
                }
            }
            // Expand inline while editing so feet/inches and Done have room.
            // AnyLayout keeps the field identity and focus while the axis changes.
            let measurements = editing == nil ? AnyLayout(HStackLayout(alignment: .top, spacing: 12)) : AnyLayout(VStackLayout(alignment: .leading, spacing: 26))
            measurements { heightField; weightField }
            if editing == .height && !heightInputIsValid {
                Text("Use whole numbers. Inches must be between 0 and 11.").font(MiraUI.font(14)).foregroundStyle(MiraUI.alert)
            }
            if editing == .weight && !weightInputIsValid {
                Text("Enter your weight in whole pounds.").font(MiraUI.font(14)).foregroundStyle(MiraUI.alert)
            }
            MiraField(label: "Condition") {
                Menu {
                    ForEach(["Knee replacement", "Knee recovery", "Hip replacement", "Back pain", "Balance training"], id: \.self) { condition in
                        Button(condition) { state.onboarding.condition = condition }
                    }
                    Button("Other / edit existing") { customCondition = true }
                } label: {
                    HStack {
                        Text(state.onboarding.condition.isEmpty ? "Choose your condition" : state.onboarding.condition).foregroundStyle(MiraUI.ink).lineLimit(2)
                        Spacer(); FigmaIcon(screen: "profile", slot: "imgVector1", size: 16).frame(minWidth: 44, minHeight: 44)
                    }
                }.accessibilityIdentifier("profileCondition")
            }
        }
    }

    private var heightField: some View {
        MiraField(label: "Height") {
            HStack(spacing: 8) {
                if editing == .height {
                    HStack(spacing: 6) {
                        TextField("0", text: measurementBinding(.feet)).keyboardType(.numberPad)
                            .focused($focusedInput, equals: .feet).frame(width: feetWidth).multilineTextAlignment(.trailing)
                            .accessibilityLabel("Height in feet").accessibilityIdentifier("profileHeightFeet")
                        Text("ft").foregroundStyle(MiraUI.soft)
                    }
                    HStack(spacing: 6) {
                        TextField("0", text: measurementBinding(.inches)).keyboardType(.numberPad)
                            .focused($focusedInput, equals: .inches).frame(width: inchesWidth).multilineTextAlignment(.trailing)
                            .accessibilityLabel("Height in inches").accessibilityIdentifier("profileHeightInches")
                        Text("in").foregroundStyle(MiraUI.soft)
                    }.padding(.leading, 8)
                } else { Text(heightDescription).lineLimit(1).minimumScaleFactor(0.8) }
                Spacer(minLength: 0)
                Button {
                    if editing == .height { focusedInput = nil; if heightInputIsValid { editing = nil } }
                    else { beginEditing(.height) }
                } label: {
                    if editing == .height { Text("Done") }
                    else { FigmaIcon(screen: "profile", slot: "imgVector1", size: 16).frame(minWidth: 32, minHeight: 44) }
                }.font(MiraUI.font(17, "Medium")).foregroundStyle(MiraUI.accent)
                    .accessibilityLabel(editing == .height ? "Finish editing height" : "Edit height")
                    .accessibilityIdentifier("profileHeightEdit")
            }
        }
    }
    private var weightField: some View {
        MiraField(label: "Weight") {
            HStack(spacing: 8) {
                if editing == .weight {
                    TextField("0", text: measurementBinding(.pounds)).keyboardType(.numberPad)
                        .focused($focusedInput, equals: .pounds).frame(width: poundsWidth).multilineTextAlignment(.trailing)
                        .accessibilityLabel("Weight in pounds").accessibilityIdentifier("profileWeightPounds")
                    Text("lbs").foregroundStyle(MiraUI.soft)
                } else { Text(weightDescription).lineLimit(1).minimumScaleFactor(0.8) }
                Spacer(minLength: 0)
                Button {
                    if editing == .weight { focusedInput = nil; if weightInputIsValid { editing = nil } }
                    else { beginEditing(.weight) }
                } label: {
                    if editing == .weight { Text("Done") }
                    else { FigmaIcon(screen: "profile", slot: "imgVector1", size: 16).frame(minWidth: 32, minHeight: 44) }
                }.font(MiraUI.font(17, "Medium")).foregroundStyle(MiraUI.accent)
                    .accessibilityLabel(editing == .weight ? "Finish editing weight" : "Edit weight")
                    .accessibilityIdentifier("profileWeightEdit")
            }
        }
    }

    private var therapist: some View {
        @Bindable var state = state
        return VStack(alignment: .leading, spacing: 24) {
            MiraIntro(eyebrow: "YOUR THERAPIST", title: "Link your therapist", detail: "Type the code your therapist\ngave you.")
            TherapistCodeInput(code: $state.onboarding.therapistCode)
                .frame(height: 72)
                .accessibilityIdentifier("therapistCode")
            Text("Letters and numbers, 6 in all.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
            MiraCard {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 16) {
                        if preview { FigmaIcon(screen: "therapist", slot: "imgIconCheckCircle", size: 30).padding(11).background(MiraUI.mist, in: Circle()) }
                        VStack(alignment: .leading, spacing: 6) {
                            Text(preview ? "Your plan is ready" : "Your plan will appear here").font(MiraUI.font(20, "SemiBold"))
                            Text(preview ? "From Dr. Sam Lee, PT" : "Therapist linking isn’t available yet.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                        }
                    }
                    Divider()
                    planRow("imgIconExercises", preview ? "3 exercises" : "Your exercises", preview ? "About 15 minutes a day" : "Prescribed by your therapist")
                    planRow("imgIconGoal", "Your goal", preview ? "Bend your knee further, to 110°" : "Targets from your care plan")
                    planRow("imgIconCheckin", "Weekly check-in", preview ? "Dr. Lee checks in each week" : "Your care team’s check-ins")
                }
            }
            if !preview { Text("You can save your code for later. This does not link a therapist or load a plan.").font(MiraUI.font(15)).foregroundStyle(MiraUI.soft) }
        }
    }
    private func planRow(_ icon: String, _ title: String, _ detail: String) -> some View {
        HStack(spacing: 14) {
            FigmaIcon(screen: "therapist", slot: icon, size: 32)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(MiraUI.font(17, "SemiBold"))
                Text(detail).font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
            }
        }
    }
    private var device: some View {
        VStack(alignment: .leading, spacing: 28) {
            MiraIntro(eyebrow: "YOUR MIRA", title: "Connect your Mira", detail: "Plug in Mira and set it\nin front of your mat.")
            FigmaIllustration(name: "device")
                .background(MiraUI.mist).clipShape(RoundedRectangle(cornerRadius: 32))
                .overlay { RoundedRectangle(cornerRadius: 32).stroke(.white) }
            MiraCard {
                HStack(spacing: 16) {
                    Image(systemName: "desktopcomputer").foregroundStyle(MiraUI.ink).padding(15).background(MiraUI.mist, in: Circle())
                    VStack(alignment: .leading, spacing: 4) {
                        Text(preview ? "Mira found" : "Mira setup pending").font(MiraUI.font(20, "SemiBold"))
                        Text(preview ? "Living room" : "Pairing is not available yet").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft)
                    }
                    Spacer()
                    if preview { FigmaIcon(screen: "device", slot: "imgIconCheckCircle", size: 32) }
                }
            }
            .overlay { RoundedRectangle(cornerRadius: 28).stroke(preview ? MiraUI.accent : .clear, lineWidth: 2) }
            if !preview { Text("You can finish your profile now and connect Mira later.").font(MiraUI.font(17)).foregroundStyle(MiraUI.soft) }
        }
    }
    private func beginEditing(_ measurement: Measurement) {
        editingName = false
        editing = measurement
        if measurement == .height {
            if let cm = state.onboarding.height, cm.isFinite, cm > 0, cm <= 300 {
                let total = Int((cm / 2.54).rounded())
                feet = String(total / 12)
                inches = String(total % 12)
            } else { feet = ""; inches = "" }
            focusedInput = .feet
        } else {
            if let kg = state.onboarding.bodyMass, kg.isFinite, kg > 0, kg <= 500 {
                pounds = String(Int((kg * 2.2046226218).rounded()))
            } else { pounds = "" }
            focusedInput = .pounds
        }
    }
    private var heightInCM: Double? {
        guard let ft = Int(feet), ft >= 0, ft <= 9,
              let inch = Int(inches), inch >= 0, inch < 12 else { return nil }
        let cm = Double(ft * 12 + inch) * 2.54
        return cm > 0 && cm <= 300 ? cm : nil
    }
    private var weightInKG: Double? {
        guard let lbs = Int(pounds), lbs > 0 else { return nil }
        let kg = Double(lbs) / 2.2046226218
        return kg <= 500 ? kg : nil
    }
    private var heightInputIsValid: Bool { heightInCM != nil }
    private var weightInputIsValid: Bool { weightInKG != nil }
    private func measurementBinding(_ input: Input) -> Binding<String> {
        Binding(get: {
            switch input {
            case .feet: return feet
            case .inches: return inches
            case .pounds: return pounds
            case .name: return state.onboarding.patientName ?? ""
            }
        }, set: { value in
            switch input {
            case .feet: feet = value
            case .inches: inches = value
            case .pounds: pounds = value
            case .name: state.onboarding.patientName = value
            }
            // Convert only after typing; opening Edit preserves the exact saved value.
            if input == .feet || input == .inches { state.onboarding.heightCM = heightInCM.map { String($0) } ?? "" }
            if input == .pounds { state.onboarding.bodyMassKG = weightInKG.map { String($0) } ?? "" }
        })
    }
    private var heightDescription: String { ProfileMeasurements.height(cm: state.onboarding.height) }
    private var weightDescription: String { ProfileMeasurements.weight(kg: state.onboarding.bodyMass) }
}

// User-facing units; persisted measurements retain the networking model's units.
enum ProfileMeasurements {
    static func height(cm: Double?) -> String {
        guard let cm, cm.isFinite, cm > 0, cm <= 300 else { return "Add height" }
        let total = Int((cm / 2.54).rounded())
        return "\(total / 12) ft \(total % 12) in"
    }
    static func weight(kg: Double?) -> String {
        guard let kg, kg.isFinite, kg > 0, kg <= 500 else { return "Add weight" }
        return "\(Int((kg * 2.2046226218).rounded())) lbs"
    }
}


// One native text input keeps paste, deletion and selection coherent. Its caret
// and hit-testing use the same six-cell geometry as the visible characters.
private struct TherapistCodeInput: UIViewRepresentable {
    @Binding var code: String
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIView(context: Context) -> CodeTextField {
        let field = CodeTextField()
        field.delegate = context.coordinator
        field.addTarget(context.coordinator, action: #selector(Coordinator.textChanged(_:)), for: .editingChanged)
        field.setCode(code)
        return field
    }
    func updateUIView(_ field: CodeTextField, context: Context) {
        context.coordinator.parent = self
        if field.text != code { field.setCode(code) }
    }
    final class Coordinator: NSObject, UITextFieldDelegate {
        var parent: TherapistCodeInput
        init(_ parent: TherapistCodeInput) { self.parent = parent }
        func textField(_ textField: UITextField, shouldChangeCharactersIn range: NSRange, replacementString string: String) -> Bool {
            guard let field = textField as? CodeTextField,
                  let swiftRange = Range(range, in: field.text ?? "") else { return false }
            let replacement = CodeTextField.clean(string)
            // Reject unsupported keystrokes rather than deleting selected text.
            if !string.isEmpty && replacement.isEmpty { return false }
            let updated = String((field.text ?? "").replacingCharacters(in: swiftRange, with: replacement).prefix(6))
            field.setCode(updated, cursor: min(range.location + replacement.count, updated.count))
            parent.code = updated
            return false
        }
        @objc func textChanged(_ field: CodeTextField) {
            let clean = CodeTextField.clean(field.text ?? "")
            if field.text != clean { field.setCode(clean, cursor: clean.count) }
            else { field.refreshCells() }
            parent.code = clean
        }
        func textFieldDidBeginEditing(_ textField: UITextField) { (textField as? CodeTextField)?.refreshCells() }
        func textFieldDidEndEditing(_ textField: UITextField) { (textField as? CodeTextField)?.refreshCells() }
        func textFieldDidChangeSelection(_ textField: UITextField) { (textField as? CodeTextField)?.refreshCells() }
        func textFieldShouldReturn(_ textField: UITextField) -> Bool { textField.resignFirstResponder(); return true }
    }
}

private final class CodeTextField: UITextField {
    private let cells = (0..<6).map { _ in UILabel() }
    private let spacing: CGFloat = 8
    override init(frame: CGRect) {
        super.init(frame: frame)
        textColor = .clear
        tintColor = UIColor(named: "MiraInk")
        font = UIFont(name: "Inter-Medium", size: 30)
        autocorrectionType = .no
        spellCheckingType = .no
        autocapitalizationType = .allCharacters
        keyboardType = .asciiCapable
        textContentType = .oneTimeCode
        returnKeyType = .done
        accessibilityLabel = "Therapist code, six letters and numbers"
        let stack = UIStackView(arrangedSubviews: cells)
        stack.axis = .horizontal; stack.distribution = .fillEqually; stack.spacing = spacing
        stack.isUserInteractionEnabled = false; stack.translatesAutoresizingMaskIntoConstraints = false
        insertSubview(stack, at: 0)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor), stack.trailingAnchor.constraint(equalTo: trailingAnchor),
            stack.topAnchor.constraint(equalTo: topAnchor), stack.bottomAnchor.constraint(equalTo: bottomAnchor)
        ])
        for cell in cells {
            cell.textAlignment = .center
            cell.font = font
            cell.textColor = UIColor(named: "MiraInk")
            cell.backgroundColor = .white
            cell.layer.cornerRadius = 18; cell.layer.borderWidth = 1.5; cell.clipsToBounds = true
            cell.isAccessibilityElement = false
        }
        setContentHuggingPriority(.defaultLow, for: .horizontal)
        setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    static func clean(_ value: String) -> String {
        String(value.uppercased().filter { $0.isASCII && ($0.isLetter || $0.isNumber) }.prefix(6))
    }
    func setCode(_ value: String, cursor: Int? = nil) {
        text = Self.clean(value)
        if let cursor, let position = position(from: beginningOfDocument, offset: min(cursor, (text ?? "").count)) {
            selectedTextRange = textRange(from: position, to: position)
        }
        refreshCells()
    }
    func refreshCells() {
        let characters = Array(text ?? "")
        let insertion = selectedTextRange.map { offset(from: beginningOfDocument, to: $0.start) } ?? characters.count
        for (index, cell) in cells.enumerated() {
            cell.text = index < characters.count ? String(characters[index]) : nil
            cell.layer.borderColor = (isFirstResponder && index == min(insertion, 5)
                ? UIColor(named: "MiraAccentTextSmall") ?? .systemGreen
                : UIColor(red: 133/255, green: 127/255, blue: 138/255, alpha: 1)).cgColor
        }
    }
    override func textRect(forBounds bounds: CGRect) -> CGRect { bounds }
    override func editingRect(forBounds bounds: CGRect) -> CGRect { bounds }
    override func caretRect(for position: UITextPosition) -> CGRect {
        let insertion = offset(from: beginningOfDocument, to: position)
        let width = max(0, (bounds.width - spacing * 5) / 6)
        let index = min(max(insertion, 0), 5)
        let center = CGFloat(index) * (width + spacing) + width / 2
        let characterWidth = ("M" as NSString).size(withAttributes: [.font: font ?? UIFont.systemFont(ofSize: 30)]).width
        let x = center + (insertion >= 6 ? characterWidth / 2 + 2 : insertion < (text ?? "").count ? -characterWidth / 2 - 2 : 0)
        return CGRect(x: x, y: (bounds.height - 34) / 2, width: 2, height: 34)
    }
    override func closestPosition(to point: CGPoint) -> UITextPosition? {
        let width = max(0, (bounds.width - spacing * 5) / 6)
        let index = min(max(Int(point.x / max(1, width + spacing)), 0), (text ?? "").count)
        return position(from: beginningOfDocument, offset: index)
    }
    override func selectionRects(for range: UITextRange) -> [UITextSelectionRect] { [] }
}

struct OnboardingReviewView: View {
    @Environment(AppState.self) private var state
    @Environment(\.miraDesignPreview) private var preview
    var onEdit: (OnboardingStep) -> Void
    var body: some View {
        MiraPage(inset: 32, top: 28) {
            MiraIntro(eyebrow: "ALMOST DONE", title: "Check your details", detail: "Tap Edit to change anything.", size: 36)
            reviewCard("About you", icon: "imgIconUser", step: .profile, color: MiraUI.peach) {
                row("Name", state.onboarding.patientName ?? "Add your name")
                row("Height", ProfileMeasurements.height(cm: state.onboarding.height))
                row("Weight", ProfileMeasurements.weight(kg: state.onboarding.bodyMass))
                row("Condition", state.onboarding.condition)
            }
            reviewCard("Emergency contact", icon: "imgIconShield", step: .emergencyContact, color: MiraUI.blush) {
                row("Name", state.onboarding.emergencyContactName.isEmpty ? "Add a contact" : contactName)
                row("Phone", state.onboarding.emergencyContactPhone.isEmpty ? "Add phone number" : state.onboarding.emergencyContactPhone)
                row(preview ? "Alert Dr. Lee" : "Therapist alerts", state.preferences.alertTherapist ? (preview ? "✓ On" : "On when connected") : "Off")
            }
            reviewCard("Your therapist", icon: "imgIconHeart", step: .therapist, color: MiraUI.blush) {
                row("Therapist", preview ? "Dr. Sam Lee, PT" : "Not linked yet")
                row("Plan", preview ? "3 exercises, 15 min a day" : "Waiting for your care team")
                row("Goal", preview ? "Bend knee to 110°" : "Not provided yet")
            }
            reviewCard("Your Mira", icon: "imgIconDevice", step: .device) {
                row("Status", preview ? "✓ Connected" : "Setup pending")
                if preview { row("Room", "Living room") }
            }
            MiraButton(title: "Yes, this is right") { state.advanceOnboarding() }.disabled(!state.onboarding.canFinish).accessibilityIdentifier("onboardingContinue")
            if !preview { Text("Your details are saved on this phone. Therapist linking, device pairing and emergency alerts still need to be connected.").font(MiraUI.font(16)).foregroundStyle(MiraUI.soft) }
        }
    }
    private var contactName: String {
        let relationship = state.preferences.contactRelationship.trimmingCharacters(in: .whitespacesAndNewlines)
        return state.onboarding.emergencyContactName + (relationship.isEmpty ? "" : " (\(relationship.lowercased()))")
    }
    private func reviewCard<Content: View>(_ title: String, icon: String, step: OnboardingStep, color: Color = MiraUI.mist, @ViewBuilder content: () -> Content) -> some View {
        MiraCard(padding: 17) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 12) {
                    FigmaIcon(screen: "review", slot: icon, size: 22).padding(9).background(color, in: Circle())
                    Text(title).font(MiraUI.font(18, "SemiBold"))
                    Spacer(minLength: 0)
                    Button { onEdit(step) } label: { HStack(spacing: 8) { FigmaIcon(screen: "review", slot: "imgIconPencil", size: 16); Text("Edit").font(MiraUI.font(16, "Medium")) }.foregroundStyle(MiraUI.accent).padding(.horizontal, 16).frame(minHeight: 46).overlay { Capsule().stroke(MiraUI.border, lineWidth: 1.5) } }.buttonStyle(.plain).accessibilityLabel("Edit \(title.lowercased())")
                }
                content()
            }
        }
    }
    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) { Text(label).font(MiraUI.font(16)).foregroundStyle(MiraUI.soft); Spacer(minLength: 8); Text(value).font(MiraUI.font(18, "SemiBold")).multilineTextAlignment(.trailing) }
    }
}
