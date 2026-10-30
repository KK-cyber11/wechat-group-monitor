import AppKit
import CoreGraphics
import Foundation
import Vision

let outRoot = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : FileManager.default.temporaryDirectory.path
try FileManager.default.createDirectory(atPath: outRoot, withIntermediateDirectories: true)

func ownerOk(_ name: String) -> Bool {
    ["WeChat", "微信", "Weixin"].contains(name)
}

let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
let windows = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] ?? []
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
    guard let img = NSImage(contentsOfFile: png),
          let tiff = img.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let cg = rep.cgImage else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    var lines: [[String: Any]] = []
    for obs in req.results ?? [] {
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
        "lines": lines
    ])
}

let data = try JSONSerialization.data(withJSONObject: ["windows": captured], options: [])
FileHandle.standardOutput.write(data)
