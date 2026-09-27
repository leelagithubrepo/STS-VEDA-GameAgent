// Read-only OCR of an explicitly supplied saved PNG; no capture or controller.
// Build explicitly (never from the reader):
//   mkdir -p artifacts/native-ocr/module-cache
//   xcrun swiftc -module-cache-path artifacts/native-ocr/module-cache \
//     scripts/native_ocr.swift -o artifacts/native-ocr/native_ocr
// Run: artifacts/native-ocr/native_ocr /absolute/path/to/saved.png
// Batch: native_ocr saved.png --regions '[{"id":"header-0","box":[10,20,110,70]}]'
import Foundation
import Vision
import ImageIO
import CryptoKit

func emit(_ payload: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}

func fail(_ code: String) -> Never {
    emit(["schema": "veda.native-text.raw.v1", "ok": false, "error": code, "observations": []])
    exit(1)
}

let arguments = CommandLine.arguments
let batchMode = arguments.count == 4 && arguments[2] == "--regions"
guard arguments.count == 2 || batchMode else { fail("invalid_arguments") }
let began = ProcessInfo.processInfo.systemUptime
let url = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
guard let bytes = try? Data(contentsOf: url), bytes.count <= 64 * 1024 * 1024,
      let source = CGImageSourceCreateWithData(bytes as CFData, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("image_unreadable") }
let width = Double(image.width), height = Double(image.height)
guard width > 0, height > 0, width * height <= 100_000_000 else { fail("image_dimensions_invalid") }
let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
func recognize(_ input: CGImage, in original: CGRect) throws -> ([Dictionary<String, Any>], Double, Int) {
    let request = VNRecognizeTextRequest()
    // Consistent with the saved-image experiment; no external inference service.
    request.usesCPUOnly = true
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.003
    let handler = VNImageRequestHandler(cgImage: input, orientation: .up, options: [:])
    let ocrBegan = ProcessInfo.processInfo.systemUptime
    func pixelPoint(_ point: CGPoint) -> [Double] {
        [original.minX + Double(point.x) * original.width,
         original.minY + (1 - Double(point.y)) * original.height]
    }
    try handler.perform([request])
    let ocrMilliseconds = (ProcessInfo.processInfo.systemUptime - ocrBegan) * 1000
    let observations: [[String: Any]] = (request.results ?? []).compactMap { observation in
        let candidates = observation.topCandidates(3).map { candidate -> [String: Any] in
            ["text": candidate.string, "confidence": candidate.confidence]
        }
        guard !candidates.isEmpty else { return nil }
        let box = observation.boundingBox
        return ["candidates": candidates,
                "box_original_pixels_top_left": [original.minX + Double(box.minX) * original.width,
                    original.minY + (1 - Double(box.maxY)) * original.height,
                    Double(box.width) * original.width, Double(box.height) * original.height],
                // Clockwise, beginning at the text observation's top-left corner.
                "quadrilateral_original_pixels_top_left": [pixelPoint(observation.topLeft),
                    pixelPoint(observation.topRight), pixelPoint(observation.bottomRight),
                    pixelPoint(observation.bottomLeft)]]
    }
    return (observations, ocrMilliseconds, request.revision)
}

func observationsFitCrop(_ observations: [[String: Any]], in crop: CGRect) -> Bool {
    // Vision can estimate text corners outside a cropped image. Such a region
    // is unreadable; never clamp its boxes or expose partially supported text.
    // Match the adapter's 0.01-original-pixel numerical tolerance exactly.
    let left = crop.minX - 0.01, top = crop.minY - 0.01
    let right = crop.maxX + 0.01, bottom = crop.maxY + 0.01
    for observation in observations {
        guard let box = observation["box_original_pixels_top_left"] as? [Double], box.count == 4,
              box.allSatisfy({ $0.isFinite }), box[2] > 0, box[3] > 0,
              box[0] >= left, box[1] >= top, box[0] + box[2] <= right, box[1] + box[3] <= bottom,
              let corners = observation["quadrilateral_original_pixels_top_left"] as? [[Double]],
              corners.count == 4, corners.allSatisfy({ point in
                  point.count == 2 && point.allSatisfy({ $0.isFinite }) &&
                  point[0] >= left && point[0] <= right && point[1] >= top && point[1] <= bottom
              }) else { return false }
    }
    return true
}

func preprocess(_ input: CGImage, mode: String) -> CGImage? {
    if mode == "original" { return input }
    guard ["green_text", "white_text"].contains(mode), let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
          let context = CGContext(data: nil, width: input.width, height: input.height,
              bitsPerComponent: 8, bytesPerRow: input.width * 4, space: colorSpace,
              bitmapInfo: CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue),
          let data = context.data else { return nil }
    // Threshold the original-size cropped pixels, before any 3x interpolation.
    context.draw(input, in: CGRect(x: 0, y: 0, width: input.width, height: input.height))
    let pixels = data.bindMemory(to: UInt8.self, capacity: context.bytesPerRow * input.height)
    for y in 0..<input.height {
        for x in 0..<input.width {
            let offset = y * context.bytesPerRow + x * 4
            let r = Double(pixels[offset]), g = Double(pixels[offset + 1]), b = Double(pixels[offset + 2])
            let keep = mode == "green_text"
                ? (g >= 160 && g > 1.12 * r && g > 1.4 * b)
                : (min(r, min(g, b)) >= 170 && max(r, max(g, b)) - min(r, min(g, b)) <= 45)
            let value: UInt8 = keep ? 0 : 255
            pixels[offset] = value
            pixels[offset + 1] = value
            pixels[offset + 2] = value
            pixels[offset + 3] = 255
        }
    }
    return context.makeImage()
}

