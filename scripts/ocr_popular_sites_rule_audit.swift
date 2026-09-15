#!/usr/bin/env swift
// Diagnostic OCR for screenshots captured during the provider-blind rule audit.
// OCR output can identify pixel-only text, but is never valid scoring evidence.

import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count == 3 else {
    FileHandle.standardError.write(Data("usage: ocr_popular_sites_rule_audit.swift SCREENSHOT_DIR OUTPUT_JSON\n".utf8))
    exit(2)
}

let input = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let output = URL(fileURLWithPath: CommandLine.arguments[2])
if FileManager.default.fileExists(atPath: output.path) {
    FileHandle.standardError.write(Data("refusing to overwrite diagnostic evidence\n".utf8))
    exit(2)
}

let files = try FileManager.default.contentsOfDirectory(
    at: input,
    includingPropertiesForKeys: nil
).filter { $0.pathExtension.lowercased() == "png" }.sorted { $0.lastPathComponent < $1.lastPathComponent }

var cases: [[String: Any]] = []
for file in files {
    guard let image = NSImage(contentsOf: file),
          let data = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: data),
          let cgImage = bitmap.cgImage else {
        cases.append(["screenshot": file.lastPathComponent, "state": "image_error", "lines": []])
        continue
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    try VNImageRequestHandler(cgImage: cgImage).perform([request])
    let lines = (request.results ?? []).compactMap { observation in
        observation.topCandidates(1).first?.string
    }
    cases.append([
        "case_id": file.deletingPathExtension().lastPathComponent,
        "screenshot": file.lastPathComponent,
        "state": "captured",
        "lines": lines,
    ])
}

let payload: [String: Any] = [
    "schema_version": 1,
    "purpose": "Diagnostic detection of pixel-only text; prohibited as scoring evidence",
    "ocr_is_scoring_evidence": false,
    "screenshot_directory": input.path,
    "cases": cases,
]
let encoded = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
try encoded.write(to: output, options: .withoutOverwriting)
print("{\"screenshots\":\(files.count),\"output\":\"\(output.path)\"}")
