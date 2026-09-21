import SwiftUI
import UserNotifications

private let canvas = Color(red:0.035, green:0.06, blue:0.105)
private let panel = Color(red:0.075, green:0.11, blue:0.16)

struct Surface<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content.padding(20).frame(maxWidth:.infinity, alignment:.leading)
            .background(panel, in:RoundedRectangle(cornerRadius:24))
            .overlay(RoundedRectangle(cornerRadius:24).stroke(.white.opacity(0.06)))
    }
}
struct StatusPill: View {
    let target: Target
    var color: Color { target.enabled == 0 ? .gray : target.last_status == "up" ? .mint : target.last_status == "down" ? .orange : .gray }
    var body: some View {
        Label(target.statusLabel, systemImage:"circle.fill").font(.caption.weight(.semibold))
            .foregroundStyle(color).padding(.horizontal,10).padding(.vertical,6)
            .background(color.opacity(0.12), in:Capsule())
    }
}

struct AuthView: View {
    @EnvironmentObject var store: Store
    @State private var username = ""
    @State private var password = ""
    @State private var register = false
    var body: some View {
        ScrollView {
            VStack(alignment:.leading, spacing:28) {
                HStack { Image(systemName:"waveform.path.ecg").font(.title); Text("BUDMON").font(.headline).tracking(4) }.foregroundStyle(.mint)
                VStack(alignment:.leading,spacing:12) {
                    Text("每一次在线，\n都安心可见。").font(.system(size:38,weight:.bold,design:.rounded))
                    Text("网站运行状态 · SSL 证书 · 即时告警")
                        .foregroundStyle(.secondary).font(.subheadline)
                }.padding(.vertical,16)
                Surface {
                    VStack(alignment:.leading,spacing:20) {
                        Picker("账号",selection:$register) { Text("登录").tag(false); Text("注册账号").tag(true) }.pickerStyle(.segmented)
                        field("用户名") { TextField("至少 3 位字母、数字或 _ . -",text:$username).accessibilityIdentifier("username").textContentType(.username) }
                        field("密码") { SecureField("至少 8 位",text:$password).accessibilityIdentifier("password").textContentType(.password) }
                        Button { Task { await store.login(username:username,password:password,register:register) } } label: {
                            HStack { Spacer(); if store.loading { ProgressView() } else { Text(register ? "创建账号" : "进入控制台").fontWeight(.bold); Image(systemName:"arrow.right") }; Spacer() }.padding(.vertical,9)
                        }.buttonStyle(.borderedProminent).disabled(store.loading || username.isEmpty || password.isEmpty)
                        Text(register ? "注册即享 5 个监测目标，可随时购买更多名额。" : "使用与网页端相同的账号登录，监测数据自动同步。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Label("让重要的服务，始终在你掌握之中",systemImage:"checkmark.shield").font(.caption).foregroundStyle(.secondary)
            }.padding(24).frame(maxWidth:540).frame(maxWidth:.infinity)
        }.background(canvas).textInputAutocapitalization(.never).autocorrectionDisabled()
    }
    private func field<C:View>(_ label: String,@ViewBuilder content: () -> C) -> some View {
        VStack(alignment:.leading,spacing:8) { Text(label).font(.caption).foregroundStyle(.secondary); content().padding(13).background(canvas,in:RoundedRectangle(cornerRadius:12)) }
    }
}

struct HomeView: View {
    @EnvironmentObject var store: Store
    var body: some View {
        TabView(selection:$store.tab) {
            NavigationStack(path:$store.path) {
                OverviewView().navigationDestination(for:Int.self) { TargetDetailView(id:$0) }
            }.tabItem { Label("监测",systemImage:"waveform.path.ecg") }.tag(0)
            NavigationStack { InboxView() }.tabItem { Label("通知",systemImage:"bell.badge") }.tag(1)
                .badge(store.notices.filter { $0.read_at == nil }.count)
            NavigationStack { AccountView() }.tabItem { Label("我的",systemImage:"person.crop.circle") }.tag(2)
        }
    }
}

struct OverviewView: View {
    @EnvironmentObject var store: Store
    @State private var adding = false
    @State private var query = ""
    @State private var filter = "全部"
    @State private var running = false
    private var online: Int { store.targets.filter { $0.enabled == 1 && $0.last_status == "up" }.count }
    private var down: Int { store.targets.filter { $0.enabled == 1 && $0.last_status == "down" }.count }
    private var visible: [Target] {
        store.targets.filter {
            (query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) || $0.url.localizedCaseInsensitiveContains(query)) &&
            (filter == "全部" || (filter == "异常" && $0.last_status == "down" && $0.enabled == 1) || (filter == "证书" && ($0.last_cert_error != nil || ($0.last_cert_days ?? 366) <= 30)))
        }
    }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:22) {
                HStack {
                    VStack(alignment:.leading,spacing:6) {
                        Text("CONTROL CENTER").font(.caption2.weight(.bold)).tracking(3).foregroundStyle(.mint)
                        Text("运行总览").font(.largeTitle.bold())
                    }
                    Spacer()
                    Button { adding = true } label: { Image(systemName:"plus").font(.title3.bold()).padding(12) }.buttonStyle(.bordered).clipShape(Circle()).accessibilityLabel("新增监测目标")
                }
                VStack(alignment:.leading,spacing:20) {
                    HStack {
                        Image(systemName: down > 0 ? "exclamationmark.shield.fill" : "waveform.path.ecg").font(.title).foregroundStyle(.mint)
                        Spacer()
                        Text("持续守护中").font(.caption).padding(8).background(.white.opacity(0.08),in:Capsule())
                    }
                    Text(store.targets.isEmpty ? "从第一个网站开始" : down > 0 ? "有 \(down) 个服务需要关注" : "每一份稳定，都有迹可循")
                        .font(.title2.bold())
                    HStack(spacing:0) {
                        metric("监测目标",value:store.targets.count)
                        metric("运行正常",value:online)
                        metric("服务异常",value:down)
                    }
                }.padding(24).background(LinearGradient(colors:[Color(red:0.07,green:0.26,blue:0.29),panel],startPoint:.topLeading,endPoint:.bottomTrailing),in:RoundedRectangle(cornerRadius:28))
                HStack {
                    Text("我的监测").font(.title3.bold()); Spacer()
                    Button(running ? "检测中…" : "立即检测",systemImage:"arrow.triangle.2.circlepath") {
                        running = true
                        Task {
                            do { let _: Acknowledgement = try await API.shared.request("/monitor/run",method:"POST"); await store.reload() }
                            catch { store.error = error.localizedDescription }
                            running = false
                        }
                    }.font(.caption).disabled(running || store.targets.isEmpty)
                }
                HStack(spacing:10) {
                    Image(systemName:"magnifyingglass").foregroundStyle(.secondary)
                    TextField("搜索名称或网址",text:$query).textInputAutocapitalization(.never).autocorrectionDisabled()
                    if !query.isEmpty { Button { query = "" } label: { Image(systemName:"xmark.circle.fill").foregroundStyle(.secondary) }.accessibilityLabel("清除搜索") }
                }.padding(14).background(panel,in:RoundedRectangle(cornerRadius:14))
                Picker("筛选",selection:$filter) { ForEach(["全部","异常","证书"],id:\.self) { Text($0).tag($0) } }.pickerStyle(.segmented)
                if visible.isEmpty {
                    ContentUnavailableView(store.targets.isEmpty ? "添加你的第一个网站" : "没有匹配的目标",systemImage:"globe",description:Text("网站状态、证书有效期和检测历史将在这里呈现。"))
                    if store.targets.isEmpty { Button("新增监测目标") { adding = true }.buttonStyle(.borderedProminent).frame(maxWidth:.infinity) }
                }
                ForEach(visible) { target in
                    NavigationLink(value:target.id) {
                        Surface {
                            VStack(alignment:.leading,spacing:16) {
                                HStack(alignment:.top) {
                                    Image(systemName:"globe.asia.australia.fill").font(.title2).foregroundStyle(.mint).frame(width:42,height:42).background(.mint.opacity(0.1),in:RoundedRectangle(cornerRadius:12))
                                    VStack(alignment:.leading,spacing:5) {
                                        Text(target.name).font(.headline).foregroundStyle(.primary)
                                        Text(target.url).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                    }; Spacer(); Image(systemName:"chevron.right").font(.caption).foregroundStyle(.secondary)
                                }
                                HStack {
                                    StatusPill(target:target); Spacer()
                                    Label(target.last_cert_error != nil ? "SSL · 异常" : target.last_cert_days.map { "SSL · \($0) 天" } ?? (target.url.hasPrefix("https") ? "SSL · 待检测" : "HTTP"),systemImage:"lock.shield")
                                        .font(.caption).foregroundStyle(target.last_cert_error != nil ? .orange : .secondary)
                                }
                            }
                        }
                    }.buttonStyle(.plain)
                }
                Text("下拉刷新 · 告警在服务器确认异常后自动发送")
                    .font(.caption2).foregroundStyle(.secondary).frame(maxWidth:.infinity)
            }.padding(20).frame(maxWidth:760).frame(maxWidth:.infinity)
        }.background(canvas).toolbar(.hidden,for:.navigationBar)
            .refreshable { await store.reload() }
            .sheet(isPresented:$adding) { TargetEditor(target:nil) }
            .task {
                while !Task.isCancelled {
                    await store.reload()
                    do { try await Task.sleep(for:.seconds(30)) } catch { break }
                }
            }
    }
    private func metric(_ title: String,value:Int) -> some View {
        VStack(alignment:.leading,spacing:5) { Text("\(value)").font(.system(size:32,weight:.semibold,design:.rounded)); Text(title).font(.caption).foregroundStyle(.secondary) }.frame(maxWidth:.infinity,alignment:.leading)
    }
}

