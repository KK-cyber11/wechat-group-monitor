import CoreGraphics
import Foundation

let args = CommandLine.arguments
guard args.count >= 2 else {
    fputs("usage: wx_input click x y | scroll x y lines\n", stderr)
    exit(2)
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
case "scroll":
    let pos = CGPoint(x: Double(args[2])!, y: Double(args[3])!)
    let lines = Int32(args[4])!
    if let move = CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: pos, mouseButton: .left) {
        move.post(tap: .cghidEventTap)
    }
    if let ev = CGEvent(scrollWheelEvent2Source: src, units: .line, wheelCount: 1, wheel1: lines, wheel2: 0, wheel3: 0) {
        ev.location = pos
        ev.post(tap: .cghidEventTap)
    }
    usleep(200000)
    restore()
default:
    fputs("unknown command\n", stderr)
    exit(2)
}
