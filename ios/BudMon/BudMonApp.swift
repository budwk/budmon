import SwiftUI
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        application.registerForRemoteNotifications()
        return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format:"%02x",$0) }.joined()
        UserDefaults.standard.set(token,forKey:"apnsToken")
        NotificationCenter.default.post(name:.pushToken,object:token)
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        NotificationCenter.default.post(name:.pushToken,object:nil,userInfo:["error":error.localizedDescription])
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        await MainActor.run { NotificationCenter.default.post(name:.refreshData,object:nil) }
        return [.banner,.sound,.list]
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        if let id = response.notification.request.content.userInfo["target_id"] as? Int {
            await MainActor.run {
                UserDefaults.standard.set(id,forKey:"pendingTarget")
                NotificationCenter.default.post(name:.openTarget,object:id)
            }
        }
    }
}

@main struct BudMonApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var store = Store()
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            Group {
                if store.booting { ProgressView("连接 BudMon…") }
                else if store.authenticated { HomeView() }
                else { AuthView() }
            }
            .environmentObject(store)
            .tint(.mint)
            .preferredColorScheme(.dark)
            .task {
                #if DEBUG
                if ProcessInfo.processInfo.arguments.contains("-ui-testing") { try? await API.shared.clear() }
                #endif
                await store.boot()
                openPending()
            }
            .alert("提示", isPresented: Binding(get:{store.error != nil},set:{if !$0 {store.error = nil}})) {
                Button("知道了") { store.error = nil }
            } message: { Text(store.error ?? "") }
            .onReceive(NotificationCenter.default.publisher(for:.sessionExpired)) { _ in store.expire() }
            .onReceive(NotificationCenter.default.publisher(for:.pushToken)) { event in
                if let token = event.object as? String { store.pushToken = token; Task { await store.registerDevice() } }
                else { store.pushStatus = "推送注册失败，请检查签名及网络" }
            }
            .onReceive(NotificationCenter.default.publisher(for:.refreshData)) { _ in if store.authenticated { Task { await store.reload() } } }
            .onReceive(NotificationCenter.default.publisher(for:.openTarget)) { _ in openPending() }
            .onChange(of:store.authenticated) { _, _ in openPending() }
            .onChange(of:phase) { _, phase in
                if phase == .active && store.authenticated { Task { await store.syncPurchases(); await store.reload() } }
            }
        }
    }
    private func openPending() {
        let id = UserDefaults.standard.integer(forKey:"pendingTarget")
        if store.authenticated && id > 0 { store.open(id); UserDefaults.standard.removeObject(forKey:"pendingTarget") }
    }
}
