import SwiftUI

enum MiraUI {
    static let ink = Color("MiraInk")
    static let soft = Color("MiraInkSoft")
    static let accent = Color("MiraAccentTextSmall")
    static let charcoal = Color("MiraCharcoal")
    static let mist = Color(red: 226/255, green: 236/255, blue: 246/255)
    static let peach = Color(red: 247/255, green: 226/255, blue: 205/255)
    static let blush = Color(red: 237/255, green: 204/255, blue: 211/255)
    static let border = Color(red: 133/255, green: 127/255, blue: 138/255)
    static let alert = Color(red: 217/255, green: 45/255, blue: 32/255)
    static let title = LinearGradient(colors: [Color("MiraWelcomeTitleStart"), Color("MiraWelcomeTitleEnd")], startPoint: .leading, endPoint: .trailing)
    static func font(_ size: CGFloat, _ weight: String = "Regular", relativeTo: Font.TextStyle = .body) -> Font {
        .custom("Inter-\(weight)", size: size, relativeTo: relativeTo)
    }
}

private struct MiraPreviewKey: EnvironmentKey { static let defaultValue = false }
extension EnvironmentValues {
    var miraDesignPreview: Bool {
        get { self[MiraPreviewKey.self] }
        set { self[MiraPreviewKey.self] = newValue }
    }
}

struct MiraBackground: View {
    var orbits = true
    var body: some View {
        GeometryReader { geometry in
            let scale = geometry.size.width / 430
            ZStack(alignment: .topLeading) {
                EllipticalGradient(stops: [
                    .init(color: Color("MiraWelcomeCenter"), location: 0),
                    .init(color: Color("MiraWelcomePeach"), location: 0.5),
                    .init(color: Color("MiraWelcomeEdge"), location: 1)
                ], center: .center, startRadiusFraction: 0, endRadiusFraction: 0.5)
                if orbits {
                    Image("Figma_7103f").resizable().frame(width: 420 * scale, height: 420 * scale)
                        .offset(x: 230 * scale, y: -190 * scale)
                    Image("Figma_9f179").resizable().frame(width: 300 * scale, height: 300 * scale)
                        .offset(x: 290 * scale, y: -130 * scale)
                }
            }
        }
        .ignoresSafeArea().accessibilityHidden(true)
    }
}

struct MiraPage<Content: View>: View {
    var inset: CGFloat = 24
    var top: CGFloat = 12
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) { content }
                .frame(maxWidth: 500, alignment: .leading)
                .padding(.horizontal, inset).padding(.top, top).padding(.bottom, 24)
                .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        .background { MiraBackground() }
        .foregroundStyle(MiraUI.ink)
        .preferredColorScheme(.light)
        .toolbarBackground(.hidden, for: .navigationBar)
    }
}

struct MiraIntro: View {
    let eyebrow: String
    let title: String
    var detail: String? = nil
    var size: CGFloat = 40
    var centered = false
    var body: some View {
        VStack(alignment: centered ? .center : .leading, spacing: 8) {
            Text(eyebrow).font(MiraUI.font(17, "Medium")).tracking(3.74).foregroundStyle(MiraUI.accent)
            Text(title).font(MiraUI.font(size, "Light", relativeTo: .largeTitle))
                .foregroundStyle(MiraUI.title).accessibilityAddTraits(.isHeader)
            if let detail { Text(detail).font(MiraUI.font(20)).foregroundStyle(MiraUI.soft).lineSpacing(4) }
        }
        .multilineTextAlignment(centered ? .center : .leading)
        .frame(maxWidth: .infinity, alignment: centered ? .center : .leading)
    }
}

struct MiraCard<Content: View>: View {
    var padding: CGFloat = 20
    @ViewBuilder var content: Content
    var body: some View {
        content.frame(maxWidth: .infinity, alignment: .leading).padding(padding)
            .background(Color("MiraSurface"), in: RoundedRectangle(cornerRadius: 28))
            .shadow(color: Color("MiraWelcomeSky").opacity(0.18), radius: 20, y: 12)
    }
}

