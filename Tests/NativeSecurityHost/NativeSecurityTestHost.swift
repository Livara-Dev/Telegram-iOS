// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit

/// Optional lifecycle evidence for one synthetic projection host. Other test
/// hosts return before any filesystem work. Values are fixed stages only;
/// there are no account identifiers, paths, credentials or exception payloads.
private enum ProjectionHostDiagnostics {
    enum Stage: String {
        case didFinish
        case sceneVisible
    }

    static func mark(_ stage: Stage) {
        guard Bundle.main.object(forInfoDictionaryKey: "AyuGramProjectionHostDiagnostics") as? Bool == true,
              let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first,
              let bytes = try? JSONSerialization.data(withJSONObject: ["stage": stage.rawValue], options: [.sortedKeys]),
              bytes.count <= 128 else { return }
        let directory = caches.appendingPathComponent("AyuGramNativeTestHost", isDirectory: true)
        do {
            // Bounded synchronous evidence in this opt-in test fixture only:
            // a failed pre-main/injection path must not be confused with a
            // later standalone launch. The collector reads before that launch.
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try bytes.write(to: directory.appendingPathComponent("projection-lifecycle.json"), options: [.atomic])
        } catch {
            // Never print exception descriptions, which may contain paths.
        }
    }
}

/// System-security and media framework tests need an application identity and
/// lifecycle. This host creates no Telegram account, store or network client.
@main
@objc(NativeSecurityTestAppDelegate)
final class NativeSecurityTestAppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        ProjectionHostDiagnostics.mark(.didFinish)
        return true
    }

    func application(_ application: UIApplication, configurationForConnecting connectingSceneSession: UISceneSession, options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let configuration = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)
        if connectingSceneSession.role == .windowApplication {
            configuration.delegateClass = NativeSecurityTestSceneDelegate.self
        }
        return configuration
    }
}

@objc(NativeSecurityTestSceneDelegate)
final class NativeSecurityTestSceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard session.role == .windowApplication, let scene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: scene)
        let controller = UIViewController()
        controller.view.backgroundColor = .systemBackground
        window.rootViewController = controller
        window.makeKeyAndVisible()
        self.window = window
        if window.windowScene != nil && !window.isHidden {
            ProjectionHostDiagnostics.mark(.sceneVisible)
        }
    }
}
