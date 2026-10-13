import Foundation
import SQLite3

private let sqliteTransient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

actor Database {
    private var db: OpaquePointer?
    let directory: URL
    private let schema = """
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT OR IGNORE INTO app_metadata VALUES ('format','loveshe-ios-v1');
    INSERT OR IGNORE INTO app_metadata VALUES ('revision','0');
    CREATE TABLE IF NOT EXISTS contacts (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, goal TEXT NOT NULL, background TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
      timestamp REAL NOT NULL, sender TEXT NOT NULL, content TEXT NOT NULL, kind TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS messages_contact_time ON messages(contact_id,timestamp DESC,id DESC);
    CREATE TABLE IF NOT EXISTS memories (
      id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
      content TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('fact','inference','unconfirmed')),
      evidence_ids TEXT NOT NULL, updated_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS memories_contact ON memories(contact_id);
    CREATE TABLE IF NOT EXISTS consultations (
      id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
      mode TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, created_at REAL NOT NULL,
      message_ids TEXT NOT NULL, memory_ids TEXT NOT NULL, model TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS consultations_contact_time ON consultations(contact_id,created_at DESC);
    PRAGMA user_version=1;
    """

    init(directory: URL) throws {
        self.directory = directory
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try Self.protect(directory)
        let path = directory.appendingPathComponent("loveshe.sqlite3").path
        var handle: OpaquePointer?
        guard sqlite3_open_v2(path, &handle, SQLITE_OPEN_CREATE | SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            if let handle { sqlite3_close(handle) }
            throw AppError("无法打开本地数据库。")
        }
        db = handle
        sqlite3_busy_timeout(handle, 5000)
        guard sqlite3_exec(handle, "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;" + schema, nil, nil, nil) == SQLITE_OK else {
            sqlite3_close(handle); db = nil
            throw AppError("无法初始化本地数据库。")
        }
        try Self.protect(URL(fileURLWithPath: path))
    }

    deinit { sqlite3_close(db) }

    static func protect(_ url: URL) throws {
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: url.path)
        var target = url
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        try target.setResourceValues(values)
    }

    private func statement(_ sql: String, _ values: [Any] = []) throws -> OpaquePointer {
        var prepared: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &prepared, nil) == SQLITE_OK, let prepared else {
            throw AppError("本地数据库操作失败。")
        }
        for (offset, value) in values.enumerated() {
            let index = Int32(offset + 1)
            let code: Int32
            if let string = value as? String { code = sqlite3_bind_text(prepared, index, string, -1, sqliteTransient) }
            else if let number = value as? Int { code = sqlite3_bind_int64(prepared, index, Int64(number)) }
            else if let number = value as? Int64 { code = sqlite3_bind_int64(prepared, index, number) }
            else if let number = value as? Double { code = sqlite3_bind_double(prepared, index, number) }
            else { sqlite3_finalize(prepared); throw AppError("无效的数据库参数。") }
            if code != SQLITE_OK { sqlite3_finalize(prepared); throw AppError("无法绑定数据库参数。") }
        }
        return prepared
    }

    private func execute(_ sql: String, _ values: [Any] = []) throws {
        let prepared = try statement(sql, values)
        defer { sqlite3_finalize(prepared) }
        guard sqlite3_step(prepared) == SQLITE_DONE else { throw AppError("无法保存本地数据。") }
    }

    private func rows<T>(_ sql: String, _ values: [Any] = [], map: (OpaquePointer) throws -> T) throws -> [T] {
        let prepared = try statement(sql, values)
        defer { sqlite3_finalize(prepared) }
        var result: [T] = []
        while true {
            let code = sqlite3_step(prepared)
            if code == SQLITE_DONE { return result }
            guard code == SQLITE_ROW else { throw AppError("无法读取本地数据。") }
            result.append(try map(prepared))
        }
    }

    private func text(_ row: OpaquePointer, _ column: Int32) -> String {
        guard let bytes = sqlite3_column_text(row, column) else { return "" }
        return String(cString: bytes)
    }

    private func transaction<T>(_ action: () throws -> T) throws -> T {
        try execute("BEGIN IMMEDIATE")
        do {
            let value = try action()
            try execute("UPDATE app_metadata SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
            try execute("COMMIT")
            return value
        } catch { try? execute("ROLLBACK"); throw error }
    }

    func revision() throws -> Int64 {
        try rows("SELECT CAST(value AS INTEGER) FROM app_metadata WHERE key='revision'", map: { sqlite3_column_int64($0, 0) }).first ?? 0
    }

    func contacts() throws -> [Contact] {
        try rows("""
        SELECT c.id,c.name,c.goal,c.background,
        (SELECT COUNT(*) FROM messages WHERE contact_id=c.id),
        (SELECT COUNT(*) FROM memories WHERE contact_id=c.id)
        FROM contacts c ORDER BY c.name,c.id
        """, map: { row in
            Contact(id: text(row, 0), name: text(row, 1), goal: text(row, 2), background: text(row, 3),
                    messageCount: Int(sqlite3_column_int64(row, 4)), memoryCount: Int(sqlite3_column_int64(row, 5)))
        })
    }

    func save(_ contact: Contact) throws {
        guard !contact.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              contact.name.count <= 100, contact.goal.count <= 4000, contact.background.count <= 12000 else {
            throw AppError("联系人姓名不能为空，姓名最多 100 字，目标最多 4000 字，背景最多 12000 字。")
        }
        try transaction {
            try execute("""
            INSERT INTO contacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,goal=excluded.goal,background=excluded.background
            """, [contact.id, contact.name, contact.goal, contact.background])
        }
    }

    func importMessages(_ data: Data, contactID: String) throws -> ImportReport {
        let messages = try ChatImport.parse(data, contactID: contactID)
        return try transaction {
            var report = ImportReport()
            // One prepared insert reused for the entire file; no per-message UI allocations.
            let insert = try statement("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?)")
            defer { sqlite3_finalize(insert) }
            for message in messages {
                sqlite3_reset(insert); sqlite3_clear_bindings(insert)
                for (index, value) in [message.id, contactID].enumerated() {
                    sqlite3_bind_text(insert, Int32(index + 1), value, -1, sqliteTransient)
                }
                sqlite3_bind_double(insert, 3, message.timestamp.timeIntervalSince1970)
                for (index, value) in [message.sender, message.content, message.kind].enumerated() {
                    sqlite3_bind_text(insert, Int32(index + 4), value, -1, sqliteTransient)
                }
                guard sqlite3_step(insert) == SQLITE_DONE else { throw AppError("导入失败，未保存此次文件中的消息。") }
                if sqlite3_changes(db) == 1 {
                    report.inserted += 1
                    if message.sender == "unknown" { report.unknownSenders += 1 }
                } else { report.duplicates += 1 }
            }
            return report
        }
    }

    func messages(_ contactID: String, search: String = "", offset: Int = 0, limit: Int = 100) throws -> [ChatMessage] {
        try rows("""
        SELECT id,contact_id,timestamp,sender,content,kind FROM messages
        WHERE contact_id=? AND (?='' OR instr(content,?)>0 OR id LIKE ?)
        ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?
        """, [contactID, search, search, search + "%", min(max(limit, 1), 200), max(offset, 0)], map: { row in
            ChatMessage(id: text(row, 0), contactID: text(row, 1), timestamp: Date(timeIntervalSince1970: sqlite3_column_double(row, 2)),
                        sender: text(row, 3), content: text(row, 4), kind: text(row, 5))
        })
    }

    func memories(_ contactID: String, limit: Int = 200, offset: Int = 0) throws -> [Memory] {
        try rows("SELECT id,contact_id,content,status,evidence_ids,updated_at FROM memories WHERE contact_id=? ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                 [contactID, min(max(limit, 1), 200), max(offset, 0)], map: { row in
            guard let status = MemoryStatus(rawValue: text(row, 3)) else { throw AppError("记忆状态无效。") }
            return Memory(id: text(row, 0), contactID: text(row, 1), content: text(row, 2), status: status,
                          evidenceIDs: try JSONDecoder().decode([String].self, from: Data(text(row, 4).utf8)),
                          updatedAt: Date(timeIntervalSince1970: sqlite3_column_double(row, 5)))
        })
    }

    func save(_ memory: Memory) throws {
        guard !memory.content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, memory.content.count <= 12000 else {
            throw AppError("记忆不能为空，最多 12000 字。")
        }
        try transaction {
            // An ID can never be moved to a different contact by an edit.
            if let owner = try rows("SELECT contact_id FROM memories WHERE id=?", [memory.id], map: { text($0, 0) }).first,
               owner != memory.contactID { throw AppError("记忆不属于此联系人。") }
            for id in Set(memory.evidenceIDs) {
                guard try !rows("SELECT id FROM messages WHERE id=? AND contact_id=?", [id, memory.contactID], map: { text($0, 0) }).isEmpty else {
                    throw AppError("记忆证据不属于此联系人。")
                }
            }
            let evidence = String(decoding: try JSONEncoder().encode(memory.evidenceIDs), as: UTF8.self)
            try execute("""
            INSERT INTO memories VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            content=excluded.content,status=excluded.status,evidence_ids=excluded.evidence_ids,updated_at=excluded.updated_at
            """, [memory.id, memory.contactID, memory.content, memory.status.rawValue, evidence, Date().timeIntervalSince1970])
        }
    }

    func memory(_ id: String, contactID: String) throws -> Memory? {
        try rows("SELECT id,contact_id,content,status,evidence_ids,updated_at FROM memories WHERE id=? AND contact_id=?",
                 [id, contactID], map: { row in
            guard let status = MemoryStatus(rawValue: text(row, 3)) else { throw AppError("记忆状态无效。") }
            return Memory(id: text(row, 0), contactID: text(row, 1), content: text(row, 2), status: status,
                          evidenceIDs: try JSONDecoder().decode([String].self, from: Data(text(row, 4).utf8)),
                          updatedAt: Date(timeIntervalSince1970: sqlite3_column_double(row, 5)))
        }).first
    }

    func deleteMemory(_ id: String, contactID: String) throws {
        try transaction { try execute("DELETE FROM memories WHERE id=? AND contact_id=?", [id, contactID]) }
    }

    func consultations(_ contactID: String, offset: Int = 0) throws -> [Consultation] {
        try rows("SELECT id,contact_id,mode,question,answer,created_at,message_ids,memory_ids,model FROM consultations WHERE contact_id=? ORDER BY created_at DESC,id LIMIT 30 OFFSET ?",
                 [contactID, max(offset, 0)], map: { row in
            guard let mode = ConsultationMode(rawValue: text(row, 2)) else { throw AppError("咨询模式无效。") }
            return Consultation(id: text(row, 0), contactID: text(row, 1), mode: mode, question: text(row, 3), answer: text(row, 4),
                                createdAt: Date(timeIntervalSince1970: sqlite3_column_double(row, 5)),
                                messageIDs: try JSONDecoder().decode([String].self, from: Data(text(row, 6).utf8)),
                                memoryIDs: try JSONDecoder().decode([String].self, from: Data(text(row, 7).utf8)), model: text(row, 8))
        })
    }

    func save(_ consultation: Consultation) throws {
        try transaction {
            let encoder = JSONEncoder()
            try execute("INSERT INTO consultations VALUES (?,?,?,?,?,?,?,?,?)",
                        [consultation.id, consultation.contactID, consultation.mode.rawValue, consultation.question, consultation.answer,
                         consultation.createdAt.timeIntervalSince1970,
                         String(decoding: try encoder.encode(consultation.messageIDs), as: UTF8.self),
                         String(decoding: try encoder.encode(consultation.memoryIDs), as: UTF8.self), consultation.model])
        }
    }

    func deleteContact(_ id: String) throws {
        try transaction { try execute("DELETE FROM contacts WHERE id=?", [id]) }
        try compact()
    }

    func clearConsultations(_ contactID: String) throws {
        try transaction { try execute("DELETE FROM consultations WHERE contact_id=?", [contactID]) }
        try compact()
    }

    private func compact() throws {
        try execute("VACUUM")
        // Checkpoint writes WAL pages into the primary file and releases the WAL space.
        guard sqlite3_wal_checkpoint_v2(db, nil, SQLITE_CHECKPOINT_TRUNCATE, nil, nil) == SQLITE_OK else {
            throw AppError("数据已处理，但空间整理未完成，请稍后重试。")
        }
    }

    func storage(cache: URL) throws -> StorageReport {
        let usage = try rows("""
        SELECT c.id,c.name,
        length(CAST(c.name||c.goal||c.background AS BLOB)) +
        COALESCE((SELECT SUM(length(CAST(content AS BLOB))) FROM messages WHERE contact_id=c.id),0) +
        COALESCE((SELECT SUM(length(CAST(content AS BLOB))) FROM memories WHERE contact_id=c.id),0) +
        COALESCE((SELECT SUM(length(CAST(question||answer AS BLOB))) FROM consultations WHERE contact_id=c.id),0)
        FROM contacts c ORDER BY 3 DESC
        """, map: { ContactStorage(id: text($0, 0), name: text($0, 1), bytes: sqlite3_column_int64($0, 2)) })
        return StorageReport(databaseBytes: Self.fileBytes(directory), cacheBytes: Self.fileBytes(cache), contacts: usage)
    }

    static func fileBytes(_ directory: URL) -> Int64 {
        guard let files = FileManager.default.enumerator(at: directory, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey]) else { return 0 }
        var count: Int64 = 0
        for case let url as URL in files {
            if let values = try? url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey]), values.isRegularFile == true {
                count += Int64(values.fileSize ?? 0)
            }
        }
        return count
    }

    func exportBackup(to destination: URL) throws {
        var target: OpaquePointer?
        guard sqlite3_open_v2(destination.path, &target, SQLITE_OPEN_CREATE | SQLITE_OPEN_READWRITE, nil) == SQLITE_OK else {
            sqlite3_close(target); throw AppError("无法创建备份文件。")
        }
        defer { sqlite3_close(target) }
        try Self.copyDatabase(from: db, to: target)
        // Export a self-contained file: it must not require a companion WAL/SHM.
        guard sqlite3_exec(target, "PRAGMA journal_mode=DELETE", nil, nil, nil) == SQLITE_OK else {
            throw AppError("备份已复制，但无法整理成独立文件。")
        }
        try Self.protect(destination)
    }

    func restoreBackup(from source: URL) throws {
        let size = try source.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0, size <= 128 * 1024 * 1024 else { throw AppError("备份文件须小于 128 MB。") }
        var imported: OpaquePointer?
        guard sqlite3_open_v2(source.path, &imported, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(imported); throw AppError("无法读取备份。")
        }
        defer { sqlite3_close(imported) }
        func scalar(_ sql: String, stage: String) throws -> String {
            var prepared: OpaquePointer?
            guard sqlite3_prepare_v2(imported, sql, -1, &prepared, nil) == SQLITE_OK, let prepared else {
                throw AppError("备份\(stage)无法读取（SQLite \(sqlite3_errcode(imported))）。")
            }
            defer { sqlite3_finalize(prepared) }
            guard sqlite3_step(prepared) == SQLITE_ROW, let value = sqlite3_column_text(prepared, 0) else {
                throw AppError("备份\(stage)校验失败（SQLite \(sqlite3_errcode(imported))）。")
            }
            return String(cString: value)
        }
        guard try scalar("PRAGMA quick_check", stage: "完整性") == "ok",
              try scalar("PRAGMA user_version", stage: "版本") == "1",
              try scalar("SELECT value FROM app_metadata WHERE key='format'", stage: "格式标记") == "loveshe-ios-v1" else {
            throw AppError("只支持此 iOS 版本导出的完整备份，不支持 Windows 数据库直接恢复。")
        }
        // Reject views/triggers and validate the entire schema, not just the marker.
        var schemaRows: OpaquePointer?
        guard sqlite3_prepare_v2(imported, "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name", -1, &schemaRows, nil) == SQLITE_OK,
              let schemaRows else { throw AppError("备份结构无效。") }
        defer { sqlite3_finalize(schemaRows) }
        let current = try rows("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name", map: { [text($0, 0), text($0, 1), text($0, 2)] })
        var incoming: [[String]] = []
        while sqlite3_step(schemaRows) == SQLITE_ROW {
            incoming.append((0..<3).map { column in
                sqlite3_column_text(schemaRows, Int32(column)).map { String(cString: $0) } ?? ""
            })
        }
        guard incoming == current else { throw AppError("备份结构与当前版本不匹配。") }
        var check: OpaquePointer?
        guard sqlite3_prepare_v2(imported, "PRAGMA foreign_key_check", -1, &check, nil) == SQLITE_OK else { throw AppError("备份校验失败。") }
        defer { sqlite3_finalize(check) }
        guard sqlite3_step(check) == SQLITE_DONE else { throw AppError("备份包含无效的联系人关联。") }
        // SQLite backup replaces the active database atomically on successful completion.
        try Self.copyDatabase(from: imported, to: db)
        try execute("UPDATE app_metadata SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        try compact()
    }

    private static func copyDatabase(from source: OpaquePointer?, to target: OpaquePointer?) throws {
        guard let backup = sqlite3_backup_init(target, "main", source, "main") else { throw AppError("无法开始数据库备份。") }
        let result = sqlite3_backup_step(backup, -1)
        let finish = sqlite3_backup_finish(backup)
        guard result == SQLITE_DONE, finish == SQLITE_OK else { throw AppError("备份操作失败，原数据库未被完整替换。") }
    }
}
