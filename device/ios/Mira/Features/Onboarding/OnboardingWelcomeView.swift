import SwiftUI

// Figma 82:127. Only decorative layers use fixed design coordinates; copy and
// controls flow vertically, scroll on small screens, and scale with Dynamic Type.
struct OnboardingWelcomeView: View {
    let onContinue: () -> Void

    var body: some View {
        GeometryReader { geometry in
            let width = min(geometry.size.width, 500)
            let scale = width / 430
            ScrollView {
                VStack(spacing: 0) {
                    mascot
                        .scaleEffect(scale, anchor: .top)
                        .frame(width: width, height: 288 * scale)
                        .padding(.top, max(12, 86 * scale - geometry.safeAreaInsets.top))
                    introduction
                        .padding(.top, 30 * scale)
                    privacyPromise
                        .padding(.horizontal, 32 * scale)
                        .padding(.top, 45 * scale)
                    Spacer(minLength: 32 * scale)
                    Button(action: onContinue) {
                        Text("Get started")
                            .font(.custom("Inter-Medium", size: 20, relativeTo: .title3))
                            .foregroundStyle(Color("MiraSurface"))
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: 64)
                            .background(Color("MiraCharcoal"), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .shadow(color: Color("MiraCharcoal").opacity(0.18), radius: 14, y: 10)
                    .accessibilityIdentifier("onboardingContinue")
                    .padding(.horizontal, 32 * scale)
                    .padding(.bottom, 34 * scale)
                }
                .frame(width: width)
                .frame(minHeight: geometry.size.height)
                .frame(maxWidth: .infinity)
            }
            .scrollIndicators(.hidden)
            .background {
                GeometryReader { background in
                    ZStack {
                        EllipticalGradient(stops: [
                            .init(color: Color("MiraWelcomeCenter"), location: 0),
                            .init(color: Color("MiraWelcomePeach"), location: 0.5),
                            .init(color: Color("MiraWelcomeEdge"), location: 1)
                        ], center: .center, startRadiusFraction: 0, endRadiusFraction: 0.5)
                        Image("WelcomeOrbits")
                            .resizable()
                            .frame(width: background.size.width, height: background.size.height)
                    }
                }
                .ignoresSafeArea()
                .accessibilityHidden(true)
            }
        }
        .preferredColorScheme(.light)
    }

    private var introduction: some View {
        VStack(spacing: 12) {
            Text("WELCOME")
                .font(.custom("Inter-Medium", size: 17, relativeTo: .body))
                .tracking(3.74)
                .foregroundStyle(Color("MiraAccentTextSmall"))
            Text("Hi, I'm Mira.")
                .font(.custom("Inter-Light", size: 46, relativeTo: .largeTitle))
                .foregroundStyle(LinearGradient(colors: [Color("MiraWelcomeTitleStart"), Color("MiraWelcomeTitleEnd")], startPoint: .leading, endPoint: .trailing))
                .accessibilityAddTraits(.isHeader)
            Text("Your physical therapy\ncoach at home.")
                .font(.custom("Inter-Regular", size: 22, relativeTo: .title2))
                .lineSpacing(3)
                .foregroundStyle(Color("MiraInkSoft"))
                .frame(minHeight: 60)
        }
        .multilineTextAlignment(.center)
        .padding(.horizontal, 24)
    }

    private var privacyPromise: some View {
        HStack(spacing: 14) {
            Image("WelcomeLock")
                .resizable()
                .frame(width: 52, height: 52)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text("No video leaves your home")
                    .font(.custom("Inter-SemiBold", size: 20, relativeTo: .headline))
                    .foregroundStyle(Color("MiraInk"))
                    .frame(minHeight: 26, alignment: .leading)
                Text("Mira shares your progress with your therapist, never your video.")
                    .font(.custom("Inter-Regular", size: 17, relativeTo: .body))
                    .lineSpacing(3)
                    .foregroundStyle(Color("MiraInkSoft"))
                    .frame(minHeight: 48, alignment: .leading)
            }
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 19)
        .padding(.vertical, 21)
        .background(Color("MiraSurface"), in: RoundedRectangle(cornerRadius: 28))
        .overlay { RoundedRectangle(cornerRadius: 28).stroke(Color("MiraSurface"), lineWidth: 1) }
        .shadow(color: Color("MiraWelcomeSky").opacity(0.18), radius: 40, y: 12)
        .accessibilityElement(children: .combine)
    }

    private var mascot: some View {
        ZStack(alignment: .topLeading) {
            Image("WelcomeHalo")
                .resizable().frame(width: 273.6, height: 273.6)
                .position(x: 215, y: 136.8)
            Image("WelcomeInnerRing")
                .resizable().frame(width: 237.6, height: 237.6)
                .position(x: 215, y: 136.8)
            Image("WelcomeMascot")
                .resizable().scaledToFit().frame(width: 228.96, height: 258.48)
                .position(x: 215, y: 145.08)
            Image("WelcomeSparkle28")
                .resizable().frame(width: 28, height: 28)
                .shadow(color: Color("MiraWelcomeSky").opacity(0.7), radius: 7)
                .position(x: 314, y: 20)
            Image("WelcomeSparkle16")
                .resizable().frame(width: 16, height: 16)
                .shadow(color: Color("MiraWelcomeSky").opacity(0.7), radius: 4)
                .position(x: 328, y: 52)
            Image("WelcomeSparkle12")
                .resizable().frame(width: 12, height: 12)
                .shadow(color: Color("MiraWelcomeSky").opacity(0.7), radius: 3)
                .position(x: 110, y: 220)
            Image("WelcomeSparkle10")
                .resizable().frame(width: 10, height: 10)
                .shadow(color: Color("MiraWelcomeSky").opacity(0.7), radius: 2.5)
                .position(x: 97, y: 39)
        }
        .frame(width: 430, height: 288)
        .accessibilityHidden(true)
    }
}

#Preview("Welcome & privacy") {
    OnboardingWelcomeView(onContinue: {})
}
