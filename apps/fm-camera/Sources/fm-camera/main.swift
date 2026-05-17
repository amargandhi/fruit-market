// fm-camera — macOS camera capture for Fruit Market.
//
// Two modes:
//
// 1. UI mode (no args)
//
//    Launches a SwiftUI window with a camera picker, a Capture
//    button, and a status line. Used the first time so macOS pops
//    the TCC dialog and the user can grant permission to this
//    specific .app bundle. The grant sticks against the bundle's
//    code signature.
//
//    Run via:
//      $ open apps/fm-camera/FruitMarketCamera.app
//
// 2. CLI mode (2 args: device-substring, output-path)
//
//    Captures one JPEG and exits. Used by Python after the .app has
//    been granted permission.
//
//    Run via:
//      $ apps/fm-camera/FruitMarketCamera.app/Contents/MacOS/fm-camera \
//          "HD Pro Webcam C920" /tmp/snapshot.jpg
//
// Exit codes:
//   0 — success
//   2 — bad arguments (CLI mode)
//   3 — device not found
//   4 — capture pipeline failed
//   5 — camera access denied
import AVFoundation
import AppKit
import SwiftUI

// ───────────────────────────────────────────────────────────────────
// Shared helpers
// ───────────────────────────────────────────────────────────────────

func stderr(_ s: String) {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
}

func videoDevices() -> [AVCaptureDevice] {
    AVCaptureDevice.DiscoverySession(
        deviceTypes: [
            .builtInWideAngleCamera,
            .external,
            .continuityCamera,
        ],
        mediaType: .video,
        position: .unspecified
    ).devices
}

func ensureCameraAuthorized() -> Bool {
    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized:
        return true
    case .notDetermined:
        let sem = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .video) { ok in
            granted = ok
            sem.signal()
        }
        sem.wait()
        return granted
    case .denied, .restricted:
        return false
    @unknown default:
        return false
    }
}

final class CaptureDelegate: NSObject, AVCapturePhotoCaptureDelegate {
    let semaphore = DispatchSemaphore(value: 0)
    var data: Data?
    var captureError: Error?

    func photoOutput(
        _ output: AVCapturePhotoOutput,
        didFinishProcessingPhoto photo: AVCapturePhoto,
        error: Error?
    ) {
        self.captureError = error
        self.data = photo.fileDataRepresentation()
        semaphore.signal()
    }
}

enum CaptureError: Error {
    case noDevice(String, available: [String])
    case inputCreation(String)
    case cannotAddOutput
    case timedOut
    case noData
    case underlying(Error)
}

/// Synchronously snap one JPEG from the first device whose name
/// contains ``deviceQuery`` (case-insensitive).
func captureOneJPEG(deviceQuery: String) throws -> Data {
    let devices = videoDevices()
    guard let device = devices.first(where: {
        $0.localizedName.lowercased().contains(deviceQuery.lowercased())
    }) else {
        throw CaptureError.noDevice(
            deviceQuery,
            available: devices.map { $0.localizedName }
        )
    }

    let session = AVCaptureSession()
    session.sessionPreset = .high
    do {
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else {
            throw CaptureError.inputCreation(device.localizedName)
        }
        session.addInput(input)
    } catch let err as CaptureError {
        throw err
    } catch {
        throw CaptureError.underlying(error)
    }

    let output = AVCapturePhotoOutput()
    guard session.canAddOutput(output) else {
        throw CaptureError.cannotAddOutput
    }
    session.addOutput(output)
    session.startRunning()
    // Let auto-exposure / white balance settle.
    Thread.sleep(forTimeInterval: 1.0)

    let delegate = CaptureDelegate()
    let settings = AVCapturePhotoSettings(
        format: [AVVideoCodecKey: AVVideoCodecType.jpeg]
    )
    output.capturePhoto(with: settings, delegate: delegate)
    let result = delegate.semaphore.wait(timeout: .now() + 10)
    session.stopRunning()

    if result == .timedOut {
        throw CaptureError.timedOut
    }
    if let err = delegate.captureError {
        throw CaptureError.underlying(err)
    }
    guard let payload = delegate.data, !payload.isEmpty else {
        throw CaptureError.noData
    }
    return payload
}

// ───────────────────────────────────────────────────────────────────
// CLI mode
// ───────────────────────────────────────────────────────────────────

