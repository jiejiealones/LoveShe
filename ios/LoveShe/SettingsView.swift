import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var draft = ModelSettings()
    @State private var key = ""
    @State private var export: URL?
    @State private var restoring = false
    @State private var pendingRestore: URL?
    @State private var confirmRestore = false
    @State private var clearCache = false
    @State private var removeKey = false
    @State private var exports: [URL] = []

    var body: some View {
        Form {
            Section("在线模型 · 使用自己的 API") {
                TextField("HTTPS API 地址，例如 …/v1", text: $draft.baseURL)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                TextField("模型名称", text: $draft.model).textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("API 密钥", text: $key).textInputAutocapitalization(.never).autocorrectionDisabled()
                Stepper("最近消息：最多 \(draft.recentLimit) 条", value: $draft.recentLimit, in: 20...200, step: 20)
                Stepper("输出上限：\(draft.maxOutputTokens) tokens", value: $draft.maxOutputTokens, in: 256...4096, step: 256)
                Button("保存模型配置") {
                    do { try store.saveSettings(draft, key: key) }
                    catch { store.error = error.localizedDescription }
                }.disabled(store.busy)
                Button("删除本机密钥", role: .destructive) { removeKey = true }.disabled(store.busy)
                Text("仅支持 Chat Completions 兼容的文字 API。密钥保存在设备钥匙串，不包含在聊天备份中，也不通过 iCloud 同步。保存配置不会调用模型。").font(.footnote).foregroundStyle(.secondary)
            }
            Section("空间占用") {
                LabeledContent("本地数据库与日志", value: StorageReport.format(store.storage.databaseBytes))
                LabeledContent("临时导出缓存", value: StorageReport.format(store.storage.cacheBytes))
                Button("刷新占用") {
                    Task { do { try await store.refresh(); exports = store.cachedExports() } catch { store.error = error.localizedDescription } }
                }.disabled(store.busy || !store.ready)
                Button("清理导出缓存", role: .destructive) { clearCache = true }.disabled(store.busy || !store.ready)
                Text("这里是数据文件大小，不是运行内存。系统设置中的 App 总占用还包含程序本身。清理缓存不会删除聊天，但会删除尚未另存的导出备份。").font(.footnote).foregroundStyle(.secondary)
            }
            Section("联系人文字量") {
                ForEach(store.storage.contacts) { contact in LabeledContent(contact.name, value: StorageReport.format(contact.bytes)) }
                Text("按 UTF-8 统计背景、聊天、记忆和咨询正文，不含数据库索引等开销，因此与文件总大小不同。删除联系人会同时整理数据库空间。").font(.footnote).foregroundStyle(.secondary)
            }
            Section("备份与恢复") {
                Button("生成完整备份") { Task { export = await store.exportBackup(); exports = store.cachedExports() } }.disabled(store.busy || !store.ready)
                if let export { ShareLink(item: export) { Label("另存或分享最新备份", systemImage: "square.and.arrow.up") } }
                ForEach(exports, id: \.self) { url in
                    ShareLink(item: url) { Text(url.lastPathComponent).font(.caption).lineLimit(2) }
                }
                Button("从 iOS 完整备份恢复") { restoring = true }.disabled(store.busy || !store.ready)
                Text("备份包含全部联系人、聊天、记忆和咨询。恢复会覆盖当前数据。仅支持本 App 导出的 SQLite 备份；Windows 聊天请通过联系人页面导入 JSON。").font(.footnote).foregroundStyle(.secondary)
            }
            Section("本地与在线的边界") {
                Text("聊天数据库保存在本机，并排除系统云备份。设备锁定时受 iOS 文件保护；卸载 App 会删除本地数据，请先备份。若你将导出文件另存到 iCloud，那份文件会进入云端。")
                Text("AI 分析需要网络，并会发送你确认的资料。任务只在 App 运行时执行；保持 App 打开，iOS 可能暂停后台请求。取消请求不能保证免除服务商费用。")
                Text("本版本不自动读取微信、不保存图片语音附件、不运行本地大模型，也不自动按天补齐历史。记忆整理先生成候选，由你审核保存。")
            }.font(.footnote)
        }
        .navigationTitle("设置与空间")
        .onAppear {
            draft = store.settings
            do { key = try Keychain.read() } catch { store.error = error.localizedDescription }
            exports = store.cachedExports()
        }
        .onChange(of: store.ready) { _, ready in if ready { draft = store.settings } }
        .confirmationDialog("清理全部临时导出文件？", isPresented: $clearCache, titleVisibility: .visible) {
            Button("清理缓存", role: .destructive) {
                Task { await store.clearCache(); export = nil; exports = store.cachedExports() }
            }
        } message: { Text("恢复前的临时备份也会删除，请先另存需要保留的文件。") }
        .confirmationDialog("删除模型 API 密钥？", isPresented: $removeKey, titleVisibility: .visible) {
            Button("删除密钥", role: .destructive) {
                do { try store.removeKey(); key = "" } catch { store.error = error.localizedDescription }
            }
        }
        .fileImporter(isPresented: $restoring, allowedContentTypes: [.data], allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls): pendingRestore = urls.first; confirmRestore = pendingRestore != nil
            case .failure(let error): store.error = error.localizedDescription
            }
        }
        .confirmationDialog("用备份覆盖当前全部数据？", isPresented: $confirmRestore, titleVisibility: .visible) {
            Button("覆盖并恢复", role: .destructive) {
                if let url = pendingRestore {
                    Task { await store.restoreBackup(url); exports = store.cachedExports(); export = nil; pendingRestore = nil }
                }
            }
            Button("取消", role: .cancel) { pendingRestore = nil }
        } message: { Text("请先另存当前备份。恢复前会额外生成一份临时安全备份，但重启 App 或清理缓存会删除它。") }
    }
}
