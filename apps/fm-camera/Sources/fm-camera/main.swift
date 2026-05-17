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
// UI mode
// ───────────────────────────────────────────────────────────────────

@MainActor
final class UIState: ObservableObject {
    @Published var devices: [AVCaptureDevice] = []
    @Published var selectedDeviceName: String = ""
    @Published var outputPath: String = "/tmp/fruit-market-smoke.jpg"
    @Published var status: String = "Click Capture to snap a JPEG."
    @Published var lastImage: NSImage? = nil

    func refreshDevices() {
        let ds = videoDevices()
        self.devices = ds
        if selectedDeviceName.isEmpty {
            // Prefer the C920 if present, otherwise the first device.
            if let c920 = ds.first(where: { $0.localizedName.contains("C920") }) {
                selectedDeviceName = c920.localizedName
            } else if let first = ds.first {
                selectedDeviceName = first.localizedName
            }
        }
    }

    func capture() {
        let device = selectedDeviceName
        let path = outputPath
        self.status = "Capturing from \(device)…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            // Use the device's full name as the query so we get an
            // exact match on the picker selection.
            do {
                let data = try captureOneJPEG(deviceQuery: device)
                try data.write(to: URL(fileURLWithPath: path))
                let img = NSImage(data: data)
                DispatchQueue.main.async {
                    self.lastImage = img
                    self.status = "\(data.count) bytes -> \(path)"
                }
            } catch {
                let message: String
                switch error {
                case CaptureError.noDevice(let q, let avail):
                    message = "device not found: \(q). available: \(avail.joined(separator: ", "))"
                case CaptureError.timedOut:
                    message = "capture timed out after 10s"
                case CaptureError.noData:
                    message = "capture returned no data"
                default:
                    message = "capture failed: \(error)"
                }
                DispatchQueue.main.async { self.status = message }
            }
        }
    }
}

struct ContentView: View {
    @ObservedObject var state: UIState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Fruit Market — camera bridge")
                .font(.title2)

            HStack {
                Text("Camera:")
                Picker("", selection: $state.selectedDeviceName) {
                    ForEach(state.devices, id: \.uniqueID) { dev in
                        Text(dev.localizedName).tag(dev.localizedName)
                    }
                }
                .pickerStyle(.menu)
                .frame(maxWidth: 320)
                Button("Refresh") { state.refreshDevices() }
            }

            HStack {
                Text("Output:")
                TextField("/tmp/fruit-market-smoke.jpg", text: $state.outputPath)
            }

            Button {
                state.capture()
            } label: {
                Label("Capture", systemImage: "camera.shutter.button")
                    .frame(maxWidth: .infinity)
            }
            .controlSize(.large)
            .buttonStyle(.borderedProminent)

            Divider()

            Text(state.status)
                .foregroundStyle(.secondary)
                .font(.callout)

            if let img = state.lastImage {
                Image(nsImage: img)
                    .resizable()
                    .scaledToFit()
                    .frame(maxHeight: 320)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.secondary.opacity(0.1))
                    .frame(height: 320)
                    .overlay(Text("No capture yet").foregroundStyle(.secondary))
            }
        }
        .padding(20)
        .frame(minWidth: 540, minHeight: 600)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    // UIState is @MainActor-isolated; defer construction until
    // applicationDidFinishLaunching, which AppKit guarantees runs on
    // the main thread. That lets the top-level entry point stay
    // outside main-actor isolation.
    var state: UIState!
    var window: NSWindow!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let state = UIState()
        self.state = state
        NSApp.setActivationPolicy(.regular)
        state.refreshDevices()
        let host = NSHostingController(rootView: ContentView(state: state))
        window = NSWindow(contentViewController: host)
        window.title = "Fruit Market Camera"
        window.setContentSize(NSSize(width: 600, height: 720))
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        // Prompt for camera access immediately so the dialog appears
        // before the user clicks Capture (less surprising).
        DispatchQueue.global(qos: .userInitiated).async {
            _ = ensureCameraAuthorized()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
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
