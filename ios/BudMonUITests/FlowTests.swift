import XCTest

// This flow uses the fixed production API; explicit opt-in is required to create data.
final class FlowTests: XCTestCase {
    @MainActor func testFixedServerAuthenticationScreen() {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing"]
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout:15))
        XCTAssertTrue(app.secureTextFields["password"].exists)
        XCTAssertFalse(app.textFields["server"].exists)
        XCTAssertFalse(app.staticTexts["服务器地址"].exists)
        app.segmentedControls.buttons["注册账号"].tap()
        XCTAssertTrue(app.buttons["创建账号"].exists)
        XCTAssertFalse(app.textFields["server"].exists)
        XCTAssertFalse(app.staticTexts["服务器地址"].exists)
    }

    @MainActor func testRegisterCreateAndInspect() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["BUDMON_RUN_LIVE_UI_TESTS"] == "1", "Live API test requires explicit opt-in")
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing"]
        app.launch()
        XCTAssertTrue(app.textFields["username"].waitForExistence(timeout:15))
        XCTAssertFalse(app.textFields["server"].exists)
        screenshot("01-sign-in")
        app.segmentedControls.buttons["注册账号"].tap()
        app.textFields["username"].tap()
        app.textFields["username"].typeText("ios_"+UUID().uuidString.prefix(8))
        app.secureTextFields["password"].tap()
        for character in "testpassword123" { app.secureTextFields["password"].typeText(String(character)) }
        app.swipeUp()
        app.buttons["创建账号"].tap()
        XCTAssertTrue(app.buttons["新增监测目标"].firstMatch.waitForExistence(timeout:20))
        let later = app.buttons["以后"]
        if later.waitForExistence(timeout:3) { later.tap() }
        app.buttons["新增监测目标"].firstMatch.tap()
        app.textFields["targetName"].tap()
        app.textFields["targetName"].typeText("Production Website")
        let url = app.textFields["targetURL"]
        url.tap()
        url.typeText("example.com")
        app.buttons["保存"].tap()
        XCTAssertTrue(app.staticTexts["Production Website"].waitForExistence(timeout:20))
        screenshot("02-overview")
        app.staticTexts["Production Website"].tap()
        XCTAssertTrue(app.staticTexts["证书安全"].waitForExistence(timeout:10))
        screenshot("03-detail")
        app.navigationBars.buttons.element(boundBy:0).tap()
        app.tabBars.buttons["通知"].tap()
        XCTAssertTrue(app.staticTexts["重要变化，不错过"].waitForExistence(timeout:5))
        screenshot("04-notifications")
        app.tabBars.buttons["我的"].tap()
        XCTAssertTrue(app.staticTexts["监测额度"].waitForExistence(timeout:5))
        screenshot("05-account")
    }
    @MainActor private func screenshot(_ name: String) {
        let attachment = XCTAttachment(screenshot:XCUIApplication().screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
