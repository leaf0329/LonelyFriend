# LonelyFriend

面向长期陪伴的 QQ 角色聊天项目，基于 AstrBot、NapCat 和 DeepSeek 构建。支持指定好友私聊、群聊接话，并通过插件接入 QQ 空间、B 站和小黑盒。

项目重点是让角色在长期交谈中保留具体经历、识别人和事件的区别，并在需要时回查原始对话。默认角色为“大肥鱼”，可以自行修改人设。

## 功能

- **角色聊天**：统一人设、私聊上下文、群聊近期话题和低频主动消息。
- **聊天准入**：私聊账号、群聊和管理员分别配置；加入某个群不会自动获得私聊权限。
- **本地长期记忆**：原文、阶段总结和结构化事实分层保存；细节不足时通过工具查原文。
- **长消息保存**：将获准 QQ 来信的完整文字接入原文事务存储，避免普通采集路径截短消息。
- **持续对话**：按 token 预算整理工作上下文，保留近期原文；不按固定 80 轮直接丢弃旧上下文。
- **平台扩展**：通过现成插件使用空间草稿与发布、B 站评论和动态、小黑盒通知回复与主动浏览。
- **后台运行**：Windows 隐藏启动、停止与名单应用入口，管理面板仅监听本机地址。

## 组成

| 组件 | 职责 |
| --- | --- |
| AstrBot | 会话、人格、模型调用、工具执行和管理面板 |
| NapCat | QQ 登录与 OneBot 消息收发 |
| DeepSeek | 对话生成与历史整理 |
| MemoryCompanion | 长期记忆、原文查询和会话可见性 |
| LonelyFriend 原文保存适配器 | 将获准 QQ 来信接入完整原文存储，并保持来源和人格归属 |
| 社区插件 | QQ 空间、B 站和小黑盒接入 |

第三方组件版本和提交固定在 [sources.lock.json](sources.lock.json)。本机 Python 依赖版本见 [requirements-windows.lock.txt](requirements-windows.lock.txt)。项目原文适配器针对 AstrBot 4.29.1 和 MemoryCompanion 2.3.1，升级这两个组件时需要同步验证接口兼容。

## 环境

当前启动脚本面向 Windows，使用 Python 3.12、Git、NapCat Windows Node 版和 FFmpeg。模型默认接入 DeepSeek 官方 API 的 `deepseek-flash`。

Linux 服务器需要安装对应系统的 NapCat 和 Python 环境，并自行设置进程管理；Windows 虚拟环境不能直接复制到 Linux 使用。

## 安装

### 1. 获取项目与 Python 依赖

```powershell
git clone https://github.com/leaf0329/LonelyFriend.git
cd LonelyFriend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.lock.txt
```

### 2. 获取固定版本插件

```powershell
$manifest = Get-Content sources.lock.json -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Path data/plugins -Force | Out-Null
foreach ($pluginItem in $manifest.plugins) {
    $pluginPath = Join-Path data/plugins $pluginItem.name
    git init $pluginPath
    git -C $pluginPath remote add origin $pluginItem.repository
    git -C $pluginPath fetch --depth 1 origin $pluginItem.commit
    git -C $pluginPath checkout --detach FETCH_HEAD
}
```

以上步骤适用于首次安装。已有插件目录应先核对本地改动，再更新版本。项目自带的原文适配器会由初始化脚本安装到 AstrBot 插件目录。

### 3. 准备运行组件

