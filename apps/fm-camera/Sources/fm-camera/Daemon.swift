// Continuous-capture daemon mode for fm-camera.
//
// Runs an AVCaptureSession the entire process lifetime, encodes every
// delivered frame to JPEG, and serves the most recent JPEG over a
// tiny localhost HTTP server. The fruit-market Python backend's
// ``DaemonCamera`` adapter connects to that endpoint and reads
// frames as fast as it wants — no TCC fight on the Python side
// because all the camera work happens inside this code-signed .app
// bundle, which has its own permission grant.
//
// Endpoints:
//   GET /frame.jpg → 200 image/jpeg + most recent frame bytes
//                   (or 503 if the session hasn't produced a frame yet)
//   GET /status    → 200 application/json with capture telemetry
//   anything else  → 404 text/plain
//
// CLI:
//   fm-camera --daemon [--device "C920"] [--port 8765]

import AVFoundation
import AppKit
import CoreImage
import CoreImage.CIFilterBuiltins
import CoreVideo
import Foundation
import Network

// ─── Frame cache + JPEG encoder ─────────────────────────────────────


final class FrameCache {
    private var data: Data?
    private var monotonicTimestamp: Double = 0
    private var frameCount: Int = 0
    private let lock = NSLock()

    func record(_ bytes: Data) {
        lock.lock()
        data = bytes
        monotonicTimestamp = ProcessInfo.processInfo.systemUptime
        frameCount += 1
        lock.unlock()
    }

    func latest() -> Data? {
        lock.lock()
        defer { lock.unlock() }
        return data
    }

    func ageSeconds() -> Double? {
        lock.lock()
        defer { lock.unlock() }
        if monotonicTimestamp == 0 { return nil }
        return ProcessInfo.processInfo.systemUptime - monotonicTimestamp
    }

    func captures() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return frameCount
    }
}


final class JPEGEncoder {
    // CIContext is expensive to create; one instance per process.
    // Sharing across the sample-buffer queue is fine — Apple's docs
    // explicitly say CIContext is thread-safe.
    private let context = CIContext(options: [.useSoftwareRenderer: false])
    private let jpegOptions: [CIImageRepresentationOption: Any] = [
        kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.78,
    ]
    private let colorSpace = CGColorSpaceCreateDeviceRGB()

    func encode(_ pixelBuffer: CVPixelBuffer) -> Data? {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        return context.jpegRepresentation(
            of: ciImage,
            colorSpace: colorSpace,
            options: jpegOptions
        )
    }
}


// ─── Capture session that feeds the cache ──────────────────────────


final class ContinuousCapture: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let cache = FrameCache()
    let deviceName: String

    private let session = AVCaptureSession()
    private let videoQueue = DispatchQueue(label: "fm.camera.video", qos: .userInitiated)
    private let encoder = JPEGEncoder()
    private var started = false

    init(deviceName: String) {
        self.deviceName = deviceName
        super.init()
    }

    func start() throws {
        guard ensureCameraAuthorized() else {
            throw NSError(
                domain: "fm.daemon", code: 5,
                userInfo: [NSLocalizedDescriptionKey:
                    "camera access denied. Grant in System Settings → Privacy → Camera"]
            )
        }

        let devices = videoDevices()
        guard let device = devices.first(where: {
            $0.localizedName.lowercased().contains(deviceName.lowercased())
        }) else {
            let avail = devices.map { $0.localizedName }.joined(separator: ", ")
            throw NSError(
                domain: "fm.daemon", code: 3,
                userInfo: [NSLocalizedDescriptionKey:
                    "device not found: \(deviceName). available: \(avail)"]
            )
        }

        session.sessionPreset = .hd1280x720
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else {
            throw NSError(domain: "fm.daemon", code: 4,
                          userInfo: [NSLocalizedDescriptionKey: "cannot add input"])
        }
        session.addInput(input)

        let output = AVCaptureVideoDataOutput()
        // BGRA so CIContext can encode without an extra colour-space hop.
        output.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
        ]
        // Drop frames if Python is slow — we always want the latest,
        // not a backlog. `alwaysDiscardsLateVideoFrames` makes the
        // pipeline drop instead of queue.
        output.alwaysDiscardsLateVideoFrames = true
        output.setSampleBufferDelegate(self, queue: videoQueue)
        guard session.canAddOutput(output) else {
            throw NSError(domain: "fm.daemon", code: 4,
                          userInfo: [NSLocalizedDescriptionKey: "cannot add video output"])
        }
        session.addOutput(output)

        session.startRunning()
        started = true
    }

    func stop() {
        guard started else { return }
        session.stopRunning()
        started = false
    }

    // AVCaptureVideoDataOutputSampleBufferDelegate
    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        guard let jpeg = encoder.encode(pixelBuffer) else { return }
        cache.record(jpeg)
    }
}


