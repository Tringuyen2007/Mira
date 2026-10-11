import SwiftUI

@main
struct MiraWatchPreviewApp: App {
    var body: some Scene {
        WindowGroup {
            WatchPreviewGallery()
        }
    }
}

struct WatchPreviewGallery: View {
    @State private var selected: WatchMockPhase = {
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: "--watch-screen"), arguments.indices.contains(index + 1),
           let phase = WatchMockPhase(rawValue: arguments[index + 1]) { return phase }
        return .ready
    }()
    @State private var smallWatch = false
    @State private var previewID = UUID()

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                VStack(spacing: 8) {
                    Text("MIRA / WATCH").font(.custom("Inter-Medium", size: 13)).tracking(3)
                        .foregroundStyle(Color(red: 0.04, green: 0.35, blue: 0.28))
                    Text("A little coach.\nRight on your wrist.")
                        .font(.custom("Inter-Light", size: 32)).multilineTextAlignment(.center)
                    Text("Interactive concept · sample data")
                        .font(.custom("Inter-Regular", size: 14)).foregroundStyle(.secondary)
                }
                .padding(.top, 16)

                WatchMockView(initialPhase: selected)
                    .id(previewID)
                    .preferredColorScheme(.dark)
                    .frame(width: smallWatch ? 176 : 198, height: smallWatch ? 215 : 242)
                    .clipShape(RoundedRectangle(cornerRadius: 40))
                    .padding(10)
                    .background(.black, in: RoundedRectangle(cornerRadius: 50))
                    .overlay { RoundedRectangle(cornerRadius: 50).stroke(.white.opacity(0.20), lineWidth: 2) }
                    .overlay(alignment: .trailing) {
                        Capsule().fill(Color(white: 0.22)).frame(width: 7, height: 30).offset(x: 6, y: -40)
                    }
                    .shadow(color: .black.opacity(0.18), radius: 25, y: 14)

                VStack(spacing: 10) {
                    Picker("Preview screen", selection: $selected) {
                        ForEach(WatchMockPhase.allCases) { phase in
                            Text(phase.label).tag(phase)
                        }
                    }
                    .pickerStyle(.menu)
                    .onChange(of: selected) { _, _ in previewID = UUID() }
                    Toggle("Small watch", isOn: $smallWatch)
                    Button("Restart preview") { previewID = UUID() }
                        .buttonStyle(.bordered)
                }
                .font(.custom("Inter-Regular", size: 15))
                .padding(20)
                .background(.white.opacity(0.70), in: RoundedRectangle(cornerRadius: 24))

                Text("Tap I'm ready, then scroll inside the watch. Demo rep adds a sample rep. Pause and Stop work locally.")
                    .font(.custom("Inter-Regular", size: 14)).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Text("Heart rate is simulated. This preview does not record a workout or send anything to Mira.")
                    .font(.custom("Inter-Regular", size: 12)).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(24).frame(maxWidth: 500).frame(maxWidth: .infinity)
        }
        .background(LinearGradient(colors: [Color(red: 0.90, green: 0.95, blue: 0.93), Color(red: 0.99, green: 0.92, blue: 0.91)], startPoint: .topLeading, endPoint: .bottomTrailing))
        .preferredColorScheme(.light)
    }
}

#Preview { WatchPreviewGallery() }
