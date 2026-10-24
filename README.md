# LonelyFriend：大肥鱼 QQ 与社区角色

2026-10-24 已完成本机安装、配置和本地联调。用户入口见 [使用说明](使用说明.md)。项目位置为 `E:\File\cuteQ`，不再包含桌宠，也不依赖桌宠运行。

## 当前状态

| 能力 | 当前结果 |
| --- | --- |
| 本地文字聊天 | 已接入官方 DeepSeek `deepseek-flash`，经过真实 API 调用验证 |
| 大肥鱼人设 | 已写入 AstrBot 原生人格 `dafeiyu`，模板在 `config/persona.md` |
| 长期记忆 | 单条记录的写入、重启和新会话召回已通过；复杂个人经历的长期回忆尚未验收 |
| QQ 接入 | NapCat 已隐藏运行，OneBot 反向连接配置完成，等待用户手机扫码 |
| 私聊和群聊名单 | 使用原生前置白名单；6 项权限测试通过；真实本地 OneBot 测试拒绝未授权私聊且未写入角色记忆 |
| 群聊接话 | 配置了 @唤醒、150 条群聊近期历史和 6% 主动回复抽样，真实群聊体验待登录后验收 |
| QQ 空间 | 插件加载成功，草稿与发布命令就绪；自动发布关闭，真实发布未验证 |
| B 站 | 插件和视频工具加载可用，等待扫码；自动评论与其他账号操作关闭，真实评论未验证 |
| 小黑盒 | 插件加载成功，公开社区配置与 QQ 私聊分离；适配器禁用并保留模拟运行，等待扫码验收 |
| 跨平台连续性 | 共用基础人设；没有全量互通私人记忆，也没有完成所有平台活动的统一回忆 |
| 后台运行 | AstrBot、NapCat 均隐藏启动，管理服务仅监听回环地址，没有设置开机自启 |
| 服务器 | 本轮没有部署远程服务器 |

没有登录任何社交账号，没有发送真实 QQ 消息、公开帖子或评论。账号登录、指定好友与群、各平台公开活动范围需要由账号所有者完成或确定。

## 组件与职责

```text
QQ账号 ←→ NapCat ←OneBot→ AstrBot ←→ DeepSeek Flash
                           ├─ 人格、近期对话
                           ├─ 陪伴记忆、低频主动聊天
                           ├─ QQ空间插件
                           ├─ B站插件（独立账号与活动记忆）
                           └─ 小黑盒适配器（公开社区配置）
```

采用 AstrBot 4.29.1、NapCat 4.18.41、Python 3.12.14、FFmpeg 9.0.2。五个插件均使用上游代码，没有修改上游源码；安装来源、版本和提交记录在 [sources.lock.json](sources.lock.json)。

## 访问与权限

- AstrBot 管理与 WebChat：`http://127.0.0.1:6185`。
- NapCat QQ 登录：`http://127.0.0.1:6099`。
- OneBot 接收：`ws://127.0.0.1:6199/ws`，与 NapCat 使用随机生成的共享令牌。
- 密码和管理 Token 在本机 `data/登录信息.txt`；API 密钥与本机私密配置在 `data/cuteq-private.json`。
- `data/cuteq-access.json` 中的 `private_qq`、`group_qq`、`admin_qq` 都是数字字符串列表；管理员身份不绕过聊天名单。
- 原生白名单采用完整会话标识 `cuteq_qq:FriendMessage:QQ号` 和 `cuteq_qq:GroupMessage:群号`，避免群号与私聊账号相互扩大权限。
- AstrBot 原生空白名单表示不限访问，因此本项目在名单为空时使用一个不匹配任何会话的占位项。
- 空间和 B 站的 52 个命令通过原生命令权限设置限制为管理员，包括评论、点赞、登录和发布入口。
- 大肥鱼的 Agent 人格当前只开放记忆工具；未开放平台写入工具或电脑控制工具。
- 小黑盒会话前缀 `cuteq_xiaoheihe::` 路由到原生配置“大肥鱼·公开社区”，只使用小黑盒插件，不加载 QQ 私聊记忆和账号工具。该配置的 QQ 白名单豁免不会改变 QQ 主配置。