// ─── Tiny HTTP server on localhost ─────────────────────────────────


final class DaemonServer {
    private let port: NWEndpoint.Port
    private let cache: FrameCache
    private let deviceName: String
    private let queue = DispatchQueue(label: "fm.daemon.http")
    private var listener: NWListener?

    init(port: UInt16, cache: FrameCache, deviceName: String) throws {
        self.port = NWEndpoint.Port(integerLiteral: port)
        self.cache = cache
        self.deviceName = deviceName
    }

    func start() throws {
        // Bind to localhost-only so the daemon never accidentally
        // becomes a public webcam endpoint. NWParameters.acceptLocalOnly
        // restricts the listener to loopback; the ``on: port`` arg is
        // what actually opens the socket — passing the port via
        // ``requiredLocalEndpoint`` alone leaves the listener bound to
        // an OS-chosen ephemeral port.
        let params = NWParameters.tcp
        params.acceptLocalOnly = true
        params.allowLocalEndpointReuse = true
        let listener = try NWListener(using: params, on: port)
        listener.newConnectionHandler = { [weak self] connection in
            self?.handle(connection: connection)
        }
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready:
                NSLog("fm-camera daemon: HTTP listener ready on port %d",
                      Int(self.port.rawValue))
            case .failed(let error):
                NSLog("fm-camera daemon: HTTP listener failed: %{public}@",
                      "\(error)")
            default:
                break
            }
        }
        listener.start(queue: queue)
        self.listener = listener
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    private func handle(connection: NWConnection) {
        connection.start(queue: queue)
        connection.receive(
            minimumIncompleteLength: 1,
            maximumLength: 8192
        ) { [weak self] data, _, _, _ in
            guard let self else { connection.cancel(); return }
            let path = Self.parseRequestPath(data: data)
            let (status, headers, body) = self.respond(path: path)
            self.write(connection: connection, status: status, headers: headers, body: body)
        }
    }

    private static func parseRequestPath(data: Data?) -> String {
        guard let data else { return "" }
        guard let text = String(data: data, encoding: .utf8) else { return "" }
        // Parse the very first line: "GET /path HTTP/1.1\r\n..."
        let firstLine = text.split(separator: "\r\n").first ?? ""
        let parts = firstLine.split(separator: " ")
        guard parts.count >= 2 else { return "" }
        return String(parts[1])
    }

    private func respond(path: String) -> (Int, [String: String], Data) {
        switch path {
        case "/frame.jpg", "/frame":
            if let body = cache.latest() {
                return (
                    200,
                    [
                        "Content-Type": "image/jpeg",
                        "Cache-Control": "no-store, max-age=0",
                        "X-Camera-Status": "ok",
                        "X-Frame-Age-Seconds": String(
                            format: "%.3f", cache.ageSeconds() ?? 0
                        ),
                    ],
                    body
                )
            }
            return (503, ["Content-Type": "text/plain", "X-Camera-Status": "starting"],
                    "no frame yet".data(using: .utf8)!)
        case "/status":
            let payload: [String: Any] = [
                "enabled": true,
                "device": deviceName,
                "captures": cache.captures(),
                "frame_age_seconds": cache.ageSeconds() ?? NSNull(),
                "frame_bytes": cache.latest()?.count ?? 0,
            ]
            let body = (try? JSONSerialization.data(
                withJSONObject: payload,
                options: [.sortedKeys]
            )) ?? "{}".data(using: .utf8)!
            return (200, ["Content-Type": "application/json",
                          "Cache-Control": "no-store"], body)
        default:
            return (404, ["Content-Type": "text/plain"],
                    "fm-camera daemon: try /frame.jpg or /status\n".data(using: .utf8)!)
        }
    }

    private func write(
        connection: NWConnection,
        status: Int,
        headers: [String: String],
        body: Data
    ) {
        let reason: String
        switch status {
        case 200: reason = "OK"
        case 404: reason = "Not Found"
        case 503: reason = "Service Unavailable"
        default:  reason = "Status"
        }
        var head = "HTTP/1.1 \(status) \(reason)\r\n"
        var allHeaders = headers
        allHeaders["Content-Length"] = String(body.count)
        allHeaders["Connection"] = "close"
        for (k, v) in allHeaders { head += "\(k): \(v)\r\n" }
        head += "\r\n"
        var payload = Data(head.utf8)
        payload.append(body)
        connection.send(content: payload, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }
}


