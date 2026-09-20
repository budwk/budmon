import SwiftUI
import StoreKit
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
    @Published var purchaseProduct: Product?
    @Published var purchasing = false
    @Published var purchaseMessage: String?
    @Published var purchaseAvailable = false
    private var purchaseContext: PurchaseContext?
    private var transactionTask: Task<Void, Never>?
    private var deviceRegistered = false
    @Published var path: [Int] = []
    @Published var tab = 0
    var pushToken: String? = UserDefaults.standard.string(forKey:"apnsToken")
    var environment: String { Bundle.main.object(forInfoDictionaryKey:"APNSEnvironment") as? String ?? "sandbox" }

    func boot() async {
        startTransactionListener()
        authenticated = await API.shared.hasSession()
        if authenticated { await reload(); await registerDevice(); await loadPurchases(); await syncPurchases() }
        booting = false
    }
    func login(username: String, password: String, register: Bool) async {
        loading = true
        defer { loading = false }
        do {
            let result: Tokens = try await API.shared.request(register ? "/auth/register" : "/auth/login", method:"POST", body:["username":username,"password":password],authenticated:false)
            try await API.shared.save(result)
            authenticated = true
            await reload()
            await registerDevice()
            await loadPurchases()
            await syncPurchases()
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
        purchaseContext = nil; purchaseProduct = nil; purchaseAvailable = false; purchaseMessage = nil
        authenticated = false; profile = nil; targets = []; notices = []; path = []; tab = 0
    }
    private func startTransactionListener() {
        guard transactionTask == nil else { return }
        transactionTask = Task { [weak self] in
            for await result in StoreKit.Transaction.updates {
                guard let self else { return }
                guard self.authenticated else { continue }
                do { try await self.deliverPurchase(result) }
                catch { self.purchaseMessage = "交易尚未同步：\(error.localizedDescription)" }
            }
        }
    }

    func loadPurchases() async {
        guard authenticated else { return }
        purchaseAvailable = false
        purchaseProduct = nil
        purchaseContext = nil
        let accountID = profile?.id
        do {
            let context: PurchaseContext = try await API.shared.request("/iap/context")
            guard context.enabled else {
                purchaseMessage = "服务端尚未启用内购，请稍后重试。"
                return
            }
            guard context.product_id == "budmon_number" else { throw APIError(message: "商品配置不一致") }
            let products = try await Product.products(for: [context.product_id])
            guard authenticated, profile?.id == accountID else { return }
            guard let product = products.first, product.type == .consumable else {
                throw APIError(message: "暂未获取到商品，请确认 App Store 账号及网络后重试")
            }
            purchaseContext = context
            purchaseProduct = product
            purchaseAvailable = true
            purchaseMessage = nil
        } catch { purchaseMessage = error.localizedDescription }
    }

    func purchaseSlot() async {
        guard !purchasing, authenticated, purchaseAvailable,
              let product = purchaseProduct, let context = purchaseContext else { return }
        purchasing = true
        defer { purchasing = false }
        do {
            let result = try await product.purchase(options: [.appAccountToken(context.app_account_token)])
            switch result {
            case .success(let verification):
                try await deliverPurchase(verification)
            case .pending:
                purchaseMessage = "购买等待 Apple 批准，完成后将自动增加名额。"
            case .userCancelled:
                purchaseMessage = "已取消购买，未增加名额。"
            @unknown default:
                purchaseMessage = "购买状态待确认，请稍后同步购买。"
            }
        } catch {
            purchaseMessage = "购买未完成同步：\(error.localizedDescription)。如已扣款，请同步未完成购买，无需再次付款。"
        }
    }

    private func deliverPurchase(_ result: VerificationResult<StoreKit.Transaction>) async throws {
        guard case .verified(let transaction) = result else {
            throw APIError(message: "Apple 交易验证未通过")
        }
        guard transaction.productID == "budmon_number" else { return }
        guard authenticated else {
            throw APIError(message: "请登录官方服务器的购买账号后重试")
        }
        // Fetch the current token: do not credit a different account after logout/login.
        let context: PurchaseContext = try await API.shared.request("/iap/context")
        guard transaction.appAccountToken == context.app_account_token else {
            throw APIError(message: "请登录购买时使用的 BudMon 账号同步此交易")
        }
        let response: PurchaseAcknowledgement = try await API.shared.request("/iap/transactions", method: "POST",
            body: ["signed_transaction": result.jwsRepresentation])
        guard response.ok else { throw APIError(message: "服务端尚未确认到账") }
        await transaction.finish()
        await reload()
        purchaseMessage = response.status == "credited" ? "购买已到账，监测名额已更新。" : "此订单已退款或撤销。"
    }

    func syncPurchases() async {
        guard authenticated else { return }
        // Consumables already delivered are stored in the BudMon account, not restored from currentEntitlements.
        for await result in StoreKit.Transaction.unfinished {
            do { try await deliverPurchase(result) }
            catch { purchaseMessage = "交易尚未同步：\(error.localizedDescription)" }
        }
        await reload()
    }

    func open(_ id: Int) { tab = 0; path = [id] }
}
