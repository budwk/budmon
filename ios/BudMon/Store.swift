import SwiftUI
import UserNotifications

@MainActor final class Store: ObservableObject {
    @Published var authenticated = false
    @Published var booting = true
    @Published var targets: [Target] = []
    @Published var notices: [Notice] = []
    @Published var profile: Profile?
    @Published var error: String?
    @Published var loading = false
    @Published var pushStatus = "尚未开启通知"
    private var deviceRegistered = false
    @Published var path: [Int] = []
    @Published var tab = 0
    var pushToken: String? = UserDefaults.standard.string(forKey:"apnsToken")
    var environment: String { Bundle.main.object(forInfoDictionaryKey:"APNSEnvironment") as? String ?? "sandbox" }

    func boot() async {
        authenticated = await API.shared.hasSession()
        if authenticated { await reload(); await registerDevice() }
        booting = false
    }
    func login(username: String, password: String, server: String, register: Bool) async {
        loading = true
        defer { loading = false }
        do {
            try await API.shared.setServer(server)
            let result: Tokens = try await API.shared.request(register ? "/auth/register" : "/auth/login", method:"POST", body:["username":username,"password":password],authenticated:false)
            try await API.shared.save(result)
            authenticated = true
            await reload()
            await registerDevice()
        } catch { if !error.isRequestCancellation && !Task.isCancelled { self.error = error.localizedDescription } }
    }
    func reload() async {
        do {
            async let t: [Target] = API.shared.request("/targets")
            async let p: Profile = API.shared.request("/me")
            async let n: [Notice] = API.shared.request("/notifications")
            let result = try await (t,p,n)
            guard authenticated else { return }
            targets = result.0; profile = result.1; notices = result.2
            if deviceRegistered { await refreshPushStatus() }
        } catch { if !error.isRequestCancellation && !Task.isCancelled { self.error = error.localizedDescription } }
    }
    func enablePush() async {
        do {
            let granted = try await UNUserNotificationCenter.current().requestAuthorization(options:[.alert,.badge,.sound])
            if granted { UIApplication.shared.registerForRemoteNotifications(); pushStatus = "正在注册设备…" }
            else { pushStatus = "通知未授权，请前往系统设置开启" }
        } catch { if !error.isRequestCancellation && !Task.isCancelled { self.error = error.localizedDescription } }
    }
    func registerDevice() async {
        guard authenticated, let pushToken else { return }
        do {
            let _: Acknowledgement = try await API.shared.request("/devices",method:"POST",body:["token":pushToken,"environment":environment])
            deviceRegistered = true
            // Query independently: cached profile may predate server APNs configuration,
            // or dashboard loading may have failed before assigning it.
            let current: Profile = try await API.shared.request("/me")
            guard authenticated else { return }
            profile = current
            await refreshPushStatus()
        } catch {
            guard authenticated else { return }
            pushStatus = "推送状态查询或设备注册失败，请重试"
            self.error = error.localizedDescription
        }
    }
    private func refreshPushStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        guard authenticated else { return }
        pushStatus = Self.pushStatusMessage(configured: profile?.push_configured, authorization: settings.authorizationStatus)
    }
    static func pushStatusMessage(configured: Bool?, authorization: UNAuthorizationStatus) -> String {
        if authorization == .denied || authorization == .notDetermined {
            return "设备已注册，请开启系统通知权限"
        }
        guard let configured else { return "设备已注册，尚未获取服务器推送配置" }
        return configured ? "设备已注册，服务器推送配置已就绪" : "设备已注册，服务器返回 APNs 配置未就绪"
    }
    func logout() async {
        do {
            if let pushToken {
                let _: Acknowledgement = try await API.shared.request("/devices",method:"DELETE",body:["token":pushToken,"environment":environment])
            }
            let _: Acknowledgement = try await API.shared.request("/auth/logout",method:"POST")
            expire()
        } catch { self.error = "退出未完成：\(error.localizedDescription)。请联网后重试，以解绑推送设备。" }
    }
    func expire() {
        try? Keychain.write(nil,key:"tokens")
        Task { try? await API.shared.clear() }
        deviceRegistered = false; pushStatus = "尚未开启通知"
        authenticated = false; profile = nil; targets = []; notices = []; path = []; tab = 0
    }
    func open(_ id: Int) { tab = 0; path = [id] }
}
