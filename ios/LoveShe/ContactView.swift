import SwiftUI
import UniformTypeIdentifiers
import UIKit

struct ContactView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let initial: Contact
    private var contact: Contact { store.contacts.first(where: { $0.id == initial.id }) ?? initial }
    @State private var tab = 0
    @State private var search = ""
    @State private var messages: [ChatMessage] = []
    @State private var memories: [Memory] = []
    @State private var consultations: [Consultation] = []
    @State private var hasMoreMessages = false
    @State private var hasMoreMemories = false
    @State private var hasMoreConsultations = false
    @State private var loading = false
    @State private var importing = false
    @State private var pasteSheet = false
    @State private var editingContact = false
    @State private var deleting = false
    @State private var clearingConsultations = false
    @State private var editingMemory: Memory?
    @State private var mode = ConsultationMode.consult
    @State private var question = ""
    @State private var prepared: PreparedRequest?
    @State private var selectedAnswer: Consultation?

    var body: some View {
        VStack(spacing: 0) {
            Picker("工作台", selection: $tab) {
                Text("聊天").tag(0); Text("记忆").tag(1); Text("咨询").tag(2)
            }.pickerStyle(.segmented).padding()
            if tab == 0 { historyView }
            else if tab == 1 { memoryView }
            else { advisorView }
        }
        .navigationTitle(contact.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("联系人档案", systemImage: "person.text.rectangle") { editingContact = true }
                    Button("导入 JSON", systemImage: "square.and.arrow.down") { importing = true }
                    Button("粘贴 JSON", systemImage: "doc.on.clipboard") { pasteSheet = true }
                    Button("清理咨询记录", systemImage: "eraser", role: .destructive) { clearingConsultations = true }
                    Button("删除联系人及全部数据", systemImage: "trash", role: .destructive) { deleting = true }
                } label: { Image(systemName: "ellipsis.circle") }.disabled(store.busy)
            }
        }
        .task { await reload() }
        .task(id: search) {
            do { try await Task.sleep(for: .milliseconds(250)); try Task.checkCancellation(); await loadMessages(reset: true) }
            catch { /* Cancelled search is superseded by the latest input. */ }
        }
        .onChange(of: store.busy) { _, busy in if !busy { Task { await reload() } } }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json], allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls): if let url = urls.first { Task { await store.importFile(url, contactID: contact.id); await reload() } }
            case .failure(let error): store.error = error.localizedDescription
            }
        }
        .sheet(isPresented: $editingContact) { ContactEditor(contact: contact) }
        .sheet(isPresented: $pasteSheet) { PasteImportView(contactID: contact.id) }
        .sheet(item: $editingMemory) { memory in MemoryEditor(memory: memory) }
        .sheet(item: $prepared) { preview in
            PreviewView(prepared: preview) {
                prepared = nil
                store.send(preview) { answer in selectedAnswer = answer; Task { await reload() } }
            }
        }
        .sheet(item: $selectedAnswer) { answer in AnswerView(consultation: answer) }
        .confirmationDialog("删除 \(contact.name) 的全部数据？", isPresented: $deleting, titleVisibility: .visible) {
            Button("永久删除联系人、聊天、记忆和咨询", role: .destructive) {
                Task {
                    if await store.perform({ try await store.requireDatabase().deleteContact(contact.id) }) { dismiss() }
                }
            }
        } message: { Text("建议先导出备份。另存到“文件”的备份不会随之删除。") }
        .confirmationDialog("清理这个联系人的全部咨询记录？", isPresented: $clearingConsultations, titleVisibility: .visible) {
            Button("删除咨询记录", role: .destructive) {
                Task { await store.perform { try await store.requireDatabase().clearConsultations(contact.id) }; await reload() }
            }
        } message: { Text("保留聊天和已保存记忆；尚未保存的记忆候选也会被删除。") }
    }

    private var historyView: some View {
        VStack {
            TextField("搜索原文或消息 ID", text: $search).textFieldStyle(.roundedBorder).padding(.horizontal)
            if messages.isEmpty {
                ContentUnavailableView("暂无匹配的聊天", systemImage: "bubble.left.and.bubble.right", description: Text("从右上角导入 JSON。发送方向待确认的消息需要核对。"))
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        ForEach(messages) { message in
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text(message.senderLabel).font(.caption.bold())
                                    Text(message.timestamp.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                                }
                                Text(message.content.isEmpty ? "[空消息]" : message.content).textSelection(.enabled)
                                HStack {
                                    Text(String(message.id.prefix(12))).font(.caption2.monospaced()).foregroundStyle(.secondary)
                                    Spacer()
                                    Button("保存为记忆") {
                                        editingMemory = Memory(contactID: contact.id, content: message.content, evidenceIDs: [message.id])
                                    }.font(.caption).disabled(store.busy || message.content.isEmpty)
                                }
                            }
                            .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                            .background(message.sender == "me" ? Color.green.opacity(0.10) : Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
                        }
                        if hasMoreMessages {
                            Button("加载更早消息") { Task { await loadMessages(reset: false) } }.disabled(loading)
                        }
                    }.padding()
                }
            }
        }
    }

    private var memoryView: some View {
        List {
            Section {
                Button("添加长期记忆", systemImage: "plus") { editingMemory = Memory(contactID: contact.id, content: "") }.disabled(store.busy)
                Text("每次咨询会读取当前记忆。你可以纠正或删除；模型候选需审核，不会自动覆盖。").font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(memories) { memory in
                VStack(alignment: .leading, spacing: 10) {
                    Text(memory.status.label).font(.caption.bold()).foregroundStyle(memory.status == .fact ? Color.green : Color.orange)
                    Text(memory.content).textSelection(.enabled)
                    ForEach(memory.evidenceIDs, id: \.self) { id in
                        Button("证据 \(id.prefix(12)) ↗") { search = id; tab = 0 }.font(.caption.monospaced())
                    }
                    Button("修改或删除") { editingMemory = memory }.font(.caption).disabled(store.busy)
                }.padding(.vertical, 5)
            }
            if hasMoreMemories {
                Button("加载更多记忆") { Task { await loadMemories(reset: false) } }.disabled(loading)
            }
        }
    }

    private var advisorView: some View {
        List {
            Section("这次想做什么") {
                Picker("模式", selection: $mode) { ForEach(ConsultationMode.allCases, id: \.self) { Text($0.label).tag($0) } }
                TextEditor(text: $question).frame(minHeight: 100).accessibilityLabel("问题或补充要求")
                Text(mode == .consult ? "填写问题；咨询使用当前背景、近期消息和记忆。" : "补充要求可不填。整理记忆仅覆盖本次预览资料，不会自动补齐全部历史。")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("查看拟发送资料") {
                    Task {
                        await store.perform {
                            _ = try store.settings.endpoint()
                            prepared = try await store.requireDatabase().prepare(contactID: contact.id, mode: mode, question: question, settings: store.settings)
                        }
                    }
                }.disabled(store.busy)
            }
            Section("最近咨询") {
                ForEach(consultations) { answer in
                    Button { selectedAnswer = answer } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(answer.question.isEmpty ? answer.mode.label : answer.question).lineLimit(2).foregroundStyle(.primary)
                            Text(answer.answer).font(.caption).lineLimit(2).foregroundStyle(.secondary)
                            Text(answer.createdAt.formatted(date: .abbreviated, time: .shortened)).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
                if hasMoreConsultations {
                    Button("加载更多咨询") { Task { await loadConsultations(reset: false) } }.disabled(loading)
                }
            }
        }
    }

    private func reload() async {
        await loadMessages(reset: true)
        await loadMemories(reset: true)
        await loadConsultations(reset: true)
    }

    private func loadMessages(reset: Bool) async {
        let query = search
        loading = true; defer { loading = false }
        do {
            let page = try await store.requireDatabase().messages(contact.id, search: query, offset: reset ? 0 : messages.count)
            guard query == search, !Task.isCancelled else { return }
            if reset { messages = page } else { messages += page }
            hasMoreMessages = page.count == 100
        } catch { store.error = error.localizedDescription }
    }

    private func loadMemories(reset: Bool) async {
        loading = true; defer { loading = false }
        do {
            let page = try await store.requireDatabase().memories(contact.id, offset: reset ? 0 : memories.count)
            if reset { memories = page } else { memories += page }
            hasMoreMemories = page.count == 200
        } catch { store.error = error.localizedDescription }
    }

    private func loadConsultations(reset: Bool) async {
        loading = true; defer { loading = false }
        do {
            let page = try await store.requireDatabase().consultations(contact.id, offset: reset ? 0 : consultations.count)
            if reset { consultations = page } else { consultations += page }
            hasMoreConsultations = page.count == 30
        } catch { store.error = error.localizedDescription }
    }
}

struct PasteImportView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let contactID: String
    @State private var json = ""
    var body: some View {
        NavigationStack {
            Form {
                Section("粘贴聊天 JSON") { TextEditor(text: $json).frame(minHeight: 300).font(.body.monospaced()) }
                Section { Text("格式与桌面版通用 JSON 相同。单次最多 16 MB、5 万条；重复消息会去重。").font(.footnote) }
            }.navigationTitle("粘贴导入")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("导入") {
                            Task {
                                let saved = await store.perform {
                                    let report = try await store.requireDatabase().importMessages(Data(json.utf8), contactID: contactID)
                                    store.notice = report.description
                                }
                                if saved { dismiss() }
                            }
                        }.disabled(store.busy || json.isEmpty)
                    }
                }
        }
    }
}

