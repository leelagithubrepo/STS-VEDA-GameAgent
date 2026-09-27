import CoreGraphics
import Foundation

// Passive enumeration only. Never activate an app or change the selected device.
let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
var result: [[String: Any]] = []
for window in windows {
    guard let owner = window[kCGWindowOwnerName as String] as? String,
          owner == "QuickTime Player",
          let id = window[kCGWindowNumber as String] as? Int,
          let bounds = window[kCGWindowBounds as String] as? [String: Any],
          let width = bounds["Width"] as? Double,
          let height = bounds["Height"] as? Double,
          width.isFinite, height.isFinite, width >= 320, height >= 180,
          (window[kCGWindowLayer as String] as? Int) == 0 else { continue }
    result.append(["id": id, "owner": owner,
                   "title": window[kCGWindowName as String] as? String ?? "",
                   "width": width, "height": height,
                   "on_screen": window[kCGWindowIsOnscreen as String] as? Bool ?? false])
}
let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
FileHandle.standardOutput.write(data)
