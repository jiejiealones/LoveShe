import Foundation
import SwiftUI

@MainActor
final class AppStore: ObservableObject {
    @Published var contacts: [Contact] = []
    @Published var settings = ModelSettings()
    @Published var storage = StorageReport()
    @Published var busy = false
    @Published var error: String?
    @Published var notice: String?
    @Published var ready = false
    private(set) var database: Database?
    private var networkTask: Task<Void, Never>?
    private(set) var cache: URL?

    func start() async {
        guard database == nil else { return }
        do {
            let files = FileManager.default
            let directory = try files.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("LoveShe", isDirectory: true)
            let cache = try files.url(for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("LoveSheExports", isDirectory: true)
            try files.createDirectory(at: cache, withIntermediateDirectories: true)
            try Database.protect(cache)
            self.cache = cache
            database = try Database(directory: directory)
            if let data = UserDefaults.standard.data(forKey: "model-settings") {
                settings = try JSONDecoder().decode(ModelSettings.self, from: data)
            }
            // Remove only this app's previous temporary exports, never Files documents.
            try clearCacheFiles()
            try await refresh()
            ready = true
        } catch { self.error = "无法初始化：\(error.localizedDescription)" }
    }

    func requireDatabase() throws -> Database {
        guard let database else { throw AppError("本地数据库尚未就绪。") }
        return database
    }

    func refresh() async throws {
        let db = try requireDatabase()
        contacts = try await db.contacts()
        if let cache { storage = try await db.storage(cache: cache) }
    }

    @discardableResult
    func perform(_ operation: () async throws -> Void) async -> Bool {
        guard !busy else { error = "还有任务运行，请等待完成或取消。"; return false }
        busy = true
        defer { busy = false }
        do { try await operation(); try await refresh(); return true }
        catch { self.error = error.localizedDescription; return false }
    }

    func importFile(_ url: URL, contactID: String) async {
        await perform {
            let permitted = url.startAccessingSecurityScopedResource()
            defer { if permitted { url.stopAccessingSecurityScopedResource() } }
            let db = try requireDatabase()
            // Read in a detached task so Files/iCloud download does not block the UI.
            let data = try await Task.detached(priority: .userInitiated) {
                let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                guard size <= ChatImport.maxBytes else { throw AppError("单次导入上限 16 MB，请拆分文件。") }
                return try Data(contentsOf: url, options: .mappedIfSafe)
            }.value
            let report = try await db.importMessages(data, contactID: contactID)
            notice = report.description
        }
    }

    func saveSettings(_ value: ModelSettings, key: String) throws {
        guard !busy else { throw AppError("请等待当前任务完成。") }
        _ = try value.endpoint()
        guard !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw AppError("请填写 API 密钥。") }
        let data = try JSONEncoder().encode(value)
        try Keychain.save(key)
        UserDefaults.standard.set(data, forKey: "model-settings")
        settings = value
        notice = "模型配置已保存；尚未调用 API。"
    }

    func removeKey() throws {
        guard !busy else { throw AppError("请等待当前任务完成。") }
        try Keychain.save("")
        notice = "模型密钥已从本机钥匙串删除。"
    }

    func send(_ prepared: PreparedRequest, completion: @escaping (Consultation) -> Void) {
        guard !busy else { error = "还有任务运行。"; return }
        busy = true
        networkTask = Task {
            defer { busy = false; networkTask = nil }
            do {
                guard Date().timeIntervalSince(prepared.createdAt) < 900 else { throw AppError("预览已超过 15 分钟，请重新预览。") }
                let db = try requireDatabase()
                guard prepared.settings == settings, try await db.revision() == prepared.revision else {
                    throw AppError("资料或模型设置已更新，请重新预览。")
                }
                let key = try Keychain.read()
                let answer = try await ModelClient.send(prepared, key: key)
                try Task.checkCancellation()
                let consultation = Consultation(contactID: prepared.contact.id, mode: prepared.mode, question: prepared.question,
                                                answer: answer, messageIDs: prepared.messageIDs, memoryIDs: prepared.memoryIDs,
                                                model: prepared.settings.model)
                try await db.save(consultation)
                try await refresh()
                completion(consultation)
            } catch is CancellationError { notice = "请求已取消；服务商可能已收到资料并计费。" }
            catch let problem as URLError where problem.code == .cancelled { notice = "请求已取消；服务商可能已收到资料并计费。" }
            catch { self.error = error.localizedDescription }
        }
    }

    func cancelRequest() { networkTask?.cancel() }

    func exportBackup() async -> URL? {
        var result: URL?
        await perform {
            guard let cache else { throw AppError("导出目录不可用。") }
            let target = cache.appendingPathComponent("LoveShe-\(UUID().uuidString.prefix(8)).sqlite3")
            do { try await requireDatabase().exportBackup(to: target) }
            catch { try? FileManager.default.removeItem(at: target); throw error }
            result = target
            notice = "备份不含 API 密钥。导出的文件含全部聊天，请自行保管。"
        }
        return result
    }

    func restoreBackup(_ url: URL) async {
        await perform {
            let permitted = url.startAccessingSecurityScopedResource()
            defer { if permitted { url.stopAccessingSecurityScopedResource() } }
            // Retain a consistent pre-restore safety backup in the export cache.
            guard let cache else { throw AppError("备份目录不可用。") }
            let db = try requireDatabase()
            let previous = cache.appendingPathComponent("恢复前-\(UUID().uuidString.prefix(8)).sqlite3")
            try await db.exportBackup(to: previous)
            try await db.restoreBackup(from: url)
            notice = "已恢复备份。恢复前的数据暂存于导出缓存，请在设置中及时另存；清理缓存或重启 App 会删除这份临时备份。"
        }
    }

    func clearCache() async {
        await perform { try clearCacheFiles(); notice = "已清理临时导出缓存。你另存到“文件”的备份不受影响。" }
    }

    private func clearCacheFiles() throws {
        guard let cache else { return }
        for file in try FileManager.default.contentsOfDirectory(at: cache, includingPropertiesForKeys: nil) {
            try FileManager.default.removeItem(at: file)
        }
    }

    func cachedExports() -> [URL] {
        guard let cache else { return [] }
        return ((try? FileManager.default.contentsOfDirectory(at: cache, includingPropertiesForKeys: nil)) ?? []).sorted { $0.lastPathComponent < $1.lastPathComponent }
    }
}
