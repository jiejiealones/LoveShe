import SwiftUI

@main
struct LoveSheApp: App {
    @StateObject private var store = AppStore()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .tint(Color(red: 0.17, green: 0.40, blue: 0.33))
                .task { await store.start() }
                .overlay {
                    if scenePhase != .active {
                        ZStack { Color(.systemBackground).ignoresSafeArea(); Label("LoveShe · 本地工作台", systemImage: "lock.fill").font(.title3) }
                    }
                }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var store: AppStore
    @State private var editing: Contact?

    var body: some View {
        TabView {
            NavigationStack {
                Group {
                    if !store.ready {
                        VStack(spacing: 16) {
                            if store.error == nil { ProgressView("正在打开本地工作台…") }
                            else { Text("本地数据暂时无法打开，请解锁手机并重新启动 App。") }
                        }.padding()
                    } else if store.contacts.isEmpty {
                        ContentUnavailableView {
                            Label("从一个联系人开始", systemImage: "person.crop.circle.badge.plus")
                        } description: {
                            Text("聊天和记忆保存在这台 iPhone。导入记录后，再决定哪些资料发送给 AI。")
                        } actions: {
                            Button("添加联系人") { editing = Contact(name: "") }.buttonStyle(.borderedProminent)
                        }
                    } else {
                        List(store.contacts) { contact in
                            NavigationLink(value: contact.id) {
                                HStack(spacing: 14) {
                                    Text(String(contact.name.prefix(1))).font(.title2).frame(width: 42, height: 42).background(.green.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text(contact.name).font(.headline)
                                        Text("\(contact.messageCount) 条消息 · \(contact.memoryCount) 条记忆").font(.caption).foregroundStyle(.secondary)
                                    }
                                }.padding(.vertical, 5)
                            }
                        }
                    }
                }
                .navigationTitle("LoveShe")
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button { editing = Contact(name: "") } label: { Image(systemName: "plus") }
                            .disabled(!store.ready || store.busy).accessibilityLabel("添加联系人")
                    }
                }
                .navigationDestination(for: String.self) { id in
                    if let contact = store.contacts.first(where: { $0.id == id }) { ContactView(initial: contact) }
                    else { ContentUnavailableView("联系人已删除", systemImage: "person.crop.circle.badge.xmark") }
                }
                .sheet(item: $editing) { contact in ContactEditor(contact: contact) }
            }
            .tabItem { Label("联系人", systemImage: "person.2") }
            NavigationStack { SettingsView() }.tabItem { Label("设置与空间", systemImage: "internaldrive") }
        }
        .safeAreaInset(edge: .bottom) {
            if store.busy {
                HStack {
                    ProgressView(); Text("正在处理，请保持 App 打开…").font(.caption)
                    Spacer()
                    Button("取消 AI 请求") { store.cancelRequest() }.font(.caption)
                }.padding(12).background(.regularMaterial)
            }
        }
        .alert("操作未完成", isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
            Button("知道了", role: .cancel) { store.error = nil }
        } message: { Text(store.error ?? "") }
        .alert("提示", isPresented: Binding(get: { store.notice != nil && store.error == nil }, set: { if !$0 { store.notice = nil } })) {
            Button("知道了", role: .cancel) { store.notice = nil }
        } message: { Text(store.notice ?? "") }
    }
}

struct ContactEditor: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State var contact: Contact

    var body: some View {
        NavigationStack {
            Form {
                Section("联系人") { TextField("备注名", text: $contact.name) }
                Section("沟通目标") { TextEditor(text: $contact.goal).frame(minHeight: 90) }
                Section("认识经过与背景") { TextEditor(text: $contact.background).frame(minHeight: 150) }
                Section { Text("这些背景会出现在 AI 外发预览中，由你决定是否发送。").font(.footnote).foregroundStyle(.secondary) }
            }
            .navigationTitle("联系人档案")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        Task {
                            let saved = await store.perform { try await store.requireDatabase().save(contact) }
                            if saved { dismiss() }
                        }
                    }.disabled(store.busy || contact.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }
}
