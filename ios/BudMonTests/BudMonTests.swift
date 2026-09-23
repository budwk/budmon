import XCTest
import UserNotifications
@testable import BudMon
final class BudMonTests: XCTestCase {
    func testLegacyServerSettingCannotChangeAPIRequests() async throws {
        let previous = UserDefaults.standard.object(forKey: "server")
        UserDefaults.standard.set("http://old-self-hosted.example:8000", forKey: "server")
        defer {
            if let previous { UserDefaults.standard.set(previous, forKey: "server") }
            else { UserDefaults.standard.removeObject(forKey: "server") }
        }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [FixedEndpointProtocol.self]
        let api = API(session: URLSession(configuration: config))
        let result: Acknowledgement = try await api.request("/auth/login", method: "POST",
            body: ["username": "test", "password": "not-sent-to-network"], authenticated: false)
        XCTAssertTrue(result.ok)
    }
    func testReadRecoversAfterTimeout() async throws {
        try await checkTransportFailure("timeout-once", method: "GET", succeeds: true, attempts: 2)
    }
    func testReadRecoversAfterConnectionLoss() async throws {
        try await checkTransportFailure("connection-lost", method: "GET", succeeds: true, attempts: 2)
    }
    func testPersistentTimeoutStopsAfterOneRetry() async throws {
        try await checkTransportFailure("timeout-always", method: "GET", succeeds: false, attempts: 2)
    }
    func testTimedOutWriteIsNotReplayed() async throws {
        try await checkTransportFailure("timeout-once", method: "POST", succeeds: false, attempts: 1)
    }
    func testCancellationAndHTTPFailuresAreNotRetried() async throws {
        try await checkTransportFailure("cancelled", method: "GET", succeeds: false, attempts: 1)
        try await checkTransportFailure("http-error", method: "GET", succeeds: false, attempts: 1)
    }
    func testTimeoutMessageIsLocalizedWithoutHidingFailure() {
        XCTAssertEqual(URLError(.timedOut).requestErrorMessage, "连接服务器超时，请稍后重试")
        XCTAssertEqual(APIError(message: "服务异常").requestErrorMessage, "服务异常")
    }
    private func checkTransportFailure(_ scenario: String, method: String, succeeds: Bool, attempts: Int) async throws {
        let path = "/\(scenario)?test=\(UUID().uuidString)"
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RecoveryProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let api = API(session: session)
        do {
            let result: Acknowledgement = try await api.request(path, method: method, authenticated: false)
            XCTAssertTrue(succeeds, "Expected a failure")
            XCTAssertTrue(result.ok)
        } catch {
            XCTAssertFalse(succeeds, "Unexpected failure: \(error)")
        }
        XCTAssertEqual(RecoveryProtocol.count(for: "https://budmon.budwk.com/api" + path), attempts)
    }
    @MainActor func testAutomaticRefreshDoesNotPresentTimeoutButManualRefreshDoes() async {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [TimeoutProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let store = Store(api: API(session: session))
        store.authenticated = true
        await store.reload(showErrors: false)
        XCTAssertNil(store.error)
        XCTAssertTrue(store.authenticated)
        await store.reload()
        XCTAssertEqual(store.error, "连接服务器超时，请稍后重试")
    }
    @MainActor func testAutomaticDeviceRegistrationKeepsFailureInPushStatus() async {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [TimeoutProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let store = Store(api: API(session: session))
        store.authenticated = true
        store.pushToken = "test-device-token"
        await store.registerDevice(showErrors: false)
        XCTAssertNil(store.error)
        XCTAssertTrue(store.pushStatus.contains("失败"))
        await store.registerDevice()
        XCTAssertEqual(store.error, "连接服务器超时，请稍后重试")
    }
    func testHTTPSOnlyTransportConfiguration() {
        let ats = Bundle.main.object(forInfoDictionaryKey: "NSAppTransportSecurity") as? [String: Any]
        XCTAssertNotEqual(ats?["NSAllowsArbitraryLoads"] as? Bool, true)
    }
    @MainActor func testPushConfigurationStatusDistinguishesUnknownFromUnconfigured() {
        let unknown = Store.pushStatusMessage(configured:nil,authorization:.authorized)
        let missing = Store.pushStatusMessage(configured:false,authorization:.authorized)
        let ready = Store.pushStatusMessage(configured:true,authorization:.authorized)
        XCTAssertNotEqual(unknown, missing)
        XCTAssertNotEqual(missing, ready)
        XCTAssertTrue(unknown.contains("尚未获取"))
        XCTAssertTrue(ready.contains("已就绪"))
        XCTAssertTrue(Store.pushStatusMessage(configured:true,authorization:.denied).contains("开启系统通知权限"))
    }
    func testRequestCancellationIsNotAnApplicationFailure() {
        XCTAssertTrue(CancellationError().isRequestCancellation)
        XCTAssertTrue(URLError(.cancelled).isRequestCancellation)
        XCTAssertFalse(URLError(.timedOut).isRequestCancellation)
        XCTAssertFalse(APIError(message:"HTTP 500").isRequestCancellation)
    }
    func testTargetDecoding() throws {
        let data = Data(#"{"id":1,"name":"网站","url":"https://example.com","enabled":1,"last_status":"down"}"#.utf8)
        let target = try JSONDecoder().decode(Target.self,from:data)
        XCTAssertEqual(target.statusLabel,"服务异常")
        XCTAssertNil(target.last_cert_days)
    }
}


private final class FixedEndpointProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        XCTAssertEqual(request.url?.absoluteString, "https://budmon.budwk.com/api/auth/login")
        XCTAssertEqual(request.httpMethod, "POST")
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
            headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(#"{"ok":true}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private final class RecoveryProtocol: URLProtocol {
    private static let lock = NSLock()
    private static var counts: [String: Int] = [:]
    static func count(for url: String) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return counts[url, default: 0]
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let url = request.url!
        Self.lock.lock()
        Self.counts[url.absoluteString, default: 0] += 1
        let attempt = Self.counts[url.absoluteString]!
        Self.lock.unlock()
        let scenario = url.lastPathComponent
        if scenario == "cancelled" {
            client?.urlProtocol(self, didFailWithError: URLError(.cancelled))
        } else if scenario == "timeout-always" || (attempt == 1 && scenario != "http-error") {
            client?.urlProtocol(self, didFailWithError: URLError(scenario == "connection-lost" ? .networkConnectionLost : .timedOut))
        } else {
            let status = scenario == "http-error" ? 503 : 200
            let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(#"{"ok":true}"#.utf8))
            client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() {}
}

private final class TimeoutProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        client?.urlProtocol(self, didFailWithError: URLError(.timedOut))
    }
    override func stopLoading() {}
}
