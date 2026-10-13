import XCTest
@testable import LoveShe

final class LoveSheTests: XCTestCase {
    private func temporary() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    private var fixture: Data {
        Data("""
        [{"timestamp":"2026-01-05T14:00:00+08:00","sender":"me","content":"周末一起看展吗？"},
         {"timestamp":"2026-01-05T14:02:00+08:00","sender":"them","content":"周六下午可以。"}]
        """.utf8)
    }

    func testImportFormatsAndMilliseconds() throws {
        let unix = try ChatImport.date(1_767_593_520_000 as NSNumber)
        let iso = try ChatImport.date("2026-01-05T06:12:00Z")
        XCTAssertEqual(unix.timeIntervalSince1970, iso.timeIntervalSince1970, accuracy: 0.001)
        let wrapped = Data("{\"data\":{\"messages\":[{\"createTime\":1767593520,\"isSend\":1,\"parsedContent\":\"你好\"}]}}".utf8)
        let messages = try ChatImport.parse(wrapped, contactID: "a")
        XCTAssertEqual(messages.first?.sender, "me")
        XCTAssertEqual(messages.first?.content, "你好")
        XCTAssertThrowsError(try ChatImport.parse(Data("[{\"content\":\"缺时间\"}]".utf8), contactID: "a"))
    }

    func testDeduplicationAndContactIsolation() async throws {
        let db = try Database(directory: temporary())
        try await db.save(Contact(id: "a", name: "A"))
        try await db.save(Contact(id: "b", name: "B"))
        let first = try await db.importMessages(fixture, contactID: "a")
        let second = try await db.importMessages(fixture, contactID: "a")
        let other = try await db.importMessages(fixture, contactID: "b")
        XCTAssertEqual(first.inserted, 2)
        XCTAssertEqual(second.duplicates, 2)
        XCTAssertEqual(other.inserted, 2)
        let a = try await db.messages("a")
        let b = try await db.messages("b")
        XCTAssertTrue(Set(a.map(\.id)).isDisjoint(with: Set(b.map(\.id))))
        do {
            try await db.save(Memory(contactID: "a", content: "不属于 A 的证据", evidenceIDs: [b[0].id]))
            XCTFail("Foreign evidence must be rejected")
        } catch { XCTAssertTrue(error.localizedDescription.contains("证据")) }
        let memory = Memory(id: "saved", contactID: "a", content: "周六下午有空", evidenceIDs: [a[0].id])
        try await db.save(memory)
        do {
            try await db.save(Memory(id: "saved", contactID: "b", content: "移动记忆"))
            XCTFail("Memory ownership must remain stable")
        } catch { XCTAssertTrue(error.localizedDescription.contains("不属于")) }
    }

    func testInvalidFileDoesNotPartiallyImport() async throws {
        let db = try Database(directory: temporary())
        try await db.save(Contact(id: "a", name: "A"))
        let invalid = Data("[{\"timestamp\":1767593520,\"sender\":\"me\",\"content\":\"有效\"},{\"content\":\"无效\"}]".utf8)
        do { _ = try await db.importMessages(invalid, contactID: "a"); XCTFail("Expected validation error") }
        catch { XCTAssertTrue(error.localizedDescription.contains("第 2 条")) }
        let messages = try await db.messages("a")
        XCTAssertTrue(messages.isEmpty)
    }

    func testPromptBudgetsAndUntrustedCandidateEvidence() throws {
        let contact = Contact(id: "a", name: "A")
        let history = (0..<200).map { index in
            ChatMessage(id: "m\(index)", contactID: "a", timestamp: Date(timeIntervalSince1970: Double(index)), sender: "them",
                        content: String(repeating: "字", count: 2000), kind: "text")
        }
        let settings = ModelSettings(baseURL: "https://example.invalid/v1", model: "fictional")
        let preview = try PromptBuilder.prepare(contact: contact, history: history, memories: [], mode: .advise,
                                                question: "", revision: 4, settings: settings)
        XCTAssertEqual(preview.messageIDs.count, 8)
        XCTAssertEqual(preview.messageIDs.last, "m199")
        XCTAssertFalse(preview.previewText.contains("m0\""))
        XCTAssertThrowsError(try PromptBuilder.candidates(answer: "{\"candidates\":[{\"content\":\"坏证据\",\"evidence_ids\":[\"foreign\"]}]}", permittedIDs: preview.messageIDs))
        let candidates = try PromptBuilder.candidates(answer: "```json\n{\"candidates\":[{\"content\":\"待审核\",\"evidence_ids\":[\"m199\"]}]}\n```", permittedIDs: preview.messageIDs)
        XCTAssertEqual(candidates.count, 1)
    }

