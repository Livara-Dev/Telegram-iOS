# Hosted macOS runner feasibility audit

Checked 2026-11-04 against GitHub's published runner image repository and runner documentation. This is an availability/configuration audit, not a hosted job execution. No project files were modified and no source/toolchain pins were changed.

## Conclusion

A Windows-only developer **can use GitHub-hosted macOS for this repository without owning a Mac**. The current feasible image label is **`xcode-27`**, an ARM64 public-preview image. It advertises macOS 27.0.1 and Xcode 27.1, matching this project's macOS major and exact Xcode pins. Do not use `macos-latest` for this checkout: its current mapping is macOS 26. The remaining issues are missing preinstalled iOS 26.5 test runtime, build capacity that has not been measured, repository/Actions access and the user's API/signing credentials. None establishes a successful baseline or IPA yet.

## Pinned evidence

The inspected `actions/runner-images` main revision was **`a2d4955654f519bd5b559bffaea60d9f90156630`**, committed 2026-11-03T15:51:07Z. The image index and both relevant manifests were re-fetched by that SHA after resolving main so these links are reproducible:

- [Image index](https://github.com/actions/runner-images/blob/a2d4955654f519bd5b559bffaea60d9f90156630/README.md)
- [Xcode 27 ARM64 image](https://github.com/actions/runner-images/blob/a2d4955654f519bd5b559bffaea60d9f90156630/images/macos/xcode-27-arm64-Readme.md)
- [macOS 26 ARM64 image](https://github.com/actions/runner-images/blob/a2d4955654f519bd5b559bffaea60d9f90156630/images/macos/macos-26-arm64-Readme.md)
- [Preview announcement](https://github.com/actions/runner-images/issues/14404), updated September 16 to state that the image now uses macOS 27 as its base OS.

| Property | `xcode-27` published image | Current project requirement | Result |
|---|---|---|---|
| Architecture | ARM64 | `debug_sim_arm64`/`release_arm64` build modes | Appropriate architecture |
| macOS | 27.0.1, build 26A434 | `versions.json`: 27 | Advertised major matches |
| Image version | 20261026.0314.1 | Record actual image in job evidence | Not an immutable workflow label |
| Xcode | 27.1, build 27A9275, default | `versions.json`: 27.1 | Advertised exact version matches |
| Explicit developer directory | `/Applications/Xcode_27.1.app/Contents/Developer` | Avoid default drift | Select explicitly and verify |
| Other Xcodes | 27.0 and 27.2 release candidate | Do not substitute | No silent upgrade/downgrade |
| Installed iOS simulator runtimes | 27.0 and 27.1 | Postbox/Core test runners: iOS 26.5, iPhone 17 | Additional runtime required or an explicit separately reviewed test-runner change |
| iPhone 17 simulator | Listed under iOS 27.0 | iPhone 17 / iOS 26.5 pairing | Device name alone is insufficient |

By comparison, `macos-latest`/`macos-26` maps to the image advertising macOS 26.6.2 and Xcode 26.6 default. That image has iOS 26.5 but not the pinned compiler/host environment. It is not a substitute baseline.

## Concrete hosted configuration direction

Use `runs-on: xcode-27` with an explicit `DEVELOPER_DIR=/Applications/Xcode_27.1.app/Contents/Developer`. Fail early if `sw_vers`, `uname -m`, or `xcodebuild -version` disagrees with the project pins. Record the runner image version, selected developer directory, SDKs, runtimes, checkout SHA, submodule SHAs, dependency checksums and successful build/test commands. Keep `versions.json` unchanged and do not invoke `--overrideXcodeVersion` to mask a mismatch.

Run the native `Make.py build`/`test` commands. GitHub documents that ARM64 hosted macOS runners do not support nested virtualization; the repository's Tart `vm-build` approach must not be transplanted into this hosted VM. [GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)

Apple documents selecting an OS version with `xcodebuild -downloadPlatform ... -buildVersion ...` and an ARM64 variant with `-architectureVariant arm64`. An appropriate attempt under the selected Xcode is:

```sh
xcodebuild -downloadPlatform iOS -buildVersion 26.5 -architectureVariant arm64
xcrun simctl list runtimes --json
xcrun simctl list devicetypes --json
```

This command is a proposed runtime setup step, **not a confirmed download**. Apple availability, compatibility, disk space and creation/boot of the matching simulator must pass in the actual job. If it fails, the existing test target remains blocked; a build alone is not a substitute for test success. [Apple's component-download documentation](https://developer.apple.com/documentation/xcode/downloading-and-installing-additional-xcode-components)

## Capacity, access and signing limits

GitHub documents the standard ARM64 runner as 3 M1 CPU cores, 7 GB RAM and 14 GB SSD. This is an available low-friction starting point, but a clean full Telegram build plus dependencies and an extra runtime may exceed resources. Measure free space before/after checkout, dependency fetch and build, and retain build failure evidence. This audit has not measured a successful cold build or established sufficient headroom. [Standard runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)

The published `xcode-27-xlarge` option offers 5 M2 CPU cores and 14 GB RAM, but its documented SSD allocation is still 14 GB; it does not by itself resolve a disk shortage. It requires eligible organization/enterprise access and billing, which have not been verified. Do not silently switch to a paid runner. [Larger runner specifications and availability](https://docs.github.com/en/actions/reference/runners/larger-runners)

The image remains public preview, so queueing/capacity and image changes are possible. Its documented existence does not verify repository permissions, Actions policy, billing limits or successful scheduling for this user. The workflow should expose actionable errors rather than claiming the baseline passed.

An unsigned simulator build does not require an Apple distribution identity. An independently signed device IPA still requires the user's Apple team, certificate/private key, matching app and extension provisioning profiles, bundle IDs/entitlements and own Telegram API ID/hash. These can be supplied through GitHub secrets from Windows; they must not be committed, printed, or included in artifacts. Build only synthetic-test data on a hosted service so archived user messages never enter runner logs or uploaded artifacts. Signature verification and a device installation check remain separate acceptance gates after an IPA is actually produced.