struct TargetEditor: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    let target: Target?
    @State private var name = ""
    @State private var url = "https://"
    @State private var enabled = true
    @State private var saving = false
    @State private var error: String?
    @State private var showingNotificationPrompt = false
    @State private var canRequestNotifications = false
    var body: some View {
        NavigationStack {
            Form {
                Section("基本信息") {
                    TextField("网站名称",text:$name).accessibilityIdentifier("targetName")
                    TextField("https://example.com",text:$url).accessibilityIdentifier("targetURL").keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Toggle("启用监测",isOn:$enabled)
                }
                Section {
                    Label("自动检测网站可用性与 HTTPS 证书有效期",systemImage:"checkmark.shield")
                    if let profile = store.profile { Text("\(profile.plan_name) · 已用 \(profile.target_used) / \(profile.target_limit) 个目标") }
                }.foregroundStyle(.secondary).font(.subheadline)
                if let error { Text(error).foregroundStyle(.orange) }
            }.navigationTitle(target == nil ? "新增监测" : "编辑监测").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement:.cancellationAction) { Button("取消") { dismiss() } }
                    ToolbarItem(placement:.confirmationAction) { Button(saving ? "保存中…" : "保存") { Task { await save() } }.disabled(saving || name.trimmingCharacters(in:.whitespaces).isEmpty) }
                }
                .onAppear { if let target { name = target.name; url = target.url; enabled = target.enabled == 1 } }
                .task {
                    guard target == nil else { return }
                    let settings = await UNUserNotificationCenter.current().notificationSettings()
                    guard !Task.isCancelled else { return }
                    canRequestNotifications = settings.authorizationStatus == .notDetermined
                    showingNotificationPrompt = canRequestNotifications || settings.authorizationStatus == .denied || settings.alertSetting == .disabled
                }
                .alert("开启系统通知", isPresented:$showingNotificationPrompt) {
                    if canRequestNotifications {
                        Button("开启通知") { Task { await store.enablePush() } }
                    } else {
                        Button("前往设置") {
                            if let url = URL(string:UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        }
                    }
                    Button("暂不开启", role:.cancel) { }
                } message: {
                    Text("开启通知后，可及时收到网站异常和证书到期提醒。暂不开启也可继续新增监测目标。")
                }
        }
    }
    private func save() async {
        saving = true; defer { saving = false }
        do {
            let _: Target = try await API.shared.request(target.map { "/targets/\($0.id)" } ?? "/targets",method:target == nil ? "POST" : "PUT",body:["name":name.trimmingCharacters(in:.whitespaces),"url":url,"enabled":enabled])
            await store.reload(); dismiss()
        } catch { self.error = error.localizedDescription }
    }
}

