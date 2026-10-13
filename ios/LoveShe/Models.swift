import Foundation

struct Contact: Identifiable, Codable, Hashable {
    var id: String = UUID().uuidString
    var name: String
    var goal: String = ""
    var background: String = ""
    var messageCount: Int = 0
    var memoryCount: Int = 0
}

struct ChatMessage: Identifiable, Codable, Hashable {
    var id: String
    var contactID: String
    var timestamp: Date
    var sender: String
    var content: String
    var kind: String
    var senderLabel: String { sender == "me" ? "我" : sender == "them" ? "对方" : "方向待确认" }
}

enum MemoryStatus: String, Codable, CaseIterable, Hashable {
    case fact, inference, unconfirmed
    var label: String {
        switch self {
        case .fact: return "已确认事实"
        case .inference: return "推测"
        case .unconfirmed: return "待确认"
        }
    }
}

struct Memory: Identifiable, Codable, Hashable {
    var id: String = UUID().uuidString
    var contactID: String
    var content: String
    var status: MemoryStatus = .unconfirmed
    var evidenceIDs: [String] = []
    var updatedAt: Date = Date()
}

enum ConsultationMode: String, Codable, CaseIterable, Hashable {
    case consult, advise, extract
    var label: String {
        switch self {
        case .consult: return "军师咨询"
        case .advise: return "回复建议"
        case .extract: return "整理记忆"
        }
    }
}

struct Consultation: Identifiable, Codable, Hashable {
    var id: String = UUID().uuidString
    var contactID: String
    var mode: ConsultationMode
    var question: String
    var answer: String
    var createdAt: Date = Date()
    var messageIDs: [String]
    var memoryIDs: [String]
    var model: String
}

struct MemoryCandidate: Identifiable, Decodable {
    var id: String { content + evidenceIDs.joined() }
    let content: String
    let evidenceIDs: [String]
    enum CodingKeys: String, CodingKey { case content; case evidenceIDs = "evidence_ids" }
}

struct ImportReport {
    var inserted = 0
    var duplicates = 0
    var unknownSenders = 0
    var description: String { "新增 \(inserted) 条，重复 \(duplicates) 条，发送方向待确认 \(unknownSenders) 条。" }
}

struct ContactStorage: Identifiable {
    let id: String
    let name: String
    let bytes: Int64
}

struct StorageReport {
    var databaseBytes: Int64 = 0
    var cacheBytes: Int64 = 0
    var contacts: [ContactStorage] = []
    static func format(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}

struct AppError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct ModelSettings: Codable, Equatable {
    var baseURL = ""
    var model = ""
    var recentLimit = 100
    var maxOutputTokens = 1500

    func endpoint() throws -> URL {
        guard var parts = URLComponents(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme == "https", let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil else {
            throw AppError("请输入不含账号、查询参数或片段的 HTTPS API 地址。")
        }
        var path = parts.path
        while path.hasSuffix("/") { path.removeLast() }
        if !path.hasSuffix("/chat/completions") { path += "/chat/completions" }
        parts.path = path
        guard let url = parts.url else { throw AppError("API 地址无效。") }
        guard !model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AppError("请填写模型名称。")
        }
        guard (20...200).contains(recentLimit), (256...4096).contains(maxOutputTokens) else {
            throw AppError("消息范围或输出上限无效。")
        }
        return url
    }
}

struct ModelMessage: Codable { let role: String; let content: String }

struct PreparedRequest: Identifiable {
    let id = UUID()
    let contact: Contact
    let mode: ConsultationMode
    let question: String
    let messages: [ModelMessage]
    let messageIDs: [String]
    let memoryIDs: [String]
    let revision: Int64
    let settings: ModelSettings
    let createdAt = Date()
    var previewText: String { messages.map { "[\($0.role)]\n\($0.content)" }.joined(separator: "\n\n") }
}