struct MiraButton: View {
    let title: String
    var secondary = false
    var icon: (String, String)? = nil
    var action: () -> Void
    @Environment(\.isEnabled) private var enabled
    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                if let icon { FigmaIcon(screen: icon.0, slot: icon.1, size: 28) }
                Text(title).font(MiraUI.font(20, "Medium", relativeTo: .title3)).multilineTextAlignment(.center)
            }.padding(.horizontal, 18).padding(.vertical, 16)
                .frame(maxWidth: .infinity, minHeight: 64)
                .foregroundStyle(secondary ? MiraUI.ink : Color("MiraSurface"))
                .background(secondary ? Color("MiraSurface") : MiraUI.charcoal, in: Capsule())
                .overlay { if secondary { Capsule().stroke(MiraUI.border, lineWidth: 1.5) } }
                .opacity(enabled ? 1 : 0.55)
        }
        .buttonStyle(.plain)
        .shadow(color: secondary ? .clear : .black.opacity(0.18), radius: 14, y: 10)
    }
}

struct MiraField<Content: View>: View {
    let label: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(label).font(MiraUI.font(19, "SemiBold"))
            content.font(MiraUI.font(22)).padding(.horizontal, 22).frame(minHeight: 64)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color("MiraSurface"), in: RoundedRectangle(cornerRadius: 20))
                .overlay { RoundedRectangle(cornerRadius: 20).stroke(MiraUI.border, lineWidth: 1.5) }
                .shadow(color: Color("MiraWelcomeSky").opacity(0.14), radius: 12, y: 8)
        }
    }
}

enum FigmaAssets {
    static let map: [String: [String: String]] = {
        guard let url = Bundle.main.url(forResource: "AssetMap", withExtension: "json", subdirectory: "Figma"),
              let data = try? Data(contentsOf: url),
              let result = try? JSONDecoder().decode([String: [String: String]].self, from: data) else { return [:] }
        return result
    }()
    static func name(_ screen: String, _ slot: String) -> String { map[screen]?[slot] ?? "" }
}

struct FigmaIcon: View {
    let screen: String
    let slot: String
    var size: CGFloat = 24
    var body: some View {
        Image(FigmaAssets.name(screen, slot)).resizable().scaledToFit()
            .frame(width: size, height: size).accessibilityHidden(true)
    }
}

// Figma illustration layers only. Screen content uses native SwiftUI layout.
struct FigmaIllustration: View {
    let name: String
    static let scenes: [String: Scene] = {
        guard let url = Bundle.main.url(forResource: "Illustrations", withExtension: "json", subdirectory: "Figma"),
              let data = try? Data(contentsOf: url),
              let scenes = try? JSONDecoder().decode([String: Scene].self, from: data) else { return [:] }
        return scenes
    }()
    struct Scene: Decodable { let width: CGFloat; let height: CGFloat; let layers: [Layer]; let dark: Bool }
    struct Layer: Decodable {
        let kind: String
        let x: CGFloat; let y: CGFloat; let width: CGFloat; let height: CGFloat
        var asset: String?; var fit: Bool?; var mask: String?
        var fill: String?; var stroke: String?; var radius: CGFloat?; var lineWidth: CGFloat?; var dashed: Bool?
        var text: String?; var fontSize: CGFloat?
    }
    var body: some View {
        if let scene = Self.scenes[name] {
            GeometryReader { geometry in
                let scale = geometry.size.width / scene.width
                ZStack(alignment: .topLeading) {
                    ForEach(Array(scene.layers.enumerated()), id: \.offset) { _, layer in
                        artwork(layer)
                            .frame(width: max(0, layer.width), height: max(0, layer.height))
                            .position(x: layer.x + layer.width / 2, y: layer.y + layer.height / 2)
                    }
                }
                .frame(width: scene.width, height: scene.height)
                .scaleEffect(scale, anchor: .topLeading)
            }
            .aspectRatio(scene.width / scene.height, contentMode: .fit)
            .accessibilityHidden(true)
        }
    }
    @ViewBuilder private func artwork(_ layer: Layer) -> some View {
        switch layer.kind {
        case "image":
            Image(layer.asset ?? "").resizable().aspectRatio(layer.fit == true ? nil : layer.width / max(layer.height, 1), contentMode: .fit)
                .mask {
                    if let mask = layer.mask { Image(mask).resizable().scaledToFit().luminanceToAlpha() }
                    else { Rectangle() }
                }
        case "text": Text(layer.text ?? "").font(MiraUI.font(layer.fontSize ?? 18, "SemiBold"))
                .foregroundStyle(Color(figma: layer.fill))
        default:
            RoundedRectangle(cornerRadius: layer.radius ?? 0).fill(Color(figma: layer.fill))
                .overlay {
                    RoundedRectangle(cornerRadius: layer.radius ?? 0)
                        .stroke(Color(figma: layer.stroke), style: StrokeStyle(lineWidth: layer.lineWidth ?? 1, dash: layer.dashed == true ? [4, 4] : []))
                }
        }
    }
}

