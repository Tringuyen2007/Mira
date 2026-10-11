import Foundation
import Security

// The server's MIRA_API_TOKEN. It is a credential: kept in the Keychain (never
// UserDefaults), never logged, and only ever shown masked.
protocol TokenStore: AnyObject {
    func load() -> String?
    func save(_ token: String) throws
    func delete()
}

struct KeychainError: LocalizedError {
    let status: OSStatus
    var errorDescription: String? { "Couldn’t save the pairing token (Keychain error \(status))." }
}

final class KeychainTokenStore: TokenStore {
    private let service: String
    private let account = "mira-api-token"

    init(service: String = "com.mira.app.server") { self.service = service }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    func load() -> String? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data, let token = String(data: data, encoding: .utf8), !token.isEmpty
        else { return nil }
        return token
    }

    func save(_ token: String) throws {
        let data = Data(token.utf8)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw KeychainError(status: added) }
        } else if status != errSecSuccess {
            throw KeychainError(status: status)
        }
    }

    func delete() { SecItemDelete(query as CFDictionary) }
}

/// For tests and previews.
final class MemoryTokenStore: TokenStore {
    private var token: String?
    init(_ token: String? = nil) { self.token = token }
    func load() -> String? { token }
    func save(_ token: String) throws { self.token = token }
    func delete() { token = nil }
}

enum PairingToken {
    /// Trims whitespace; nil when nothing usable was entered.
    static func normalized(_ text: String) -> String? {
        let token = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return token.isEmpty ? nil : token
    }

    /// Safe to display: the last four characters only.
    static func masked(_ token: String) -> String {
        token.count <= 8 ? "••••" : "••••" + token.suffix(4)
    }
}