func runCLI(deviceQuery: String, outputPath: String) -> Never {
    guard ensureCameraAuthorized() else {
        stderr(
            "camera access denied. Launch FruitMarketCamera.app from "
            + "Finder (or `open`) once and click Allow on the dialog, "
            + "then retry."
        )
        exit(5)
    }
    do {
        let data = try captureOneJPEG(deviceQuery: deviceQuery)
        try data.write(to: URL(fileURLWithPath: outputPath))
        print("\(data.count) bytes -> \(outputPath)")
        exit(0)
    } catch CaptureError.noDevice(let q, let avail) {
        stderr("device not found: \(q)")
        stderr("available: " + avail.joined(separator: ", "))
        exit(3)
    } catch CaptureError.timedOut {
        stderr("capture timed out after 10s")
        exit(4)
    } catch CaptureError.noData {
        stderr("capture returned no data")
        exit(4)
    } catch CaptureError.inputCreation(let name) {
        stderr("input creation failed for \(name)")
        exit(4)
    } catch CaptureError.cannotAddOutput {
        stderr("cannot add photo output to session")
        exit(4)
    } catch CaptureError.underlying(let err) {
        stderr("capture error: \(err.localizedDescription)")
        exit(4)
    } catch {
        stderr("unexpected error: \(error)")
        exit(4)
    }
}

// ───────────────────────────────────────────────────────────────────
// UI mode — dead simple: launch, prompt for camera, capture from the
// preferred device (default C920), save to /tmp, show alert, exit.
// No persistent window; no SwiftUI dance. The whole point is to give
// the .app bundle a chance to be granted camera permission once;
// subsequent invocations should use CLI mode.
// ───────────────────────────────────────────────────────────────────

let DEFAULT_DEVICE_QUERY = "C920"
let DEFAULT_OUTPUT_PATH = "/tmp/fruit-market-smoke.jpg"

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)

        // Do the work off the main thread so the run loop stays
        // responsive enough for the TCC permission dialog.
        DispatchQueue.global(qos: .userInitiated).async {
            guard ensureCameraAuthorized() else {
                DispatchQueue.main.async {
                    self.showAlert(
                        title: "Camera access denied",
                        message: "Grant camera permission to "
                            + "FruitMarketCamera in System Settings "
                            + "> Privacy & Security > Camera, then "
                            + "relaunch this app.",
                        style: .critical
                    )
                    NSApp.terminate(nil)
                }
                return
            }

            let deviceQuery = ProcessInfo.processInfo.environment["FM_CAMERA_DEVICE"]
                ?? DEFAULT_DEVICE_QUERY
            let outputPath = ProcessInfo.processInfo.environment["FM_CAMERA_OUTPUT"]
                ?? DEFAULT_OUTPUT_PATH

            do {
                let data = try captureOneJPEG(deviceQuery: deviceQuery)
                try data.write(to: URL(fileURLWithPath: outputPath))
                DispatchQueue.main.async {
                    self.showAlert(
                        title: "Captured \(data.count) bytes",
                        message: "Saved to \(outputPath)\n\n"
                            + "Device: \(deviceQuery)\n\n"
                            + "You can close this app now. "
                            + "Future captures from Python will use "
                            + "fm-camera in CLI mode.",
                        style: .informational
                    )
                    NSApp.terminate(nil)
                }
            } catch {
                let detail: String
                switch error {
                case CaptureError.noDevice(let q, let avail):
                    detail = "Device not found: \(q)\n\nAvailable cameras:\n"
                        + avail.map { "• \($0)" }.joined(separator: "\n")
                        + "\n\nSet FM_CAMERA_DEVICE to a substring of one "
                        + "of the names above."
                case CaptureError.timedOut:
                    detail = "Capture timed out after 10s."
                case CaptureError.noData:
                    detail = "Capture returned no data."
                default:
                    detail = "\(error)"
                }
                DispatchQueue.main.async {
                    self.showAlert(
                        title: "Capture failed",
                        message: detail,
                        style: .critical
                    )
                    NSApp.terminate(nil)
                }
            }
        }
    }

    func showAlert(title: String, message: String, style: NSAlert.Style) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = style
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

func runUI() -> Never {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.run()
    exit(0)
}

// ───────────────────────────────────────────────────────────────────
// Entry point
// ───────────────────────────────────────────────────────────────────

let argv = CommandLine.arguments
if argv.count >= 3 {
    runCLI(deviceQuery: argv[1], outputPath: argv[2])
}
runUI()
