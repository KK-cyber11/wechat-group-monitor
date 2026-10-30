import AppKit
import CoreGraphics
import Foundation
import Vision
import CryptoKit

let outRoot = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : FileManager.default.temporaryDirectory.path
let mainOnly = CommandLine.arguments.contains("--main-window")
let useCache = CommandLine.arguments.contains("--cache-unchanged")
let started = Date()
try FileManager.default.createDirectory(atPath: outRoot, withIntermediateDirectories: true)

func ownerOk(_ name: String) -> Bool {
    ["WeChat", "微信", "Weixin"].contains(name)
}

let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
var windows = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] ?? []
if mainOnly {
    windows = windows.filter { w in
        let b = w[kCGWindowBounds as String] as? [String: CGFloat] ?? [:]
        return ownerOk(w[kCGWindowOwnerName as String] as? String ?? "") &&
            (w[kCGWindowLayer as String] as? Int) == 0 &&
            (b["Width"] ?? 0) >= 500 && (b["Height"] ?? 0) >= 400
    }.sorted { a, b in
        let aa = a[kCGWindowBounds as String] as? [String: CGFloat] ?? [:]
        let bb = b[kCGWindowBounds as String] as? [String: CGFloat] ?? [:]
        return (aa["Width"] ?? 0) * (aa["Height"] ?? 0) > (bb["Width"] ?? 0) * (bb["Height"] ?? 0)
    }
    windows = Array(windows.prefix(1))
}
var captured: [[String: Any]] = []

for w in windows {
    let owner = w[kCGWindowOwnerName as String] as? String ?? ""
    guard ownerOk(owner) else { continue }
    guard (w[kCGWindowLayer as String] as? Int) == 0 else { continue }
    guard let wid = w[kCGWindowNumber as String] as? Int else { continue }
    let b = w[kCGWindowBounds as String] as? [String: CGFloat] ?? [:]
    let width = b["Width"] ?? 0
    let height = b["Height"] ?? 0
    guard width >= 500, height >= 400 else { continue }
    let png = outRoot + "/" + String(wid) + ".png"
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    p.arguments = ["-x", "-o", "-l", String(wid), png]
    try p.run()
    p.waitUntilExit()
    guard p.terminationStatus == 0 else { continue }
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: png) as CFURL, nil),
          let cg = CGImageSourceCreateImageAtIndex(source, 0, nil) else { continue }
    // Decode directly through ImageIO, avoiding NSImage -> TIFF conversion.
    let pixelData = cg.dataProvider?.data as Data? ?? Data()
    let fingerprint = SHA256.hash(data: pixelData).map { String(format: "%02x", $0) }.joined()
    let cachePath = outRoot + "/" + String(wid) + ".cache.json"
    let old = (try? Data(contentsOf: URL(fileURLWithPath: cachePath))).flatMap {
        try? JSONSerialization.jsonObject(with: $0) as? [String: Any]
    }
    let cachedLines = old?["lines"] as? [[String: Any]]
    let cacheHit = useCache && old?["fingerprint"] as? String == fingerprint && cachedLines != nil
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.usesLanguageCorrection = false
    if !cacheHit { try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req]) }
    var lines: [[String: Any]] = cacheHit ? cachedLines! : []
    for obs in cacheHit ? [] : (req.results ?? []) {
        guard let cand = obs.topCandidates(1).first else { continue }
        let box = obs.boundingBox
        lines.append([
            "text": cand.string,
            "confidence": cand.confidence,
            "x": box.origin.x,
            "y": box.origin.y,
            "w": box.size.width,
            "h": box.size.height
        ])
    }
    if useCache && !cacheHit {
        let cache = try JSONSerialization.data(withJSONObject: ["fingerprint": fingerprint, "lines": lines])
        try cache.write(to: URL(fileURLWithPath: cachePath), options: .atomic)
    }
    captured.append([
        "window_id": wid,
        "owner": owner,
        "name": w[kCGWindowName as String] as? String ?? "",
        "x": b["X"] ?? 0,
        "y": b["Y"] ?? 0,
        "width": width,
        "height": height,
        "image": png,
        "pixel_width": cg.width,
        "pixel_height": cg.height,
        "captured_at": ISO8601DateFormatter().string(from: Date()),
        "image_fingerprint": fingerprint,
        "ocr_cache_hit": cacheHit,
        "lines": lines
    ])
}

let data = try JSONSerialization.data(withJSONObject: ["windows": captured, "elapsed_ms": Date().timeIntervalSince(started) * 1000], options: [])
FileHandle.standardOutput.write(data)