QQ 私聊记忆禁止自动跨入群聊，空间草稿不会自动采集群聊记录。B 站安全共享保持关闭；未来若绑定主人身份，可使用上游现有的受限摘要共享，再单独验证实际边界。

## 文件与常用操作

| 位置 | 用途 |
| --- | --- |
| `启动cuteQ.vbs` / `停止cuteQ.vbs` | 无窗口启动、停止项目后台进程 |
| `应用聊天名单.vbs` | 校验名单后后台重启并应用；不重置模型或账号配置 |
| `config/persona.md` | 初始人设模板 |
| `scripts/manage.ps1` / `scripts/napcat.ps1` | 管理本项目记录的进程，核对 PID、程序路径和创建时间 |
| `scripts/set_access.py` | 应用名单；`--check` 只检查 |
| `scripts/configure.py` | **初始化脚本，会重置模型、平台和插件初始设置，日常不要用来应用名单** |
| `scripts/prepare_public_profile.py` | 初始化公开社区路由和管理员命令权限，需要 AstrBot 已启动 |
| `scripts/verify.py` | 本地集成验收；`--chat` 额外调用真实模型验证验收记忆，会消耗 API 额度 |
| `tests/test_access.py` | 原生访问控制的 6 项针对性测试 |
| `.venv/` | 项目 Python 运行环境 |
| `runtime/` | NapCat 与 FFmpeg，本机运行必须保留 |
| `data/` | 账号、配置、插件、角色、对话与记忆，不公开上传 |
| `logs/` | 运行日志，可能含聊天或管理信息，不公开上传 |
| `.cache/` | 安装下载与依赖缓存；不是历史版本备份 |

日常使用不需要运行命令。维护时，在项目根目录使用：

```powershell
.\.venv\Scripts\python.exe scripts\set_access.py --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\verify.py
```

已经验证隐藏启动入口、模型真实回复、重启后新会话召回、未授权私聊阻断、插件加载、管理员命令权限、视频生成与读取。验收记录为 `data/verification.json`。这些结果不等同于各平台真实账号登录或公开内容可见性验收。

## 重建与迁移

在兼容的 Python 3.12 独立环境安装 `requirements.txt`，再按 `sources.lock.json` 的仓库与提交恢复五个插件到 `data/plugins/`。插件额外依赖应使用对应提交的要求；当前主要新增项已固定在 `requirements.txt`，其余由 AstrBot 自身依赖提供。安装 Windows Node 版 NapCat 和 FFmpeg 到 `runtime/`，首次配置时准备私密配置、运行 `scripts/configure.py`，启动后运行 `scripts/prepare_public_profile.py`。

现有安装可直接使用，重建步骤供维护时参考；未验收在一台全新电脑上从零恢复。跨系统迁移需重装运行环境和对应平台的 NapCat，保留 `data/` 中的账号、配置和记忆，并重新核对路径与登录状态。Python 虚拟环境还依赖当前用户 AppData 中的 Python 3.12.14，不应单独删除那个解释器。

本项目仓库为 [leaf0329/LonelyFriend](https://github.com/leaf0329/LonelyFriend)，本地目录继续使用 `E:\File\cuteQ`。记忆改进前的部署基线标记为 `baseline-deployed-2026-10-24`。`config/deployed-baseline/` 保存脱敏后的部署参数，`sources.lock.json` 保存上游版本与提交，`requirements-windows.lock.txt` 保存本机 Python 依赖版本。

模型密钥、管理密码、平台登录凭据、QQ 名单、聊天记录和记忆库留在本机，不纳入公开仓库。`data/`、`logs/`、`runtime/` 与缓存已加入忽略列表。旁边的 `E:\File\cuteQ参考资料` 是之前保留的参考材料，不属于程序运行目录。旧桌宠历史继续保存在原仓库。

## 上游资料

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)
- [陪伴记忆插件](https://github.com/menglimi/astrbot_plugin_memory_companion)
- [主动聊天插件](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat)
- [QQ 空间插件](https://github.com/Zhalslar/astrbot_plugin_qzone)
- [B 站 AI Bot](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot)
- [小黑盒适配器](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter)
- [小黑盒接口契约与验证范围](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/blob/main/docs/xiaoheihe-api-contract.md)