private extension Color {
    init(figma value: String?) {
        guard let value else { self = .clear; return }
        if value.hasPrefix("#"), let hex = UInt64(value.dropFirst(), radix: 16) {
            self.init(red: Double((hex >> 16) & 255)/255, green: Double((hex >> 8) & 255)/255, blue: Double(hex & 255)/255)
        } else if value.hasPrefix("rgba") {
            let values = value.dropFirst(5).dropLast().split(separator: ",").compactMap { Double($0) }
            if values.count == 4 { self.init(red: values[0]/255, green: values[1]/255, blue: values[2]/255, opacity: values[3]) }
            else { self = .clear }
        } else { self = .clear }
    }
}

struct MiraArtworkBackground: View {
    var body: some View {
        EllipticalGradient(stops: [.init(color: .white, location: 0), .init(color: Color("MiraWelcomePeach"), location: 0.6), .init(color: MiraUI.mist, location: 1)], center: .center, startRadiusFraction: 0, endRadiusFraction: 0.5)
    }
}

struct MiraScreenHeading: View {
    let title: String
    var detail: String? = nil
    var mascot = false
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 8) {
                Text(title).font(MiraUI.font(34, "Light", relativeTo: .largeTitle))
                    .foregroundStyle(MiraUI.title).accessibilityAddTraits(.isHeader)
                if let detail { Text(detail).font(MiraUI.font(18)).foregroundStyle(MiraUI.soft).lineSpacing(3) }
            }.frame(maxWidth: .infinity, alignment: .leading)
            if mascot { Image("MascotIdle").resizable().scaledToFit().frame(width: 84, height: 94).accessibilityHidden(true) }
        }
    }
}

struct MiraSegments: View {
    let labels: [String]
    @Binding var selection: String
    var body: some View {
        HStack(spacing: 0) {
            ForEach(labels, id: \.self) { label in
                Button { selection = label } label: {
                    Text(label).font(MiraUI.font(16, selection == label ? "SemiBold" : "Medium"))
                        .minimumScaleFactor(0.8).lineLimit(1).padding(.horizontal, 8)
                        .frame(maxWidth: .infinity, minHeight: 48)
                        .foregroundStyle(selection == label ? .white : MiraUI.ink)
                        .background(selection == label ? MiraUI.charcoal : .clear, in: Capsule())
                }.buttonStyle(.plain).accessibilityAddTraits(selection == label ? .isSelected : [])
            }
        }.padding(4).background(.white.opacity(0.9), in: Capsule())
            .overlay { Capsule().stroke(MiraUI.border, lineWidth: 1) }
    }
}

struct MiraMenuRow: View {
    let title: String
    var detail: String? = nil
    let screen: String
    let icon: String
    var index = 0
    var body: some View {
        HStack(spacing: 18) {
            FigmaIcon(screen: screen, slot: icon).padding(10)
                .background(index % 3 == 0 ? Color(red: 0.97, green: 0.88, blue: 0.8) : index % 3 == 1 ? MiraUI.mist : Color(red: 0.93, green: 0.8, blue: 0.83), in: Circle())
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(MiraUI.font(20, "SemiBold"))
                if let detail { Text(detail).font(MiraUI.font(16)).foregroundStyle(MiraUI.soft) }
            }.frame(maxWidth: .infinity, alignment: .leading)
            FigmaIcon(screen: screen, slot: "imgIconChevron", size: 20)
        }.foregroundStyle(MiraUI.ink).padding(.vertical, 10).contentShape(Rectangle())
    }
}

struct MiraPendingCard: View {
    let title: String
    let detail: String
    var body: some View {
        MiraCard {
            VStack(alignment: .leading, spacing: 10) {
                Text(title).font(MiraUI.font(20, "SemiBold"))
                Text(detail).font(MiraUI.font(18)).foregroundStyle(MiraUI.soft).lineSpacing(3)
            }
        }
    }
}