if batchMode {
    guard let raw = arguments[3].data(using: .utf8), raw.count <= 16384,
          let decoded = try? JSONSerialization.jsonObject(with: raw),
          let regions = decoded as? [[String: Any]], regions.count <= 20 else { fail("invalid_regions") }
    var parsed: [(String, CGRect, String)] = []
    var ids = Set<String>()
    var totalScaledPixels = 0.0
    for region in regions {
        guard Set(region.keys).isSubset(of: ["box", "id", "preprocessing"]), let id = region["id"] as? String,
              !id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, id.count <= 128,
              ids.insert(id).inserted, let numbers = region["box"] as? [NSNumber], numbers.count == 4,
              numbers.allSatisfy({ CFGetTypeID($0) != CFBooleanGetTypeID() }) else { fail("invalid_regions") }
        let preprocessing: String
        if let supplied = region["preprocessing"] {
            guard let mode = supplied as? String, ["original", "green_text", "white_text"].contains(mode) else {
                fail("invalid_region_preprocessing")
            }
            preprocessing = mode
        } else {
            preprocessing = "original"
        }
        let box = numbers.map { $0.doubleValue }
        guard box.allSatisfy({ $0.isFinite && $0.rounded() == $0 }),
              box[0] >= 0, box[1] >= 0, box[2] > box[0], box[3] > box[1],
              box[2] <= width, box[3] <= height else { fail("invalid_region_box") }
        let rect = CGRect(x: box[0], y: box[1], width: box[2] - box[0], height: box[3] - box[1])
        let scaledPixels = rect.width * rect.height * 9
        totalScaledPixels += scaledPixels
        guard scaledPixels <= 12_000_000, totalScaledPixels <= 48_000_000,
              rect.width * 3 <= 8192, rect.height * 3 <= 8192 else { fail("region_pixel_limit") }
        parsed.append((id, rect, preprocessing))
    }
    var results: [[String: Any]] = []
    var ocrTotal = 0.0
    for (id, rect, preprocessing) in parsed {
        let regionBegan = ProcessInfo.processInfo.systemUptime
        var result: [String: Any] = ["id": id, "box_original_pixels_ltrb": [rect.minX, rect.minY, rect.maxX, rect.maxY],
            "scale": 3, "preprocessing": preprocessing, "ok": false, "error": NSNull(), "observations": [],
            "image_sha256": digest, "source_dimensions": [image.width, image.height]]
        var ocrMilliseconds = 0.0
        autoreleasepool {
            guard let cropped = image.cropping(to: rect), let prepared = preprocess(cropped, mode: preprocessing),
                  let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(data: nil, width: Int(rect.width * 3), height: Int(rect.height * 3),
                      bitsPerComponent: 8, bytesPerRow: 0, space: colorSpace,
                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
                result["error"] = "region_image_failed"
                return
            }
            context.interpolationQuality = .high
            context.draw(prepared, in: CGRect(x: 0, y: 0, width: rect.width * 3, height: rect.height * 3))
            guard let scaled = context.makeImage() else {
                result["error"] = "region_image_failed"
                return
            }
            do {
                let (observations, milliseconds, revision) = try recognize(scaled, in: rect)
                ocrMilliseconds = milliseconds
                guard observationsFitCrop(observations, in: rect) else {
                    result["error"] = "native_ocr_outside_region"
                    result["rejected_observation_count"] = observations.count
                    return
                }
                result["observations"] = observations
                result["recognition_revision"] = revision
                result["ok"] = true
            } catch {
                result["error"] = "native_ocr_failed"
            }
        }
        ocrTotal += ocrMilliseconds
        result["timing_ms"] = ["ocr": ocrMilliseconds,
            "region_total": (ProcessInfo.processInfo.systemUptime - regionBegan) * 1000]
        results.append(result)
    }
    emit(["schema": "veda.native-text-regions.raw.v1", "ok": true,
          "image_path": url.path, "image_sha256": digest, "source_dimensions": [image.width, image.height],
          "orientation": "up", "scale": 3, "recognition_level": "accurate", "cpu_only": true,
          "language_correction": false, "custom_words": [], "regions": results,
          "timing_ms": ["ocr": ocrTotal, "load_and_ocr": (ProcessInfo.processInfo.systemUptime - began) * 1000]])
} else {
    do {
        let (observations, ocrMilliseconds, revision) = try recognize(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        emit(["schema": "veda.native-text.raw.v1", "ok": true,
          "image_path": url.path, "image_sha256": digest, "source_dimensions": [image.width, image.height],
          "orientation": "up", "recognition_level": "accurate", "recognition_revision": revision,
          "language_correction": false, "custom_words": [], "cpu_only": true,
          "timing_ms": ["ocr": ocrMilliseconds,
                        "load_and_ocr": (ProcessInfo.processInfo.systemUptime - began) * 1000], "observations": observations])
    } catch {
        fail("native_ocr_failed")
    }
}
