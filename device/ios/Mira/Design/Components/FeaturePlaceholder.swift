import SwiftUI

struct FeaturePlaceholder: View {
    let title: String
    let symbol: String
    let detail: String

    var body: some View {
        ContentUnavailableView(title, systemImage: symbol, description: Text(detail))
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.Mira.background)
            .navigationTitle(title)
    }
}
