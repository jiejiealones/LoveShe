# 第三方工具与设计参考

LoveShe 原创代码采用 MIT，第三方项目保留各自许可证。本仓库不打包其他项目源码或模型权重。

## 可选运行工具

- [WeFlow CLI](https://github.com/zhuobichen/weflow-cli)：微信读取适配器通过子进程调用 npm 包 `weflow-cli@1.9.0`，不内嵌其源码。首次使用可能从 npm 下载该工具及依赖。上游声明 MIT；[许可证](https://github.com/zhuobichen/weflow-cli/blob/master/LICENSE)、[依赖清单](https://github.com/zhuobichen/weflow-cli/blob/master/package.json)。2026-10-11 核对上游版本为 1.9.0、Node 要求 >=22.13.0。许可证与依赖以实际安装包为准；其依赖不因此统一变为 MIT。
- Codex CLI：可选实验性模型入口，通过本机安装的命令行调用；本仓库不分发该工具、登录凭证或账户额度。使用者需自行安装、登录并确认可用模型；它不是通用 API 密钥入口。
- Python 标准库：核心服务没有第三方 Python 运行依赖。构建包使用 setuptools，开发验证使用 Node 检查前端语法，均不打包其运行时。

## 设计参考与调研

| 项目 | 本项目使用范围 |
| --- | --- |
| [狗头军师](https://github.com/shengjidaguai-china/goutoujunshi) | 参考事实、推测与未知的分析边界；未复制完整知识库或话术模板。 |
| [LearnLove](https://github.com/jx-2-a/LearnLove) | 参考按联系人组织记忆和压缩的数据流；未复制代码。 |
| [she-love-me](https://github.com/863401402/she-love-me) | 调研时独立验证转换和统计；本应用自行实现导入与基础统计，不分发其脚本。 |
| [backseat-ai](https://github.com/Miseat/backseat-ai)、[WechatVibe](https://github.com/tswawa/WechatVibe)、[ChatLab](https://github.com/ChatLab/ChatLab) | 调研参考，未集成产品内核。 |
| [goutoujunshi-jev-chat](https://github.com/shengjidaguai-china/goutoujunshi-jev-chat)、[relationship-growth-advisor](https://github.com/Gycnr/relationship-growth-advisor) | 后续交互与复盘思路参考，未复制代码。 |

运行提示词由 LoveShe 编写，见 `core.py`、`service.py`、`memory_engine.py`。未来引入代码、知识库或模板前，应重新核对对应版本许可并保留必要声明。
