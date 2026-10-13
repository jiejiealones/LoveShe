import Foundation

private final class NoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // Do not forward the personal API credential to a redirected destination.
        completionHandler(nil)
    }
}

enum ModelClient {
    static func send(_ prepared: PreparedRequest, key: String) async throws -> String {
        guard !key.isEmpty else { throw AppError("请在设置中填写自己的 API 密钥。") }
        let endpoint = try prepared.settings.endpoint()
        struct Body: Encodable {
            let model: String
            let messages: [ModelMessage]
            let max_tokens: Int
            let stream = false
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(Body(model: prepared.settings.model, messages: prepared.messages,
                                                       max_tokens: prepared.settings.maxOutputTokens))
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 90
        configuration.timeoutIntervalForResource = 120
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        let session = URLSession(configuration: configuration, delegate: NoRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw AppError("模型服务没有返回 HTTP 响应。") }
        guard (200..<300).contains(http.statusCode) else {
            // Do not reflect provider bodies: some services echo credentials or private prompts.
            throw AppError("模型服务返回 HTTP \(http.statusCode)。请检查 API 地址、模型、密钥与额度。")
        }
        guard response.expectedContentLength <= 2 * 1024 * 1024 else { throw AppError("模型响应超过 2 MB 上限。") }
        var data = Data()
        for try await byte in bytes {
            try Task.checkCancellation()
            guard data.count < 2 * 1024 * 1024 else { throw AppError("模型响应超过 2 MB 上限。") }
            data.append(byte)
        }
        struct Reply: Decodable {
            struct Choice: Decodable { struct Message: Decodable { let content: String? }; let message: Message }
            let choices: [Choice]
        }
        guard let reply = try JSONDecoder().decode(Reply.self, from: data).choices.first?.message.content,
              !reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw AppError("模型未返回可用文字；此 App 要求 Chat Completions 的文字响应。")
        }
        return reply
    }
}
