import Foundation

struct Tokens: Codable {
    let token: String
    let refresh_token: String
}
struct Profile: Codable {
    let id: Int
    let username: String
    let role: String
    var push_enabled: Bool
    let push_configured: Bool
    let plan_name: String
    let target_limit: Int
    let target_used: Int
    let purchased_quota: Int?
    let plan_expires_at: String?
}
struct Target: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let url: String
    let enabled: Int
    let last_status: String
    let last_code: Int?
    let last_error: String?
    let last_checked_at: String?
    let last_cert_days: Int?
    let last_cert_expires_at: String?
    let last_cert_error: String?
    var statusLabel: String { enabled == 0 ? "已暂停" : last_status == "up" ? "运行正常" : last_status == "down" ? "服务异常" : "等待检测" }
}
struct CheckLog: Codable, Identifiable {
    let id: Int
    let event_type: String
    let ok: Int
    let status_code: Int?
    let cert_days: Int?
    let error: String?
    let checked_at: String
}
struct Notice: Codable, Identifiable {
    let id: Int
    let target_id: Int?
    let kind: String
    let title: String
    let body: String
    let read_at: String?
    let created_at: String
}
struct Acknowledgement: Decodable { let ok: Bool }
struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

// Swift task cancellation and URLSession cancellation are normal navigation events.
extension Error {
    var isRequestCancellation: Bool {
        if self is CancellationError { return true }
        let value = self as NSError
        return value.domain == NSURLErrorDomain && value.code == NSURLErrorCancelled
    }
}


struct PurchaseContext: Decodable {
    let enabled: Bool
    let product_id: String
    let app_account_token: UUID
}
struct PurchaseAcknowledgement: Decodable {
    let ok: Bool
    let status: String
}