struct TargetDetailView: View {
    let id: Int
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    @State private var target: Target?
    @State private var logs: [CheckLog] = []
    @State private var editing = false
    @State private var deleting = false
    @State private var hasMore = true
    @State private var loading = false
    @State private var error: String?
    @State private var filter = "all"
    @State private var requestID = UUID()
    private let pageSize = 20
    var body: some View {
        ScrollView {
            LazyVStack(alignment:.leading,spacing:20) {
                if let target {
                    Surface {
                        VStack(alignment:.leading,spacing:16) {
                            StatusPill(target:target)
                            Text(target.name).font(.largeTitle.bold())
                            Text(target.url).font(.subheadline).foregroundStyle(.secondary).textSelection(.enabled)
                            Divider()
                            LabeledContent("HTTP 状态",value:target.last_code.map(String.init) ?? "尚未检测")
                            LabeledContent("最后检测",value:target.last_checked_at ?? "等待首次检测")
                            if let error = target.last_error { Text(error).font(.caption).foregroundStyle(.orange) }
                        }
                    }
                    Surface {
                        VStack(alignment:.leading,spacing:14) {
                            Label("证书安全",systemImage:"lock.shield.fill").font(.headline).foregroundStyle(.mint)
                            Text(target.last_cert_days.map { "\($0) 天" } ?? (target.url.hasPrefix("https") ? "等待检测" : "HTTP 无 SSL 证书")).font(.title.bold())
                            if let expiry = target.last_cert_expires_at { Text("到期时间：\(expiry)").font(.caption).foregroundStyle(.secondary) }
                            if let error = target.last_cert_error { Text(error).font(.caption).foregroundStyle(.orange) }
                        }
                    }
                    Text("检测时间线").font(.title3.bold())
                    Picker("检测结果",selection:$filter) {
                        Text("全部记录").tag("all")
                        Text("仅异常").tag("abnormal")
                    }.pickerStyle(.segmented)
                    if loading && logs.isEmpty { ProgressView("加载记录…") }
                    if let error {
                        Text(error).font(.caption).foregroundStyle(.orange)
                        Button("重试加载") { Task { await load(more:!logs.isEmpty) } }
                    }
                    if logs.isEmpty && !loading && error == nil { ContentUnavailableView(filter == "abnormal" ? "暂无异常记录" : "暂无检测记录",systemImage:"clock",description:Text("可下拉刷新最新结果。")) }
                    ForEach(logs) { log in
                        Surface {
                            HStack(alignment:.top,spacing:14) {
                                Image(systemName:log.ok == 1 ? "checkmark.circle.fill" : "exclamationmark.circle.fill").foregroundStyle(log.ok == 1 ? .mint : .orange)
                                VStack(alignment:.leading,spacing:7) {
                                    Text(log.event_type == "certificate" ? "SSL 证书检测" : "网站可用性检测").font(.subheadline.bold())
                                    Text(log.checked_at + " · 北京时间").font(.caption2).foregroundStyle(.secondary)
                                    if let code = log.status_code { Text("HTTP \(code)").font(.caption).monospaced() }
                                    if let days = log.cert_days { Text("证书剩余 \(days) 天").font(.caption) }
                                    if let error = log.error { Text(error).font(.caption).foregroundStyle(.orange) }
                                }
                            }
                        }
                    }
                    if hasMore && !logs.isEmpty {
                        Button(loading ? "加载中…" : "加载更早记录") { Task { await load(more:true) } }
                            .disabled(loading).frame(maxWidth:.infinity)
                            .task(id:logs.last?.id) { if error == nil { await load(more:true) } }
                    }
                    if !hasMore && !logs.isEmpty { Text("已显示全部保留记录").font(.caption).foregroundStyle(.secondary) }
                    Button("删除监测目标",role:.destructive) { deleting = true }.frame(maxWidth:.infinity).padding()
                } else if loading { ProgressView().frame(maxWidth:.infinity) }
                else { ContentUnavailableView("无法加载目标",systemImage:"globe",description:Text(error ?? "目标可能已被删除")); Button("重试") { Task { await load() } } }
            }.padding(20).frame(maxWidth:760).frame(maxWidth:.infinity)
        }.background(canvas).navigationTitle("监测详情").navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("编辑") { editing = true }.disabled(target == nil) }
            .sheet(isPresented:$editing,onDismiss:{Task { await load() }}) { if let target { TargetEditor(target:target) } }
            .confirmationDialog("删除后将同时移除该目标的检测历史。",isPresented:$deleting,titleVisibility:.visible) {
                Button("删除目标",role:.destructive) {
                    Task {
                        do { let _: Acknowledgement = try await API.shared.request("/targets/\(id)",method:"DELETE"); await store.reload(); dismiss() }
                        catch { store.error = error.localizedDescription }
                    }
                }
            }
            .refreshable { await load() }.task(id:filter) { await load() }
            .onDisappear { requestID = UUID(); loading = false }
    }
    private func load(more:Bool = false) async {
        if more && (loading || !hasMore) { return }
        let currentRequest = UUID()
        requestID = currentRequest
        let selectedFilter = filter
        let cursor = more ? logs.last?.id : nil
        loading = true; error = nil
        if !more { logs = []; hasMore = true }
        defer { if requestID == currentRequest { loading = false } }
        do {
            if !more {
                let item: Target = try await API.shared.request("/targets/\(id)")
                try Task.checkCancellation()
                guard requestID == currentRequest else { return }
                target = item
            }
            var query = "?limit=\(pageSize)&result=\(selectedFilter)"
            if let cursor { query += "&before=\(cursor)" }
            let page: [CheckLog] = try await API.shared.request("/targets/\(id)/logs"+query)
            try Task.checkCancellation()
            guard requestID == currentRequest else { return }
            let existing = Set(logs.map(\.id))
            logs = more ? logs + page.filter { !existing.contains($0.id) } : page
            hasMore = page.count == pageSize
        } catch {
            guard requestID == currentRequest, !error.isRequestCancellation, !Task.isCancelled else { return }
            self.error = error.localizedDescription
        }
    }
}

