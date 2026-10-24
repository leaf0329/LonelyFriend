# 记忆改进前的部署参数

对应 Git 标签 `baseline-deployed-2026-10-24`。这些文件来自当前本机部署，供对照和重建使用。

密码、令牌、API 密钥、Cookie 及账号标识已移除或替换为占位符。此目录不含聊天记录、记忆库、登录状态或安装包，不能直接作为本机私密配置覆盖运行。

- `cmd_config.json`：AstrBot 主配置的脱敏副本。
- 五个 `astrbot_plugin_*_config.json`：对应插件的脱敏配置副本。
- 上游源代码和版本见根目录 `sources.lock.json`；当前本机 Python 依赖见 `requirements-windows.lock.txt`。

本阶段只验证了单条持久记忆的新会话召回；复杂事件、相似经历区分、记忆更正及跨会话隐私边界尚未完成系统对照验收。记忆配置保留原部署值，包括 30 天的已总结时间线保留策略，便于后续对比改进。

首次重建时，将 `config/cuteq-private.example.json` 复制到本机 `data/cuteq-private.json`，填入自己的 API 密钥，并为管理密码和两个令牌分别生成随机值。然后按主 README 的安装与初始化步骤操作。