// ─── Daemon entry point ─────────────────────────────────────────────


func runDaemon(deviceName: String, port: UInt16) -> Never {
    let app = NSApplication.shared
    let delegate = DaemonAppDelegate(deviceName: deviceName, port: port)
    app.delegate = delegate
    // Set the activation policy + activate BEFORE run() so that
    // when the binary is launched directly (not via ``open``),
    // AppKit still fires ``applicationDidFinishLaunching``. Without
    // these two calls the run loop spins forever without ever
    // triggering the delegate — which is exactly what happens when
    // a Swift CLI binary tries to bring up NSApplication.
    app.setActivationPolicy(.regular)
    app.activate(ignoringOtherApps: true)
    app.run()
    exit(0)
}


final class DaemonAppDelegate: NSObject, NSApplicationDelegate {
    let deviceName: String
    let port: UInt16
    var capture: ContinuousCapture?
    var server: DaemonServer?
    var window: NSWindow?
    var statusField: NSTextField?
    var refreshTimer: Timer?

    init(deviceName: String, port: UInt16) {
        self.deviceName = deviceName
        self.port = port
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // .regular so macOS treats us as a foreground app for TCC
        // purposes. The window is tiny but visible — operator can
        // see "daemon is alive" at a glance.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        makeStatusWindow()

        // CRITICAL: ``ensureCameraAuthorized()`` calls
        // ``AVCaptureDevice.requestAccess(for:)`` and waits on a
        // semaphore. The TCC dialog needs the main thread to render
        // — if we block here, the app deadlocks and macOS never
        // shows the prompt (and the HTTP listener never starts).
        //
        // So: kick the whole start-up onto a background queue.
        // Anything that touches AppKit later hops back to .main.
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.bootCaptureAndServer()
        }

        refreshTimer = Timer.scheduledTimer(
            withTimeInterval: 0.5, repeats: true
        ) { [weak self] _ in
            DispatchQueue.main.async { self?.refreshStatus() }
        }
    }

    private func bootCaptureAndServer() {
        let capture = ContinuousCapture(deviceName: deviceName)
        do {
            try capture.start()
        } catch {
            let msg = error.localizedDescription
            DispatchQueue.main.async { [weak self] in
                self?.capture = capture
                self?.showAlert(
                    title: "Camera failed to start",
                    message: msg,
                    style: .critical
                )
                NSApp.terminate(nil)
            }
            return
        }

        // Capture is up; assign so the status timer can read it.
        DispatchQueue.main.async { [weak self] in self?.capture = capture }

        // HTTP listener can run on a background queue too — it
        // doesn't need main-thread access. Failures still hop to
        // main for the alert.
        do {
            let server = try DaemonServer(
                port: port,
                cache: capture.cache,
                deviceName: deviceName
            )
            try server.start()
            DispatchQueue.main.async { [weak self] in self?.server = server }
        } catch {
            let msg = error.localizedDescription
            DispatchQueue.main.async { [weak self] in
                self?.showAlert(
                    title: "HTTP listener failed",
                    message: msg,
                    style: .critical
                )
                NSApp.terminate(nil)
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        refreshTimer?.invalidate()
        server?.stop()
        capture?.stop()
    }

    private func makeStatusWindow() {
        let frame = NSRect(x: 0, y: 0, width: 460, height: 180)
        let window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Fruit Market — camera daemon"

        let content = NSView(frame: frame)
        let label = NSTextField(labelWithString: "starting…")
        label.frame = NSRect(x: 20, y: 20, width: 420, height: 140)
        label.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        label.usesSingleLineMode = false
        label.cell?.wraps = true
        content.addSubview(label)
        window.contentView = content
        window.center()
        window.makeKeyAndOrderFront(nil)
        self.window = window
        self.statusField = label
    }

    private func refreshStatus() {
        guard let capture, let statusField else { return }
        let cache = capture.cache
        let age = cache.ageSeconds().map { String(format: "%.2f s", $0) } ?? "—"
        let bytes = cache.latest()?.count ?? 0
        let kb = Double(bytes) / 1024.0
        let lines = [
            "device:   \(capture.deviceName)",
            "endpoint: http://127.0.0.1:\(port)/frame.jpg",
            "captures: \(cache.captures())",
            "latest:   \(age) ago · \(String(format: "%.1f KB", kb))",
            "",
            "Set FM_CAMERA_BACKEND=daemon on the Python side to use this feed.",
        ]
        statusField.stringValue = lines.joined(separator: "\n")
    }

    private func showAlert(title: String, message: String, style: NSAlert.Style) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = style
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}