struct InboxView: View {
    @EnvironmentObject var store: Store
    @State private var more = true
    @State private var loading = false
    var body: some View {
        ScrollView {
            LazyVStack(alignment:.leading,spacing:16) {
                Surface {
                    HStack { Image(systemName:"bell.badge.fill").font(.title).foregroundStyle(.mint); VStack(alignment:.leading,spacing:5) { Text("重要变化，不错过").font(.headline); Text("异常、恢复与证书提醒集中呈现").font(.caption).foregroundStyle(.secondary) } }
                }
                if store.notices.isEmpty { ContentUnavailableView("暂时没有告警",systemImage:"checkmark.shield",description:Text("有新的状态变化时，我们会在这里告诉你。")) }
                ForEach(store.notices) { notice in
                    Button {
                        Task {
                            do {
                                let _: Acknowledgement = try await API.shared.request("/notifications/\(notice.id)/read",method:"POST")
                                await store.reload()
                                if let id = notice.target_id { store.open(id) }
                            } catch { store.error = error.localizedDescription }
                        }
                    } label: {
                        Surface {
                            VStack(alignment:.leading,spacing:10) {
                                HStack { Circle().fill(notice.read_at == nil ? .mint : .gray).frame(width:7,height:7); Text(notice.title).font(.headline); Spacer(); Image(systemName:notice.kind == "service_recovered" ? "checkmark.circle" : "bell") }
                                Text(notice.body).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.leading)
                                Text(notice.created_at + " · 北京时间").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }.buttonStyle(.plain)
                }
                if more && store.notices.count >= 50 {
                    Button("加载更早通知") {
                        loading = true
                        Task {
                            do {
                                let page: [Notice] = try await API.shared.request("/notifications?before=\(store.notices.last?.id ?? Int.max)")
                                store.notices.append(contentsOf:page); more = page.count == 50
                            } catch { store.error = error.localizedDescription }
                            loading = false
                        }
                    }.disabled(loading)
                }
            }.padding(20).frame(maxWidth:760).frame(maxWidth:.infinity)
        }.background(canvas).navigationTitle("通知中心").refreshable { await store.reload(); more = true }
    }
}

struct AccountView: View {
    @EnvironmentObject var store: Store
    @State private var deleting = false
    @State private var signingOut = false
    @State private var oldPassword = ""
    @State private var newPassword = ""
    @State private var changing = false
    var body: some View {
        Form {
            if let profile = store.profile {
                Section {
                    HStack(spacing:16) {
                        Image(systemName:"person.crop.circle.fill").font(.system(size:54)).foregroundStyle(.mint)
                        VStack(alignment:.leading,spacing:6) { Text(profile.username).font(.title2.bold()); Text(profile.role == "admin" ? "管理员" : "BudMon 用户").foregroundStyle(.secondary) }
                    }.padding(.vertical,12)
                }
                Section("监测额度") {
                    LabeledContent("监测总额度",value:"\(profile.target_used) / \(profile.target_limit)")
                    ProgressView(value:Double(min(profile.target_used,profile.target_limit)),total:Double(max(1,profile.target_limit))).tint(.mint)
                    if let date = profile.plan_expires_at { Text("套餐到期：\(date) UTC").font(.caption) }
                    LabeledContent("永久已购名额", value: "\(profile.purchased_quota ?? 0) 个")
                    Text("每份永久增加 1 个监测名额，可重复购买，累计数量不限。名额绑定当前 BudMon 账号，换设备后登录同一账号即可使用。").font(.caption).foregroundStyle(.secondary)
                    if let product = store.purchaseProduct {
                        Button(store.purchasing ? "正在处理购买…" : "购买 1个永久监测目标 · \(product.displayPrice)") {
                            Task { await store.purchaseSlot() }
                        }.disabled(store.purchasing || !store.purchaseAvailable)
                    } else {
                        Button("加载购买选项") { Task { await store.loadPurchases() } }.disabled(store.purchasing)
                    }
                    Button("同步额度") { Task { await store.syncPurchases() } }.disabled(store.purchasing)
                    if let message = store.purchaseMessage { Text(message).font(.caption).foregroundStyle(.secondary) }
                }
                Section("即时通知") {
                    Toggle("接收推送告警",isOn:Binding(get:{store.profile?.push_enabled ?? true},set:{ value in
                        Task {
                            do { let _: Acknowledgement = try await API.shared.request("/me/preferences",method:"PUT",body:["push_enabled":value]); store.profile?.push_enabled = value }
                            catch { store.error = error.localizedDescription }
                        }
                    }))
                    Button("开启系统通知",systemImage:"bell.badge") { Task { await store.enablePush() } }
                    Text(store.pushStatus).font(.caption).foregroundStyle(.secondary)
                    Button("刷新推送状态",systemImage:"arrow.clockwise") { Task { await store.registerDevice() } }
                    Button("打开系统设置",systemImage:"gearshape") { if let url = URL(string:UIApplication.openSettingsURLString) { UIApplication.shared.open(url) } }
                }
                Section("账号安全") {
                    SecureField("原密码",text:$oldPassword)
                    SecureField("新密码（至少 8 位）",text:$newPassword)
                    Button(changing ? "修改中…" : "修改密码并重新登录") {
                        changing = true
                        Task {
                            do {
                                let _: Acknowledgement = try await API.shared.request("/auth/reset-password",method:"POST",body:["old_password":oldPassword,"new_password":newPassword])
                                store.expire()
                            } catch { store.error = error.localizedDescription }
                            changing = false
                        }
                    }.disabled(changing || oldPassword.isEmpty || newPassword.count < 8)
                }
                Section {
                    Button(signingOut ? "正在退出…" : "退出登录",role:.destructive) { signingOut = true; Task { await store.logout(); signingOut = false } }.disabled(signingOut)
                    if profile.role != "admin" { Button("删除账号",role:.destructive) { deleting = true } }
                } footer: { Text("BudMon · 为每一次稳定在线") }
            } else { Button("重新加载账号") { Task { await store.reload() } } }
        }.navigationTitle("我的账户")
            .confirmationDialog("永久删除账号、全部监测目标及历史记录？此操作不可恢复。",isPresented:$deleting,titleVisibility:.visible) {
                Button("永久删除账号",role:.destructive) {
                    Task {
                        do { let _: Acknowledgement = try await API.shared.request("/me",method:"DELETE"); store.expire() }
                        catch { store.error = error.localizedDescription }
                    }
                }
            }
    }
}
