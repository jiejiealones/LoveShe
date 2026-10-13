# 没有 Mac 时的云端签名与 TestFlight

本仓库提供 `.github/workflows/ios-sign.yml` 手动签名流程。它不会在 PR、push 或定时任务时发布 App。先完成模拟器检查、审核工程和安装流程，再决定是否注册付费会员。

## 账号和费用

需要 Apple Developer Program 会员、App Store Connect 应用记录、专用 Bundle ID 和分发签名凭证。Apple 公布会员费为 US$99/年，地区价格以结算页面为准。[Apple 官方说明](https://developer.apple.com/programs/)

GitHub macOS runner 的使用与计费取决于仓库可见性、账户计划和当时的 Actions 政策；本工程不要求租用长期运行的 Mac 或云服务器。AI API 仍按服务商用量另计。

注册、付款、身份验证和 Apple 控制台配置必须在你自己的账户中完成。不需要把 Apple 账号密码或任何私钥发到聊天里。测试版尚未经真机验证，建议先确认你接受安装方式和会员费用。

## 签名凭证准备

1. 在 Apple Developer 控制台创建与 App Store Connect 一致的显式 App ID，例如你自己的 `com.yourname.LoveShe`，不要使用工程占位 ID。
2. 创建 Apple Distribution 证书，以及与这个 App ID 和证书匹配的 **App Store** 分发描述文件。
3. 将分发证书和对应私钥导出成带密码的 `.p12`。没有 Mac 时，可使用可信云端 macOS 服务，或在受信任的环境用 OpenSSL 生成私钥和 CSR，在 Apple 控制台签发证书后打包 P12。私钥必须由你控制并妥善保管。
4. 若需要自动上传 TestFlight，在 App Store Connect 创建具有相应上传权限的 API key，保存 `.p8`、Key ID 和 Issuer ID。仅有分发证书不足以授权上传。

凭证均不能进入 Git 历史。工作流不替你注册账号、创建 Apple 证书或绕过会员要求。

## 配置 GitHub environment

在仓库 Settings → Environments 创建 **ios-release**，建议设置 required reviewer 和分支限制。工作流使用这个 environment 的 variables/secrets；仓库 variables/secrets 也可作为账户配置，但环境隔离更清楚。

| 类型 | 名称 | 内容 |
| --- | --- | --- |
| Variable | `IOS_TEAM_ID` | 10 位 Apple Team ID |
| Variable | `IOS_BUNDLE_ID` | 你注册的 App ID / Bundle ID |
| Secret | `IOS_DISTRIBUTION_P12_BASE64` | 分发证书及私钥 P12 文件的 Base64，单行无换行 |
| Secret | `IOS_P12_PASSWORD` | P12 导出密码，须非空 |
| Secret | `IOS_PROFILE_BASE64` | App Store 描述文件的 Base64，单行无换行 |
| Variable，上传时需要 | `ASC_KEY_ID` | App Store Connect API Key ID |
| Variable，上传时需要 | `ASC_ISSUER_ID` | App Store Connect API Issuer ID |
| Secret，上传时需要 | `ASC_PRIVATE_KEY_BASE64` | API `.p8` 私钥文件的 Base64，单行无换行 |

通过 GitHub 的 secrets 设置界面保存这些值，避免将 Base64 内容复制到公开命令输出、Issue、PR 或聊天中。Base64 是编码，不是加密。

## 运行流程

工作流进入默认分支后，在 Actions 中选择 **iOS signed build (manual)** → Run workflow。先保持 `upload_to_testflight` 关闭，只验证 Archive 和 IPA 导出；通过后，可在后续手动运行时勾选上传。新上传的 build number 使用该工作流的 run number。

流程会：

1. 检查配置和描述文件的 Team ID、Bundle ID、分发类型与有效期。
2. 选择已安装的 Xcode 26 或更新版本，满足当前 App Store Connect SDK 要求。[Apple SDK 要求](https://developer.apple.com/news/upcoming-requirements/)
3. 将签名私钥导入临时钥匙串，安装描述文件；使用你的 ID 生成分发 Archive 和 IPA。
4. 上传签名 IPA 作为短期 GitHub artifact；**App Store 分发 IPA 不能直接侧载安装**。
5. 仅在明确勾选时用 App Store Connect API key 上传 TestFlight。Apple 处理完成后，在 TestFlight 添加测试者并接受邀请安装；外部测试可能需要 Beta App Review。
6. 完成后删除 runner 上的临时签名文件、描述文件和钥匙串。

它不会购买会员、执行付款、自动邀请测试者或发布到正式 App Store。GitHub environment 的 reviewer 门槛应由仓库所有者配置；声明 environment 名称本身并不自动启用审批。

## 发布前仍需检查

- 签名流程当前只有配置和语法检查，尚未使用真实证书、Apple API key 或 TestFlight 上传验证。证书信任、App Store Connect 角色、出口合规和审核要求要以实际 Apple 响应为准。
- 先通过模拟器测试，再使用少量虚构数据在真机检查导入、锁屏中断、空间统计、备份恢复及真实 API 兼容性。
- 该 App 使用 HTTPS / 系统安全机制，工程初始设置了非豁免加密申报为否；提交时需根据最终实现和地区要求核对出口合规答案。
- 隐私清单已声明用于功能的用户内容外发；根据最终模型服务商的数据政策补齐隐私政策和 App Store Connect 隐私问卷。
- TestFlight 是测试分发，不是永久安装保证。后续需要按 Apple 的测试有效期和会员政策维护更新。[TestFlight 官方说明](https://developer.apple.com/testflight/)
