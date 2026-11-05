// SPDX-License-Identifier: GPL-2.0-or-later
import UIKit
import CustomeOptionsFixture

@main
@objc(CustomeOptionsHostAppDelegate)
final class CustomeOptionsHostAppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool { return true }

    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession, options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let configuration = UISceneConfiguration(name: "Default Configuration", sessionRole: session.role)
        configuration.delegateClass = CustomeOptionsHostSceneDelegate.self
        return configuration
    }
}

@objc(CustomeOptionsHostSceneDelegate)
final class CustomeOptionsHostSceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    private var fixture: CustomeOptionsFixture?
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard session.role == .windowApplication, let scene = scene as? UIWindowScene else { return }
        let fixture = CustomeOptionsFixture()
        fixture.install(in: scene)
        self.fixture = fixture
        self.window = fixture.window
    }
}
