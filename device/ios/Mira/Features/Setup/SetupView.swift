import ARKit
import AVFoundation
import SwiftUI

struct SetupView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var cameraAuthorized = false

    var body: some View {
        Group {
            if !ARWorldTrackingConfiguration.isSupported {
                ContentUnavailableView("AR unavailable", systemImage: "arkit",
                    description: Text("Open Mira on an ARKit-capable iPhone or iPad to set up your space."))
            } else if cameraAuthorized {
                ARSetupPreview(isActive: scenePhase == .active)
                    .overlay(alignment: .bottom) {
                        Text("Move your device slowly to find a surface. Calibration is not implemented yet.")
                            .padding().background(.regularMaterial).clipShape(.rect(cornerRadius: 16)).padding()
                    }
            } else {
                ContentUnavailableView {
                    Label("Camera access needed", systemImage: "camera")
                } description: {
                    Text("Allow camera access in Settings to scan your space.")
                } actions: {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        Link("Open Settings", destination: url)
                    }
                }
            }
        }
        .navigationTitle("Setup")
        .task(id: scenePhase) {
            guard scenePhase == .active, ARWorldTrackingConfiguration.isSupported else { return }
            cameraAuthorized = await AVCaptureDevice.requestAccess(for: .video)
        }
    }
}

private struct ARSetupPreview: UIViewRepresentable {
    let isActive: Bool

    func makeUIView(context: Context) -> ARSCNView { ARSCNView(frame: .zero) }

    func updateUIView(_ view: ARSCNView, context: Context) {
        if isActive {
            let configuration = ARWorldTrackingConfiguration()
            configuration.planeDetection = [.horizontal, .vertical]
            view.session.run(configuration)
        } else {
            view.session.pause()
        }
    }

    static func dismantleUIView(_ view: ARSCNView, coordinator: ()) { view.session.pause() }
}
