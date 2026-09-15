import XCTest
import UserNotifications
@testable import BudMon
final class BudMonTests: XCTestCase {
    func testServerValidation() throws {
        XCTAssertEqual(try API.validatedServer(" https://monitor.example.com/ "),"https://monitor.example.com")
        XCTAssertNoThrow(try API.validatedServer("http://localhost:8000"))
        XCTAssertNoThrow(try API.validatedServer("http://example.com"))
        XCTAssertEqual(try API.validatedServer("http://203.0.113.10:9977/"), "http://203.0.113.10:9977")
        XCTAssertThrowsError(try API.validatedServer("ftp://example.com"))
        XCTAssertThrowsError(try API.validatedServer("https://user:pass@example.com"))
        XCTAssertThrowsError(try API.validatedServer("https://example.com/api"))
    }
    func testHTTPTransportConfiguration() {
        let ats = Bundle.main.object(forInfoDictionaryKey: "NSAppTransportSecurity") as? [String: Any]
        XCTAssertEqual(ats?["NSAllowsArbitraryLoads"] as? Bool, true)
        // On modern iOS, these keys override NSAllowsArbitraryLoads even when false.
        for key in ["NSAllowsLocalNetworking", "NSAllowsArbitraryLoadsForMedia", "NSAllowsArbitraryLoadsInWebContent"] {
            XCTAssertNil(ats?[key])
        }
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