struct MemoryEditor: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State var memory: Memory
    @State private var deleting = false
    var body: some View {
        NavigationStack {
            Form {
                Section("记忆内容") { TextEditor(text: $memory.content).frame(minHeight: 180) }
                Section("判断状态") { Picker("状态", selection: $memory.status) { ForEach(MemoryStatus.allCases, id: \.self) { Text($0.label).tag($0) } } }
                Section("证据") {
                    if memory.evidenceIDs.isEmpty { Text("用户主动提供，无消息证据").foregroundStyle(.secondary) }
                    ForEach(memory.evidenceIDs, id: \.self) { Text($0).font(.caption.monospaced()).textSelection(.enabled) }
                }
                Section {
                    Button("删除这条记忆", role: .destructive) { deleting = true }.disabled(store.busy)
                    Text("修改和删除会影响下次咨询；旧咨询记录保留作为历史，不会自动发送给模型。").font(.footnote).foregroundStyle(.secondary)
                }
            }.navigationTitle("审核与编辑记忆")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("保存") {
                            Task { if await store.perform({ try await store.requireDatabase().save(memory) }) { dismiss() } }
                        }.disabled(store.busy || memory.content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
                .confirmationDialog("删除这条记忆？", isPresented: $deleting, titleVisibility: .visible) {
                    Button("删除", role: .destructive) {
                        Task { if await store.perform({ try await store.requireDatabase().deleteMemory(memory.id, contactID: memory.contactID) }) { dismiss() } }
                    }
                }
        }
    }
}
