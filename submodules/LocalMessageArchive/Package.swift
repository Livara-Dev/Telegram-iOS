// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "LocalMessageArchive",
    platforms: [.iOS(.v15), .macOS(.v12)],
    products: [.library(name: "LocalMessageArchive", targets: ["LocalMessageArchive"])],
    dependencies: [.package(path: "../sqlcipher")],
    targets: [
        .target(name: "LocalMessageArchive", dependencies: [.product(name: "sqlcipher", package: "sqlcipher")],
                linkerSettings: [.linkedFramework("Security"), .linkedFramework("Foundation")]),
        .testTarget(name: "LocalMessageArchiveTests", dependencies: ["LocalMessageArchive", .product(name: "sqlcipher", package: "sqlcipher")]),
    ]
)
