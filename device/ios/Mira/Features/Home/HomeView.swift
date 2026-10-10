import SwiftUI

struct HomeView: View {
    var body: some View {
        List {
            Section("Get started") {
                NavigationLink("Set up your space") { SetupView() }
                NavigationLink("Live session") { LiveView() }
            }
            Section("Reflect") {
                NavigationLink("Session summary") { SummaryView() }
            }
        }
        .navigationTitle("Mira")
    }
}
