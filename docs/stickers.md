# 本地大肥鱼表情库

图库用于 QQ 聊天中的偶尔表达，不需要额外的表情插件、图片托管或向量服务。模型通过 `lonelyfriend_sticker` 提供情绪和场景词，本项目从审核通过的本地条目中选择图片，只发送到正在对话的 QQ 会话。

## 准备与审核

```powershell
.\.venv\Scripts\python.exe scripts/prepare_stickers.py
```

下载清单位于 `config/stickers.json`。脚本验证格式、尺寸和单图大小，将原图保存至 `data/stickers/files/`，生成运行目录 `catalog.json`、导入报告与离线审核页面 `data/stickers/gallery.html`。已有文件通过检查后直接复用，不重新下载。下载失败的条目写入报告，脚本以失败状态退出。

用浏览器打开 `gallery.html`，搜索编号、情绪和标签，点击缩略图查看原图或播放 GIF。“手动保留 / 手动排除”保存在当前浏览器，点击“导出我的审核”保存 JSON。页面无需启动服务，也不会上传图片、审核结果或修改正在运行的机器人。

导出文件是待应用的审核清单，不会直接改变运行中的图库。应用前将排除项改为 `review.status: reserved`，确认保留的条目改为 `approved`，再生成运行目录。自动选择只读取 `approved` 项；未审、仅收藏和缺失文件不参与发送。

导入时明确选择未标记项的处理规则。例如，已确认“没操作的都保留”时使用：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_stickers.py --review "审核结果.json" --unmarked keep
```

默认的 `--unmarked initial` 沿用初审状态；`keep` 允许未标记项进入可用范围，包括初审时的仅收藏项。明确排除始终优先。导入结果保存在私人目录的 `user-review.json`，以后重新准备图库时自动复用，不会恢复已经排除的图片。公开来源目录保留初审记录，不包含个人的选择。

当前收集 583 张，其中 323 张动图，原图合计约 600.4 MiB。第一轮审核给出 516 张可用候选与 67 张仅收藏，最终使用范围由人工复核决定。原图不做去水印、重绘、转静图或二次压缩；缩略图单独生成，播放 GIF 时读取原文件。

第一轮审核对静图检查整张缩图，疑义处查看原图；动图检查首、中、尾帧，不能保证每一中间帧都正确。来源描述保存于 `source_metadata` 便于追溯，自动搜索只使用审核后的名称、情绪、标签和说明。

修正示例：蹭仙人掌流泪不再标为“喜欢、亲近”；吸走灵魂不再标为“舒服的头部按摩”；“这赢不了”不再附会为拒绝用户需求。含“用户发情了”的图与原说明“发火了”不符，先保留收藏。带发布日期、活动优惠、难以确定的长篇剧情、非表情立绘及部分重复图也列为仅收藏。

## 发送行为

- 每轮最多尝试一张，并行工具调用不会连发多张。失败不报告成功，也不自动重试。
- 同一会话尽量避开最近五张。多个近似匹配中随机选择；关键词匹配仍可能不够准确。
- 人设提示鼓励偶尔用图，认真讨论优先文字，不连续几轮贴图。跨轮频率由模型决定，不是固定概率或硬性冷却。
- 没有匹配时不临时上网找图。发送目标来自当前事件，没有任意指定收件人的参数。
- QQ 使用 OneBot 图片段的 `sub_type: 1`，从本地原文件编码传送，保留 GIF 数据。文字仍经过既有分段链路。

本模块针对仓库固定的 AstrBot / NapCat 版本验证；升级后需复核图片调度接口。单图最多 8 MiB，尺寸不超过 4096 像素。两个超大动图未下载，来源保留在清单的 `excluded` 中。内存常驻标签目录，发送时读取当前图片；图片发送会消耗服务器出站流量。

## 来源与条款

| 来源 | 内容与署名 | 条款 |
| --- | --- | --- |
| [可以叫我云大人 / 雾雪](https://kiriyuki.com)（[B站作者](https://space.bilibili.com/103739)） | 321 张大肥鱼及双人 GIF；目录参考 [dsh-nekowhale-stickers](https://github.com/Loping151/dsh-nekowhale-stickers) 的固定修订 | 知识数据 CC BY-NC 4.0；原图独立条款允许保留作者链接及网站的非商业分享 |
| [蓝色大肥鱼档案馆](https://fisharchive.cc) / EDMOK 与各原作者 | 262 张静图及动图，来自[公开仓库](https://github.com/EDMOK/blue-fish-archive) | [站点说明](https://fisharchive.cc/about)标示 CC BY-NC-SA 4.0，图片权利归各原作者 |

大肥鱼角色设计参考 ZipZipPipe（[作者页](https://space.bilibili.com/4168597)）与上善无形（[作者页](https://space.bilibili.com/4456176)）；GPT 娘设计参考 ZipZipPipe。原作者署名及图片条款保存在 `docs/licenses/nekowhale-stickers-NOTICE.txt` 和 `nekowhale-stickers-ASSETS.txt`。本项目代码许可不改变第三方图片和标注许可。

公开仓库仅保存来源、目录、审核标签和集成代码，不提交原图合集或私人审核文件。
