import Foundation
import Security

// Tokens are kept in the device-only Keychain, never UserDefaults.
enum Keychain {
    static func read(_ key: String) -> Data? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.budmon.session", kSecAttrAccount as String: key,
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        return SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess ? result as? Data : nil
    }
    static func write(_ data: Data?, key: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.budmon.session", kSecAttrAccount as String: key]
        SecItemDelete(query as CFDictionary)
        guard let data else { return }
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else {
            throw APIError(message: "无法安全保存登录信息，请重试")
        }
    }
}

actor API {
    static let shared = API()
    private var base = UserDefaults.standard.string(forKey: "server") ?? "http://localhost:8000"
    private var tokens: Tokens?
    private var refreshTask: Task<Tokens, Error>?
    init() {
        if let origin = Keychain.read("server"), String(data:origin,encoding:.utf8) == base, let data = Keychain.read("tokens") { tokens = try? JSONDecoder().decode(Tokens.self, from: data) }
    }
    func hasSession() -> Bool { tokens != nil }
    static func validatedServer(_ input: String) throws -> String {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: value), let host = url.host, !host.isEmpty, ["http", "https"].contains(url.scheme ?? ""),
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty else { throw APIError(message: "请输入服务器根地址，支持 http:// 或 https://，例如 http://monitor.example.com:9977") }
        return value
    }
    func setServer(_ value: String) throws {
        let value = try Self.validatedServer(value)
        if value != base { try clear() }
        base = value
        UserDefaults.standard.set(value, forKey: "server")
    }
    func save(_ value: Tokens) throws {
        try Keychain.write(Data(base.utf8), key:"server")
        try Keychain.write(JSONEncoder().encode(value), key: "tokens")
        tokens = value
    }
    func clear() throws {
        refreshTask?.cancel()
        refreshTask = nil
        tokens = nil
        try Keychain.write(nil, key: "tokens")
    }
    private func transport<T: Decodable>(_ path: String, method: String, body: [String: Any]?, token: String?) async throws -> (T?, Int, String) {
        guard let url = URL(string: base + "/api" + path) else { throw APIError(message: "服务器地址无效") }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = path == "/monitor/run" ? 300 : 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body { request.httpBody = try JSONSerialization.data(withJSONObject: body) }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw APIError(message: "服务器响应无效") }
        if (200..<300).contains(response.statusCode) {
            return (try JSONDecoder().decode(T.self, from: data), response.statusCode, "")
        }
        let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        let validation = (json?["detail"] as? [[String:Any]])?.map { issue in
            let field = (issue["loc"] as? [String])?.last ?? "输入"
            return "\(field)：\(issue["msg"] as? String ?? "格式不正确")"
        }.joined(separator:"\n")
        let detail = json?["detail"] as? String ?? validation ?? "请求失败（\(response.statusCode)）"
        return (nil, response.statusCode, detail)
    }
    func request<T: Decodable>(_ path: String, method: String = "GET", body: [String: Any]? = nil, authenticated: Bool = true) async throws -> T {
        let attemptedToken = authenticated ? tokens?.token : nil
        let (value, status, message): (T?, Int, String) = try await transport(path, method: method, body: body, token: attemptedToken)
        if let value { return value }
        if status == 401 && authenticated, let old = tokens {
            let renewed: Tokens
            if old.token != attemptedToken {
                renewed = old
            } else {
                if refreshTask == nil {
                    refreshTask = Task {
                        let (value, _, message): (Tokens?, Int, String) = try await self.transport("/auth/refresh", method: "POST", body: ["refresh_token":old.refresh_token], token:nil)
                        guard let value else { throw APIError(message: message) }
                        try Task.checkCancellation()
                        try self.save(value)
                        return value
                    }
                }
                do {
                    renewed = try await refreshTask!.value
                    refreshTask = nil
                } catch {
                    refreshTask = nil
                    if error is APIError {
                        try? clear()
                        await MainActor.run { NotificationCenter.default.post(name: .sessionExpired, object:nil) }
                    }
                    throw error
                }
            }
            let (retried, retryStatus, retryError): (T?, Int, String) = try await transport(path, method:method,body:body,token:renewed.token)
            if let retried { return retried }
            if retryStatus == 401 {
                try? clear()
                await MainActor.run { NotificationCenter.default.post(name: .sessionExpired, object:nil) }
            }
            throw APIError(message:retryError)
        }
        throw APIError(message: message)
    }
}
extension Notification.Name {
    static let sessionExpired = Notification.Name("budmon.sessionExpired")
    static let pushToken = Notification.Name("budmon.pushToken")
    static let openTarget = Notification.Name("budmon.openTarget")
    static let refreshData = Notification.Name("budmon.refreshData")
}
