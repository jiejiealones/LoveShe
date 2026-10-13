import SwiftUI
import UIKit

struct PreviewView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let prepared: PreparedRequest
    let confirm: () -> Void
    @State private var consent = false

    var body: some View {
        NavigationStack {
            List {
                Section("即将发送给外部模型") {
                    LabeledContent("联系人", value: prepared.contact.name)
                    LabeledContent("模型", value: prepared.settings.model)
                    LabeledContent("消息", value: "\(prepared.messageIDs.count) 条")
                    LabeledContent("记忆", value: "\(prepared.memoryIDs.count) 条")
                    Text((try? prepared.settings.endpoint().absoluteString) ?? "地址无效").font(.caption).textSelection(.enabled)
                    Text("未确认前不会调用模型。发送后服务商可能留存资料并计费。这里不是全部历史；截断内容会明确标注。").font(.footnote).foregroundStyle(.secondary)
                }
                Section("完整拟发送资料") {
                    Text(prepared.previewText).font(.caption.monospaced()).textSelection(.enabled)
                }
                Section {
                    Toggle("我同意将上述资料发送给所选服务", isOn: $consent)
                    Button("确认发送并生成", action: confirm).disabled(!consent || store.busy)
                }
            }
            .navigationTitle("核对发送范围")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } } }
        }
    }
}

struct AnswerView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let consultation: Consultation
    @State private var candidates: [MemoryCandidate] = []
    @State private var candidateError: String?
    @State private var editing: Memory?
    @State private var draft = ""
    @State private var copied = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text(consultation.mode.label).font(.headline)
                    Text(consultation.createdAt.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                    if !consultation.question.isEmpty { Text(consultation.question).textSelection(.enabled) }
                    Text("\(consultation.model) · 发送了 \(consultation.messageIDs.count) 条消息、\(consultation.memoryIDs.count) 条记忆").font(.caption).foregroundStyle(.secondary)
                }
                if consultation.mode == .extract {
                    Section("待审核候选") {
                        if let candidateError { Text(candidateError).foregroundStyle(.orange) }
                        ForEach(Array(candidates.enumerated()), id: \.offset) { index, candidate in
                            VStack(alignment: .leading, spacing: 10) {
                                Text(candidate.content).textSelection(.enabled)
                                Text(candidate.evidenceIDs.isEmpty ? "基于用户背景，无消息证据" : "证据：" + candidate.evidenceIDs.map { String($0.prefix(12)) }.joined(separator: "、"))
                                    .font(.caption).foregroundStyle(.secondary)
                                Button("审核与保存") {
                                    Task {
                                        do {
                                            let id = consultation.id + ":candidate:\(index)"
                                            let previous = try await store.requireDatabase().memory(id, contactID: consultation.contactID)
                                            editing = previous ?? Memory(id: id, contactID: consultation.contactID,
                                                                         content: candidate.content, evidenceIDs: candidate.evidenceIDs)
                                        } catch { store.error = error.localizedDescription }
                                    }
                                }.disabled(store.busy)
                            }
                        }
                        Text("候选不会自动保存，也不会覆盖其他手动记忆。审核时请核对原始证据。").font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section("模型原始回答") { Text(consultation.answer).textSelection(.enabled) }
                Section("编辑和复制") {
                    TextEditor(text: $draft).frame(minHeight: 160)
                    Button(copied ? "已复制" : "复制编辑后的文字", systemImage: "doc.on.doc") {
                        UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: draft]], options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(300)])
                        copied = true
                    }
                    Text("编辑仅用于复制，不改变原始回答；App 不会自动发送微信消息。剪贴板内容设置为仅本机、5 分钟后过期。").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("咨询结果")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
            .sheet(item: $editing) { memory in MemoryEditor(memory: memory) }
            .onAppear {
                draft = consultation.answer
                if consultation.mode == .extract {
                    do { candidates = try PromptBuilder.candidates(answer: consultation.answer, permittedIDs: consultation.messageIDs) }
                    catch { candidateError = error.localizedDescription }
                }
            }
        }
    }
}
