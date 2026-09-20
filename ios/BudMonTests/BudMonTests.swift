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
