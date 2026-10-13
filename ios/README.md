# LoveShe iPhone 本地版

这是原生 SwiftUI / SQLite iOS 工程，最低 iOS 17，无第三方 App 运行依赖。Python 桌面后端不随 App 启动。聊天、记忆和咨询在本机保存；AI 通过用户自己配置的 Chat Completions 兼容 HTTPS API 调用。

源码工程不是安装包。当前新增代码需要 Xcode / GitHub Actions 完成 iOS 编译与模拟器测试，并需 Apple 签名和真机验证后才能交付可安装版本。

## 已实现的首版功能

- 联系人档案：姓名、沟通目标、关系背景，联系人之间独立存储。
- 从“文件”导入或粘贴 JSON，支持通用数组及 messages / data / records 包装、WeFlow 常用字段、秒/毫秒时间戳和 ISO 8601。重复导入去重。
- SQLite 索引和分页读取，搜索原文或证据 ID。每次聊天页读取 100 条，不会启动时加载所有历史。
- 手动记忆、事实/推测/待确认状态、消息证据、修改和删除。
- 军师咨询、回复建议、记忆候选整理；发送前展示完整请求，必须明确确认。候选引用校验后由用户审核保存，不自动覆盖已有记忆。
- API 密钥存设备钥匙串，配置保存不调用模型，备份不含密钥；拒绝 HTTP 地址和 API 重定向。
- 模型回答本机保存，编辑后复制；不发送微信消息。
- 空间管理：数据库及日志真实文件大小、导出缓存大小、各联系人正文 UTF-8 字节量。
- 清理导出缓存、清理咨询、删除联系人并整理数据库；完整 SQLite 备份导出和校验恢复。
- App 进入非活动状态时遮盖工作台画面；本地文件使用 iOS complete 文件保护，排除系统云备份。

## 数据量和内存边界

- 单次导入最多 16 MB、5 万条；所有行校验后才写入事务，错误文件不进行部分导入。导入阶段仍会临时解析整个文件，应分批导入大量历史。
- 单条正文上限 256 KB。
- 咨询至多选择最新 20—200 条消息，原文预算 16000 字；最近更新的至多 200 条记忆、预算 8000 字。截断资料会注明，不代表读取全部历史。
- 模型响应上限 2 MB，输出 token 上限可设置。
- 完整备份恢复文件上限 128 MB；较大数据库仍可导出备份，但当前版本不能恢复超过此上限的文件。
- 不保存图片和语音文件、不缓存本地模型。存储页展示文件字节量，不是运行内存；真实峰值 RAM 仍需真机 Instruments 验证，不能仅凭代码保证。
- 联系人文字量不包含索引、主键和 SQLite 空闲页等开销，不应与数据库总大小直接相加比较。

## 构建和验证

直接打开 `ios/LoveShe.xcodeproj`，选择共享 Scheme **LoveShe**。无须 CocoaPods、Node.js、XcodeGen 或 Python 后端。

项目文件由以下脚本生成，新增 Swift 文件后重新执行并提交项目文件：

```sh
python3 scripts/generate_ios_project.py
```

在 Mac 上运行模拟器测试：

```sh
xcodebuild test -project ios/LoveShe.xcodeproj -scheme LoveShe \
  -destination 'platform=iOS Simulator,name=iPhone 16' CODE_SIGNING_ALLOWED=NO
```

请将设备名改为 `xcrun simctl list devices available` 中的实际名字。原生测试覆盖导入兼容、去重、联系人隔离、无效导入回滚、记忆证据、预览预算、纠正后的记忆召回，以及备份恢复与级联删除。测试不会调用真实付费模型。

仓库的 `.github/workflows/ios.yml` 在相关 PR 和 main 变更时使用 GitHub macOS runner 编译并运行模拟器测试；也支持手动触发。它会上传测试报告和**模拟器 App**，不生成可安装到真机的签名 IPA，也不访问 Apple 账户。

## 没有 Mac 时如何安装

不需要购买 Mac，但最终仍要由 Apple 签名。推荐路径：