从 [NapCat 发布页](https://github.com/NapNeko/NapCatQQ/releases/tag/v4.18.41) 获取 `NapCat.Shell.Windows.Node.zip`，解压后保证存在：

```text
runtime/napcat/node.exe
runtime/napcat/index.js
```

从 [FFmpeg Windows 构建页](https://www.gyan.dev/ffmpeg/builds/) 获取 essentials 构建，保留如下目录结构：

```text
runtime/ffmpeg/<构建目录>/bin/ffmpeg.exe
runtime/ffmpeg/<构建目录>/bin/ffprobe.exe
```

### 4. 配置模型与本机凭据

```powershell
Copy-Item config/cuteq-private.example.json data/cuteq-private.json
```

编辑 `data/cuteq-private.json`，填入自己的 API 密钥，并将管理密码、OneBot 令牌和 NapCat 令牌分别替换为独立随机值。不要直接使用示例占位符。可以在本机生成随机值：

```powershell
py -3.12 -c "import secrets; print(secrets.token_urlsafe(32))"
```

初始化后启动：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_semantic_model.py
.\.venv\Scripts\python.exe scripts/configure.py
.\scripts\manage.ps1 -Action Start
```

等待管理面板可访问后，初始化公开社区配置与平台命令权限：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_public_profile.py
```

`configure.py` 用于首次初始化，会写入初始模型、平台、人设和插件设置。日常应用名单使用下方入口；单独更新本项目记忆策略使用 `scripts/apply_memory_policy.py`，执行前停止服务，完成后重新启动。

本机专用角色卡可保存在 `data/persona.md`，初始化和应用记忆策略时优先使用这份文件；未配置时使用 `config/persona.md`。角色卡中的私人称呼与账号信息随 `data/` 留在本机。日常启动不会覆盖管理面板中的人格修改。

指定账号的固定称呼可通过本机 `data/persona-binding.json` 设置，字段为字符串 `owner_qq` 和 `owner_address`。绑定依据平台发送者身份，消息正文中的自报身份不会改变绑定；修改后重启服务。该文件与角色卡一样不提交到 Git。

## 使用

| 入口 | 地址或文件 |
| --- | --- |
| 管理与本地聊天 | http://127.0.0.1:6185 |
| QQ 扫码登录 | http://127.0.0.1:6099 |
| 本机登录信息 | `data/登录信息.txt` |
| 隐藏启动 | `启动cuteQ.vbs` |
| 隐藏停止 | `停止cuteQ.vbs` |
| 应用 QQ 名单 | `应用聊天名单.vbs` |

完成 QQ 扫码后，编辑 `data/cuteq-access.json`：

```json
{
  "private_qq": ["123456789"],
  "group_qq": ["987654321"],
  "admin_qq": ["123456789"]
}
```

数字仅为格式示例，请替换成自己的账号和群号，保留字符串双引号。名单为空时拒绝全部 QQ 聊天；管理员也必须在对应聊天名单内。保存后双击“应用聊天名单.vbs”。

私聊直接发消息，群聊可以 @角色，也会少量随机接话。默认随机接话概率为 6%；群聊空闲时的定时破冰关闭。私聊主动消息的间隔为 2–8 小时，23:00–08:00 安静，连续一次未获回复后暂停；可在主动聊天插件中关闭。

更多操作见 [使用说明](使用说明.md)。

## 记忆与上下文

记忆策略位于 [config/memory-policy.json](config/memory-policy.json)：

- QQ 来信原文不因总结完成或经过 30 天自动删除，自动归档与删除关闭；召回排序仍使用原生分类型指数衰减和使用强化。
- 每累计约 8 条事件或达到阶段间隔时整理记忆；保留原文以便核对总结遗漏的细节。
- 自动召回与按需原文查询配合，区别不同人物、不同事件以及后续更正。
- 本地 BGE 中文模型为结构化记忆与原文建立语义索引，与关键词、时间和权限过滤共同使用。首次下载模型后，Embedding 在本机 CPU 上运行，无需额外的 Embedding API 密钥。
- 对原生策略识别出的历史追问，会在首次回答前补入有权限的原文候选，减少模型只口头承诺查找却没有调用工具的情况；普通闲聊不强制进行原文回忆。
- 工作上下文预算为 800000 个估算 token，框架在约 656000 时触发整理，近期约 25% 的上下文按完整轮次保留。
- 私聊原文按账号和窗口隔离；小黑盒公开讨论使用独立配置，不加载 QQ 私聊记忆工具。

上下文整理是有损的，本地原文提供补查依据。检索和模型理解仍可能遗漏或混淆内容；单条超大输入也不能突破模型服务的硬性上下文限制。具体实现边界与验证方法见 [验证说明](docs/validation.md)。

升级现有安装时，先安装依赖、停止服务，再依次运行 `prepare_semantic_model.py`、`apply_memory_policy.py`、`index_memory.py`，最后重新启动。索引维护保持原有账号、人格和窗口归属，信息不足的旧记录不会被自动绑定到其他身份。设计与取舍见 [记忆设计](docs/memory-design.md)。

原文适配器保存可见的文字消息，使用底层接口的 256 KiB 单条正文边界；图片、语音和附件不作为完整媒体档案保存。角色回复沿用 MemoryCompanion 的原有采集方式。

## 社区平台

- **QQ 空间**：支持生成待审核草稿和发布说说。自动发布与自动评论默认关闭，草稿不自动取任意群聊记录作为素材。
- **B 站**：在插件详情中打开 BiliBot 控制中心扫码，也可由管理员使用 `/bili登录` 和 `/bili确认`。评论、动态和其他自动账号行为需要分别启用。
- **小黑盒**：在插件管理页扫码，并为 `cuteq_xiaoheihe` 适配器绑定账号档案。默认禁用适配器并保留模拟运行，可先核对生成内容再开放真实发送。

空间和 B 站的聊天命令仅管理员可用。不同平台沿用统一基础人设，但不自动全量共享私人记忆，也未实现所有平台活动的统一回忆。账号登录、内容发送和实际可见性需要逐个平台核对。

## 数据与隐私

`data/` 包含密钥、密码、平台登录状态、名单、聊天和记忆，不应公开提交。`logs/` 可能含聊天或管理信息；两者均已加入 Git 忽略列表。

本地保存不等于本地推理：被选入上下文的记忆片段会随请求发给配置的模型服务。迁移到服务器后，数据库位于服务器。长期保留会增加磁盘占用；需要清除某段经历时，应同时检查原文与派生记忆。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/verify.py
```

`evaluate_memory.py`、`verify_context.py` 和 `verify_pipeline.py` 会调用真实模型并消耗 API 额度，使用虚构资料和隔离数据库。测试记录、对照方法和覆盖范围放在 [docs/validation.md](docs/validation.md)。

## 上游项目

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)
- [MemoryCompanion](https://github.com/menglimi/astrbot_plugin_memory_companion)
- [主动聊天插件](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat)
- [QQ 空间插件](https://github.com/Zhalslar/astrbot_plugin_qzone)
- [B 站 AI Bot](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot)
- [小黑盒适配器](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter)
