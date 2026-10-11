import ARKit
import AVFoundation
import SwiftUI

struct SetupView: View {
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.miraDesignPreview) private var preview
    @Environment(\.dismiss) private var dismiss
    @State private var cameraAuthorized = false
    @State private var guide = false
    @State private var step: Int
    @State private var spot = "Living room"
    init(initialStep: Int = 0) { _step = State(initialValue: initialStep) }
    private let scenes = ["findmat", "tripods", "fixes", "body", "save"]
    private var illustrating: Bool { preview || guide }

    var body: some View {
        MiraPage(inset: 16, top: 8) {
            if guide && !preview {
                Text("Setup guide · illustrations only").font(MiraUI.font(15, "Medium")).foregroundStyle(MiraUI.accent)
            }
            if step == 4 {
                FigmaIllustration(name: "save").padding(.horizontal, -16)
                MiraIntro(eyebrow: "ALL DONE", title: preview ? "You’re all set up" : "You’ve seen the setup steps", detail: preview ? "Mira will remember your mat and\ncameras, so you only do this once." : "Calibration still needs to be completed\nwith your Mira device.", centered: true)
                    .padding(.horizontal, 16)
                MiraField(label: "Name this spot") {
                    TextField("Living room", text: $spot).accessibilityLabel("Name this spot")
                }.padding(.horizontal, 16)
            } else {
                ZStack(alignment: step == 3 ? .top : .bottom) {
                    if illustrating {
                        FigmaIllustration(name: scenes[step])
                            .background {
                                if step == 3 { MiraArtworkBackground() }
                                else { LinearGradient(colors: [Color(white: 0.18), Color(white: 0.07)], startPoint: .top, endPoint: .bottom) }
                            }
                    } else if !ARWorldTrackingConfiguration.isSupported {
                        viewportMessage("AR unavailable", detail: "Use an ARKit-capable iPhone to scan your space.")
                    } else if cameraAuthorized {
                        ARSetupPreview(isActive: scenePhase == .active)
                            .aspectRatio(398/360, contentMode: .fit)
                    } else {
                        viewportMessage("Camera access needed", detail: "Allow Mira camera access in Settings.")
                    }
                    if illustrating {
                        HStack(spacing: 8) {
                            if step == 0 || step == 3 { FigmaIcon(screen: scenes[step], slot: "imgIconCheckCircle", size: 22) }
                            Text(caption).font(MiraUI.font(17, "Medium")).foregroundStyle(step == 3 ? MiraUI.ink : .white)
                        }.padding(14).background(step == 3 ? .white : .black.opacity(0.5), in: Capsule()).padding(20)
                    }
                }.clipShape(RoundedRectangle(cornerRadius: 28))
                MiraIntro(eyebrow: step == 3 ? "BODY CHECK" : "SET UP YOUR SPACE", title: title, detail: detail, size: 36).padding(.horizontal, 16)
                if step == 2 {
                    cameraRow("Camera A", "Ready", ready: true)
                    cameraRow("Camera B", "Move a hand-width left", ready: false)
                }
                if !illustrating {
                    Text("Surface scanning is available. Mat recognition, camera alignment and calibration are not connected yet.")
                        .font(MiraUI.font(15)).foregroundStyle(MiraUI.soft).padding(.horizontal, 16)
                    MiraButton(title: "View setup guide", secondary: true) { guide = true }.padding(.horizontal, 16)
                    if !cameraAuthorized, let url = URL(string: UIApplication.openSettingsURLString), ARWorldTrackingConfiguration.isSupported {
                        Link("Open Settings", destination: url).padding(.horizontal, 16)
                    }
                }
            }
        }
        .safeAreaInset(edge: .bottom) {
            if illustrating {
                VStack(spacing: 10) {
                    MiraButton(title: buttonTitle) {
                        if step < 4 { step += 1 } else { dismiss() }
                    }
                    if step == 3 { Button("Try again") { step = 0 }.font(MiraUI.font(17, "Medium")) }
                }.padding(.horizontal, 32).padding(.vertical, 16)
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Text("Step \(step + 1) of 5").font(MiraUI.font(17, "Medium")).foregroundStyle(MiraUI.accent)
            }
            if step > 0 {
                ToolbarItem(placement: .topBarLeading) { Button("Back") { step -= 1 } }
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .task(id: scenePhase) {
            guard !illustrating, scenePhase == .active, ARWorldTrackingConfiguration.isSupported else { return }
            cameraAuthorized = await AVCaptureDevice.requestAccess(for: .video)
        }
    }
    private var title: String { ["Find your mat", "Place your cameras", "Adjust your cameras", "Stand in the middle\nof your mat"][min(step,3)] }
    private var detail: String { ["Point your phone at your mat.\nMira will find its edges.", "Put each camera on its outline.\nThe cameras should face your mat\nfrom opposite sides.", "A small move will help Mira\nsee you clearly.", "Mira only sees this stick figure,\nnever video."][min(step,3)] }
    private var caption: String { ["Mat found", "Put each camera on its outline", "Camera B: move a hand-width left", "Both cameras can see all of you"][min(step,3)] }
    private var buttonTitle: String {
        if step == 4 { return preview ? "Save setup" : "Close setup guide" }
        return ["Continue", "Both cameras are in place", "Preview next step", "Looks good"][step]
    }
    private func viewportMessage(_ title: String, detail: String) -> some View {
        ContentUnavailableView(title, systemImage: "arkit", description: Text(detail))
            .aspectRatio(398/360, contentMode: .fit).background(MiraUI.mist)
    }
    private func cameraRow(_ title: String, _ detail: String, ready: Bool) -> some View {
        HStack(spacing: 14) {
            FigmaIcon(screen: "fixes", slot: ready ? "imgIconCheckCircle1" : "imgIconArrowLeftCircle", size: 32)
            Text(title).font(MiraUI.font(19, "SemiBold")); Spacer()
            Text(detail).font(MiraUI.font(17)).foregroundStyle(ready ? MiraUI.accent : MiraUI.soft)
        }.padding(16).background(.white, in: RoundedRectangle(cornerRadius: 20)).padding(.horizontal, 16)
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