1. 先让 GitHub Actions 模拟器检查通过，修复编译与测试问题。
2. 注册 Apple Developer Program，创建 App Store Connect 应用和专用 Bundle ID。当前工程中的 `com.example.LoveShe` 是占位 ID，签名时必须替换。
3. 在云端 macOS 构建服务或受信任的 Mac 上配置分发证书、描述文件和 App Store Connect 上传凭证，生成签名 Archive，上传 TestFlight。
4. iPhone 安装 TestFlight，接受测试邀请，再安装 LoveShe。模型配置、联系人和数据在这台手机上创建，不会从桌面自动同步。

这一步需要用户拥有相应 Apple 账号及支付能力。证书私钥、API 密钥和描述文件仅保存在受限 CI secrets 中，不发到聊天或提交仓库。模拟器检查工作流没有签名上传步骤，避免将模拟器产物误当作真机安装包。

另提供手动签名工作流 `.github/workflows/ios-sign.yml`，配置方法见 [云端签名说明](SIGNING.md)。只有手动触发、配置签名凭证后才会构建真机包；上传 TestFlight 还需要单独勾选。这个流程尚未用真实 Apple 签名凭证执行。

如果不购买开发者会员，可以借助一台受信任的 Mac 和个人 Apple 账号通过 Xcode 进行有限期的设备测试；这不是无需维护的长期分发方式。

Apple 官方说明：[会员和费用](https://developer.apple.com/programs/)、[会员能力比较](https://developer.apple.com/support/compare-memberships/)、[TestFlight](https://developer.apple.com/testflight/)。具体费用以账户所在地区和 Apple 结算页面为准。

## 首次使用

1. 添加联系人，导入项目 `examples/demo-a.json` 等虚构数据，确认消息时间和方向。
2. 在“设置与空间”填写 HTTPS API 地址、模型名和自己的密钥。地址可填写 `…/v1` 或完整 `…/chat/completions`。
3. 选择联系人，填写咨询问题或切换回复建议，点击“查看拟发送资料”。检查后勾选同意再发送。
4. 记忆整理只针对此次预览范围，返回候选后逐条核对证据和状态再保存。
5. 大量导入、删除或恢复前，生成完整备份，通过系统分享另存到“文件”。

## 重要限制

- 不是桌面版全部功能的等价移植：尚无按天增量整理、版本恢复、滚动人物摘要、反馈闭环或追问线程。新咨询会读取当前记忆，旧回答不会自动重新进入上下文。
- 记忆不是本地大模型：本地负责存储和检索，分析仍需联网。用户自选服务商是否留存数据，取决于其政策；App 隐私清单保守声明用于功能的用户内容外发，正式上架前必须按最终模型服务核对隐私披露。
- 普通 iOS App 不能读取微信私有数据库；请手动导入 JSON。本阶段没有 OCR 或截图导入。
- 不承诺 iOS 后台长时间执行，AI 生成时保持 App 打开。取消或网络错误不代表服务商一定未计费。
- 原生备份有独立 schema，不能直接恢复 Python `pilot.sqlite3`。桌面导出的聊天 JSON 可导入；联系人、记忆和咨询的完整跨平台迁移尚未实现。
- API 上下文采用有限的近期消息和最近修改的记忆；海量记忆的语义检索尚未实现。
- 导出缓存启动时清理。恢复前的临时安全备份也在此缓存里，要保留必须主动另存。
- 恢复会覆盖当前所有联系人数据。先另存当前备份，且只恢复你信任的本 App 备份。
- 删除 App 会删除其数据库，钥匙串密钥可能保留；需要移除密钥时请先在设置中删除。

## 真机发布前检查

检查大文件导入峰值 RAM、中文输入、系统“文件”导入与分享、删除后的占用、锁屏/后台中断、API 超时和费用、备份恢复、候选重复审核与证据跳转。TestFlight 和 App Store 仍需正确签名、版本号、图标、隐私政策及模型服务相关披露。当前已经包含 1024px 原创图标和隐私清单，但不代表通过 Apple 审核。
