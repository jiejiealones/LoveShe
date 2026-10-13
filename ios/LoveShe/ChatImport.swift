import Foundation
import CryptoKit

enum ChatImport {
    static let maxBytes = 16 * 1024 * 1024
    static let maxRecords = 50_000

    static func parse(_ data: Data, contactID: String) throws -> [ChatMessage] {
        guard data.count <= maxBytes else { throw AppError("单次导入上限为 16 MB，请拆分文件。") }
        var clean = data
        if clean.starts(with: [0xEF, 0xBB, 0xBF]) { clean.removeFirst(3) }
        let payload = try JSONSerialization.jsonObject(with: clean, options: [.fragmentsAllowed])
        let rows = try messageArray(payload)
        guard rows.count <= maxRecords else { throw AppError("单次最多导入 5 万条，请拆分文件。") }
        // Validate every row before a database transaction: no partially imported files.
        return try rows.enumerated().map { offset, item in
            guard let raw = item as? [String: Any] else { throw AppError("第 \(offset + 1) 条不是消息对象。") }
            do { return try normalize(raw, contactID: contactID) }
            catch { throw AppError("第 \(offset + 1) 条：\(error.localizedDescription)") }
        }
    }

    private static func messageArray(_ payload: Any) throws -> [Any] {
        if let rows = payload as? [Any] { return rows }
        if let object = payload as? [String: Any] {
            for key in ["messages", "data", "records"] {
                if let rows = object[key] as? [Any] { return rows }
                if let nested = object[key] as? [String: Any], let rows = nested["messages"] as? [Any] { return rows }
            }
        }
        throw AppError("需要消息数组或包含 messages / data / records 的 JSON。")
    }

    static func normalize(_ raw: [String: Any], contactID: String) throws -> ChatMessage {
        let timestamp = try date(raw["createTime"] ?? raw["timestamp"] ?? raw["ts"])
        var sender = raw["sender"] as? String ?? "unknown"
        if !["me", "them"].contains(sender) {
            let sent = raw["isSend"]
            if let value = sent as? NSNumber { sender = value.intValue == 1 ? "me" : value.intValue == 0 ? "them" : "unknown" }
            else if let value = sent as? String { sender = value == "1" ? "me" : value == "0" ? "them" : "unknown" }
            else { sender = "unknown" }
        }
        let rawContent = raw["parsedContent"] ?? raw["content"] ?? raw["rawContent"] ?? ""
        let content: String
        if let text = rawContent as? String { content = text }
        else if JSONSerialization.isValidJSONObject(rawContent) {
            content = String(decoding: try JSONSerialization.data(withJSONObject: rawContent, options: [.sortedKeys, .fragmentsAllowed]), as: UTF8.self)
        } else { throw AppError("消息正文必须是文字或 JSON 对象。") }
        guard content.utf8.count <= 256 * 1024 else { throw AppError("单条消息超过 256 KB。") }
        let kind = String(describing: raw["type"] ?? raw["localType"] ?? "text")
        // The native ID deliberately ignores exporter IDs to deduplicate identical text exports.
        let identity: [String] = [contactID, String(Int64(timestamp.timeIntervalSince1970 * 1000)), sender, content, kind]
        let bytes = try JSONEncoder().encode(identity)
        let id = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        return ChatMessage(id: id, contactID: contactID, timestamp: timestamp, sender: sender, content: content, kind: kind)
    }

    static func date(_ value: Any?) throws -> Date {
        if let number = value as? NSNumber { return try epoch(number.doubleValue) }
        guard let text = value as? String else { throw AppError("缺少有效时间。") }
        if let number = Double(text), !text.contains("-") { return try epoch(number) }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let parsed = iso.date(from: text) { return parsed }
        iso.formatOptions = [.withInternetDateTime]
        if let parsed = iso.date(from: text) { return parsed }
        // Match the desktop app's UTC interpretation of timestamps without a zone.
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd"] {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(secondsFromGMT: 0)
            formatter.dateFormat = format
            formatter.isLenient = false
            if let parsed = formatter.date(from: text) { return parsed }
        }
        throw AppError("时间格式不受支持，请使用 ISO 8601 或 Unix 时间戳。")
    }

    private static func epoch(_ raw: Double) throws -> Date {
        guard raw.isFinite else { throw AppError("时间戳无效。") }
        var seconds = raw
        while abs(seconds) > 100_000_000_000 { seconds /= 1000 }
        guard seconds >= -62_135_596_800, seconds <= 253_402_300_799 else { throw AppError("时间戳超出范围。") }
        return Date(timeIntervalSince1970: seconds)
    }
}
