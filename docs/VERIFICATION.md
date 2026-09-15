# 验证记录

2026-09-14，本机 Python 3.12.12、Xcode 26.6、iPhone 17 Pro / iOS 26.5 模拟器。

- 后端：12 项 pytest 全部通过。覆盖租户隔离与管理员权限、并发配额、到期降级、会话刷新与撤销、设备换绑与重复注册、异常去重与恢复、APNs 成功/重试/无效 token、关闭推送、停用账号、SSRF 地址过滤、过期检测结果丢弃、账号删除、旧库迁移。
- 网页：`npm run build` 成功。保留 Vite 大包提示，未阻塞构建。
- iOS：完整模拟器构建成功，2 项 XCTest 单元测试通过。
- iOS 端到端：BudMonUI scheme 通过，真实连接临时本地后台完成注册、创建目标、打开详情、通知与账户页面。截图保存在 `docs/screenshots`。
- `git diff --check` 通过。

APNs HTTP 响应由测试替身覆盖；没有 Apple 私钥与签名真机，因此未验证真实 APNs 推送到达。未部署公网、未上传 App Store、未接入或执行真实付款。生产部署仍需按 IOS.md 配置服务器访问地址、Apple Team / Bundle ID / APNs 密钥。
