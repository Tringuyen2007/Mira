import SwiftUI

/// Routes `model.route` (from `WatchFlowReducer`) to a screen over the shared radial wash.
///
/// - Ready, Holding to Stop and the user-opened overlays (Arm Exercises, Tremor Today, Settings) share one
///   horizontal pager while no session runs: swipe from Ready to open them (→ `.open(route)`), back to Ready
///   (→ `.close`). Ready and Holding to Stop keep one view identity so the hold press survives the switch.
/// - Every other route renders through `screen(for:)`. W6/W7 add their routes there (and a preview scenario in
///   `WatchPreview`); anything not yet built shows `PendingScreen`.
/// - `mira://ready` (Smart Stack widget) closes overlays and returns to Ready.
/// - DEBUG: `-MiraPreviewRoute <name>` loads a `WatchPreview` scenario instead of the phone link.
struct RootView: View {
    @Environment(WatchSessionModel.self) private var model

    var body: some View {
        ZStack {
            Rectangle().fill(WatchTheme.background).ignoresSafeArea()
            content
        }
        .toolbar(.hidden, for: .navigationBar)
        .onOpenURL { url in
            if url.scheme == "mira", url.host == "ready" { model.send(.deepLinkReady) }
        }
        .task {
            #if DEBUG
            if let name = WatchPreview.requestedRoute, model.loadPreview(name) { return }
            #endif
            model.activate()
        }
    }

    @ViewBuilder
    private var content: some View {
        let route = model.route
        if usesPager(route) {
            ReadyPager(route: route)
        } else {
            screen(for: route)
        }
    }

    /// Ready, Holding to Stop, and overlays opened from Ready (no session running).
    private func usesPager(_ route: WatchRoute) -> Bool {
        switch route {
        case .ready, .holdingToStop: return true
        default: return route.isOverlay && !model.flow.state.sessionActive
        }
    }

    @ViewBuilder
    private func screen(for route: WatchRoute) -> some View {
        switch route {
        // 01 Setup
        case .paired: PairedView()
        case .heartRate: HeartRatePermissionView()
        case .whichArm: WhichArmView()
        case .syncWave: SyncWaveView()
        case .learnTaps: LearnTapsView()
        // 02 Ready & Stop (.ready / .holdingToStop live in ReadyPager)
        case .countdown(let n): CountdownView(count: n)
        case .stopped: StoppedView()
        // 03 Live, 04 Rotation, 05 More — W6 / W7
        default: PendingScreen(route: route)
        }
    }
}

/// Ready plus the swipe-to pages (Arm Exercises while a watch exercise is unfinished, Tremor Today when
/// monitoring is on, Settings). The selected page mirrors the reducer's overlay.
struct ReadyPager: View {
    @Environment(WatchSessionModel.self) private var model
    let route: WatchRoute

    var body: some View {
        let state = model.flow.state
        var pages: [WatchRoute] = [.ready]
        if state.hasUnfinishedWatchExercise || route == .armExercises { pages.append(.armExercises) }
        if state.tremorMonitoring || route == .tremorToday { pages.append(.tremorToday) }
        pages.append(.settings)
        let selection = Binding<WatchRoute>(
            get: { route.isOverlay ? route : .ready },
            set: { new in
                guard new != (route.isOverlay ? route : .ready) else { return }
                model.send(new == .ready ? .close : .open(new))
            })
        return TabView(selection: selection) {
            ForEach(pages, id: \.self) { page in
                pageView(page).tag(page)
            }
        }
        .tabViewStyle(.page(indexDisplayMode: .never))
        .ignoresSafeArea()
    }

    @ViewBuilder
    private func pageView(_ page: WatchRoute) -> some View {
        switch page {
        case .ready: ReadyView(holding: route == .holdingToStop)
        // W6/W7: Arm Exercises, Tremor Today and Settings screens go here.
        default: PendingScreen(route: page)
        }
    }
}

/// Placeholder for routes whose screens land in W6/W7.
struct PendingScreen: View {
    let route: WatchRoute
    var body: some View {
        WatchCanvas(halo: .init(ringRadius: 60, center: CGPoint(x: 104, y: 110))) {
            CanvasStatusBar(title: "Mira")
            Text(String(describing: route)).watchText(17, color: WatchTheme.muted).figma(16, 100, 176, 40)
        }
    }
}
