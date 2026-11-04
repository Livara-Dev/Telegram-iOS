// SPDX-License-Identifier: GPL-2.0-or-later
import UIKit

/// Hosts XCTest and the production presentation resources. It creates no
/// Telegram account, session, network connection or messaging interface.
@main
final class ChatLocalTextFilterTestAppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = UIViewController()
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
}
