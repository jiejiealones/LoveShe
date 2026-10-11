# 模型与可选微信接入

## 本机模型配置

双击 `配置模型.cmd` 或运行 `python -m loveshe.configure`。选择 `api`，填写服务商的 API 地址、实际可用模型名和密钥，重启应用。向导不联网测试、不调用模型；配置保存在数据目录下的 `model_config.json`，不应提交到 Git。

实现的是 OpenAI 兼容 Chat Completions，不代表支持所有模型服务或其他协议。外部接口必须 HTTPS，本机 localhost 接口可 HTTP；地址不得包含凭证、查询参数或片段。密钥不返回浏览器，但本地文件尚无应用层加密。

| 环境变量 | 说明 |
| --- | --- |
| `LOVESHE_BACKEND` | `api` 或实验性 `codex`；默认 `api`。 |
| `LOVESHE_API_BASE` | 基础地址，通常以 `/v1` 结尾；也接受完整 `/chat/completions` 路径。 |
| `LOVESHE_MODEL` | 实际可用模型名，默认未配置。 |
| `LOVESHE_API_KEY` | API 密钥，仅保存在后端本机。 |
| `LOVESHE_DATA_DIR` | 可写数据目录，默认源码工程的 `data/`。 |

环境变量优先于配置文件。模板见根目录 `config.example.json`；程序不会自动加载 `.env`。

实验性 `codex` 入口需自行安装、登录本机 Codex CLI 并指定可用模型；不是共享账号、额度或 API 密钥入口，不保证每个账户或 CLI 版本可用。

## 演示目录

`python -m loveshe.demo` 固定使用工程 `data/demo/`，不继承终端中的模型环境变量，也不启动自动化后台。如确需在虚构演示中调用模型，先对这个独立目录配置：

```powershell
$env:LOVESHE_DATA_DIR = Join-Path (Get-Location) 'data/demo'
python -m loveshe.configure
Remove-Item Env:LOVESHE_DATA_DIR
python -m loveshe.demo
```

实际生成前仍需核对并确认外发；可能产生费用。日常使用通过正常工作台进行。

## 可选微信入口

需要 Windows 微信、Node.js >=22.13.0 和已初始化的 WeFlow CLI。适配器固定调用 `weflow-cli@1.9.0`，首次可能下载 npm 包及依赖。

```powershell
npm exec --yes --package=weflow-cli@1.9.0 -- weflow-cli check
npm exec --yes --package=weflow-cli@1.9.0 -- weflow-cli init
```

遵循 [上游文档](https://github.com/zhuobichen/weflow-cli) 的检查与初始化提示；可能需要重新登录微信。随后在应用“数据管理”读取会话，核对联系人再绑定，手动更新或按联系人开启轮询。

仅曾在单台 Windows / 微信 4.1.15.13 验证，不保证其他版本。导出失败时保留手动 JSON 导入路径，不影响基础闭环。自动记忆须单独授权，详见 [隐私说明](privacy.md)。