    func testCorrectedMemoryChangesRevisionAndDeletedMemoryIsNotRecalled() async throws {
        let db = try Database(directory: temporary())
        try await db.save(Contact(id: "a", name: "A"))
        let memory = Memory(id: "memory", contactID: "a", content: "旧背景")
        try await db.save(memory)
        let settings = ModelSettings(baseURL: "https://example.invalid/v1", model: "fictional")
        let before = try await db.prepare(contactID: "a", mode: .consult, question: "怎么看？", settings: settings)
        var edited = memory; edited.content = "纠正后的背景"
        try await db.save(edited)
        let after = try await db.prepare(contactID: "a", mode: .consult, question: "怎么看？", settings: settings)
        XCTAssertNotEqual(before.revision, after.revision)
        XCTAssertFalse(after.previewText.contains("旧背景"))
        try await db.deleteMemory(memory.id, contactID: "a")
        let deleted = try await db.prepare(contactID: "a", mode: .consult, question: "怎么看？", settings: settings)
        XCTAssertTrue(deleted.memoryIDs.isEmpty)
        XCTAssertFalse(deleted.previewText.contains("纠正后的背景"))
    }

    func testBackupRestoreAndCascadeDeletion() async throws {
        let directory = try temporary()
        let db = try Database(directory: directory.appendingPathComponent("database"))
        try await db.save(Contact(id: "a", name: "A"))
        _ = try await db.importMessages(fixture, contactID: "a")
        try await db.save(Memory(contactID: "a", content: "初始背景"))
        try await db.save(Consultation(contactID: "a", mode: .consult, question: "问题", answer: "演示回答",
                                       messageIDs: [], memoryIDs: [], model: "fictional"))
        let file = directory.appendingPathComponent("backup.sqlite3")
        try await db.exportBackup(to: file)
        try await db.deleteContact("a")
        let removedContacts = try await db.contacts()
        let removedMessages = try await db.messages("a")
        let removedMemories = try await db.memories("a")
        let removedConsultations = try await db.consultations("a")
        XCTAssertTrue(removedContacts.isEmpty)
        XCTAssertTrue(removedMessages.isEmpty)
        XCTAssertTrue(removedMemories.isEmpty)
        XCTAssertTrue(removedConsultations.isEmpty)
        try await db.restoreBackup(from: file)
        let restored = try await db.contacts()
        XCTAssertEqual(restored.first?.messageCount, 2)
        XCTAssertEqual(restored.first?.memoryCount, 1)
        let consultations = try await db.consultations("a")
        XCTAssertEqual(consultations.first?.answer, "演示回答")
    }

    func testRejectedBackupDoesNotDestroyCurrentData() async throws {
        let directory = try temporary()
        let db = try Database(directory: directory.appendingPathComponent("db"))
        try await db.save(Contact(id: "a", name: "A"))
        let invalid = directory.appendingPathComponent("invalid.sqlite3")
        try Data("not-a-database".utf8).write(to: invalid)
        do { try await db.restoreBackup(from: invalid); XCTFail("Invalid backup must be rejected") } catch { }
        let contacts = try await db.contacts()
        XCTAssertEqual(contacts.map(\.id), ["a"])
    }

    func testEndpointValidationAndCredentialFreePreview() throws {
        let value = ModelSettings(baseURL: "https://example.invalid/v1/", model: "fictional")
        XCTAssertEqual(try value.endpoint().absoluteString, "https://example.invalid/v1/chat/completions")
        XCTAssertEqual(try ModelSettings(baseURL: "https://example.invalid/v1/chat/completions", model: "fictional").endpoint(), try value.endpoint())
        for base in ["http://example.invalid/v1", "https://user:secret@example.invalid/v1", "https://example.invalid/v1?key=secret"] { // pragma: allowlist secret -- fictional invalid URLs
            XCTAssertThrowsError(try ModelSettings(baseURL: base, model: "fictional").endpoint())
        }
        let prepared = try PromptBuilder.prepare(contact: Contact(id: "a", name: "A"), history: [], memories: [],
                                                  mode: .consult, question: "问题", revision: 0, settings: value)
        XCTAssertFalse(prepared.previewText.contains("Authorization"))
        XCTAssertFalse(prepared.previewText.contains("api_key"))
    }
}
