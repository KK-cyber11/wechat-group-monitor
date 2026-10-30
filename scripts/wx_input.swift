import CoreGraphics
import Foundation
import AppKit
import ApplicationServices

let args = CommandLine.arguments
guard args.count >= 2 else {
    fputs("usage: wx_input click x y | scroll x y lines\n", stderr)
    exit(2)
}
// Read-only, bounded AX probe. Never mistake message OCR for a clickable control.
if args[1] == "latest-buttons" {
    guard args.count == 6, let x = Double(args[2]), let y = Double(args[3]),
          let width = Double(args[4]), let height = Double(args[5]) else { exit(2) }
    let target = CGRect(x: x, y: y, width: width, height: height)
    func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value
    }
    func rect(_ element: AXUIElement) -> CGRect? {
        guard let p = attr(element, kAXPositionAttribute), let s = attr(element, kAXSizeAttribute),
              CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
        var point = CGPoint.zero
        var size = CGSize.zero
        guard AXValueGetValue(p as! AXValue, .cgPoint, &point),
              AXValueGetValue(s as! AXValue, .cgSize, &size) else { return nil }
        return CGRect(origin: point, size: size)
    }
    var buttons: [[String: Any]] = []
    var visited = 0
    func walk(_ element: AXUIElement, _ depth: Int) {
        guard depth <= 14, visited < 1200 else { return }
        visited += 1
        if attr(element, kAXRoleAttribute) as? String == kAXButtonRole,
           attr(element, kAXEnabledAttribute) as? Bool != false,
           let box = rect(element), target.contains(box), box.width > 0, box.height > 0 {
            let labels = [kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute]
                .compactMap { attr(element, $0) as? String }.filter { !$0.isEmpty }
            buttons.append(["role": "AXButton", "labels": labels,
                            "x": box.minX, "y": box.minY, "w": box.width, "h": box.height])
        }
        for child in attr(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] { walk(child, depth + 1) }
    }
    for app in NSWorkspace.shared.runningApplications where app.bundleIdentifier == "com.tencent.xinWeChat" {
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 0.3)
        for window in attr(root, kAXWindowsAttribute) as? [AXUIElement] ?? [] {
            guard let box = rect(window), abs(box.minX - x) <= 2, abs(box.minY - y) <= 2,
                  abs(box.width - width) <= 2, abs(box.height - height) <= 2 else { continue }
            walk(window, 0)
        }
    }
    let data = try JSONSerialization.data(withJSONObject: ["buttons": buttons])
    FileHandle.standardOutput.write(data)
    exit(0)
}
let src = CGEventSource(stateID: .hidSystemState)
let saved = CGEvent(source: nil)?.location
func restore() {
    guard let saved, let move = CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: saved, mouseButton: .left) else { return }
    move.post(tap: .cghidEventTap)
}
switch args[1] {
case "click":
    let pos = CGPoint(x: Double(args[2])!, y: Double(args[3])!)
    if let down = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: pos, mouseButton: .left),
       let up = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: pos, mouseButton: .left) {
        down.post(tap: .cghidEventTap)
        usleep(50000)
        up.post(tap: .cghidEventTap)
    }
    restore()
case "scroll", "scroll-pixels":
    let pos = CGPoint(x: Double(args[2])!, y: Double(args[3])!)
    let lines = Int32(args[4])!
    if let move = CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: pos, mouseButton: .left) {
        move.post(tap: .cghidEventTap)
    }
    if let ev = CGEvent(scrollWheelEvent2Source: src, units: args[1] == "scroll-pixels" ? .pixel : .line, wheelCount: 1, wheel1: lines, wheel2: 0, wheel3: 0) {
        ev.location = pos
        ev.post(tap: .cghidEventTap)
    }
    // The collector observes settling; do not add a second fixed pixel-mode wait.
    if args[1] == "scroll" { usleep(200000) }
    restore()
default:
    fputs("unknown command\n", stderr)
    exit(2)
}
