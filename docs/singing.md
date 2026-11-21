# 本地翻唱

在QQ自然语言点歌，或提供音频文件、B站BV号，由本机分离人声、转换角色音色并配回伴奏。默认转换整首，只有明确指定起止时间时截取片段。服务器运行聊天与任务入口，本机运行轻量任务服务和按需启动的GPU推理进程。音源最长60分钟，暂不自动定位副歌。

普通 TTS 负责从文字合成讲话。翻唱使用原曲人声提供旋律、时值与演唱方式，由 Seed-VC 转换音色；祀音的 GGUF 权重不直接参与歌声转换。

## 组件与资源

- [Seed-VC](https://github.com/Plachtaa/seed-vc)：源码固定到 `51383efd921027683c89e5348211d93ff12ac2a8`，使用 v1 的44.1kHz歌声转换模型。上游已归档，后续兼容维护需要自行承担。
- [Demucs 4.0.1](https://pypi.org/project/demucs/4.0.1/)：使用 `htdemucs` 分离人声与伴奏。
- Python 3.10、PyTorch 2.4.0 / CUDA 12.4、NVIDIA GPU，以及基础项目已有的 Windows FFmpeg。
- 独立依赖见 `requirements-singing.txt`。运行环境和模型保存在被 Git 忽略的 `runtime/singing/`，音频和生成记录保存在 `data/singing/`。

推理按需启动子进程，完成后退出，释放该进程占用的显存。Windows上复用祀音的 `Local\SiVoiceInference` 互斥锁，避免同时执行GPU推理；已在合成的祀音会使翻唱报忙。服务器等待翻唱期间保留普通文字回复，不再启动偶发TTS；未通过QQ提交的本机任务仍受原有15秒TTS等待上限约束。

8GB显存电脑已在祀音运行时完成22.5秒的双版本试听及约5分9秒的整首转换。短片段转换期间曾观测到整卡占用约7.4GB，余量较小。游戏等其他GPU负载可能使推理失败，不适合作为常驻模型并行运行。

## 安装

以下命令在项目根目录执行，使用 [uv](https://docs.astral.sh/uv/) 准备独立环境；不要把依赖安装到聊天机器人或祀音自己的 Python 环境。

```powershell
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\runtime\singing\python"
uv python install 3.10 --no-bin --no-registry
uv venv runtime/singing/.venv --python 3.10

git clone https://github.com/Plachtaa/seed-vc.git runtime/singing/seed-vc
git -C runtime/singing/seed-vc checkout --detach 51383efd921027683c89e5348211d93ff12ac2a8

uv pip install --python runtime/singing/.venv/Scripts/python.exe torch==2.4.0+cu124 torchaudio==2.4.0+cu124 torchvision==0.19.0+cu124 --index-url https://download.pytorch.org/whl/cu124
uv pip install --python runtime/singing/.venv/Scripts/python.exe -r requirements-singing.txt
.\runtime\singing\.venv\Scripts\python.exe scripts/prepare_singing_models.py
```

模型准备脚本从作者的 Hugging Face 仓库下载歌声模型、音高提取器、说话人编码器、Whisper 内容编码器和 BigVGAN 声码器，并下载官方 Demucs 权重。它仅请求公开模型，不发送参考录音；具体模型修订与本地路径记录在私有 `data/singing/model-manifest.json`。缓存不完整时，翻唱阶段明确报错。

## QQ使用

入口沿用现有私聊、群聊允许名单。普通群聊仍只在实际@角色或引用回复角色时启动模型对话；翻唱命令属于明确工具请求，成品回到发起请求的会话。任务列表、选歌结果和上传文件按会话与发送者隔离，名单移除后不再向该会话发送结果或状态通知。

| 命令 | 用途 |
| --- | --- |
| `/翻唱搜歌 歌名 歌手` | 搜索网易云，列出最多10个候选的歌手、专辑与时长；歌手可省略 |
| `/翻唱搜歌 QQ 歌名` | 使用QQ音乐访客接口，受平台访问限制 |
| `/翻唱搜歌 本地 关键词` | 搜索事先登记在本机的音源 |
| `/翻唱 1` | 将自己当前搜索列表的第1首整首转换，返回立体声MP3 |
| `/翻唱 1 0:30-0:50` | 仅转换指定时间段；30秒内默认语音，更长时发MP3 |
| `/翻唱 BV号` | 提取指定B站视频的完整音轨，也接受完整视频链接和分P参数 |
| `/翻唱 文件` | 转换自己在当前会话最近一小时发送的完整音频文件 |
| `/翻唱 本地:编号` | 转换已登记的本地音源，不接受任意本机路径；同样可附加起止时间 |
| `/翻唱 1 0:30-0:50 12 文件` | 升高12半音，以立体声MP3发送 |
| `/翻唱状态` | 查看自己最近的任务，可附加任务编号前缀 |
| `/取消翻唱` | 停止本地推理和后续音频发送；已进入发送阶段的消息无法用此命令撤回 |
| `/取回翻唱` | 主动再次发送自己的已完成成品，缓存过期时需重新生成 |

也可在获准私聊中自然提出“唱一首《5:20AM》”，群内自然语言请求仍需点名。模型通过 `lonelyfriend_search_music` 和 `lonelyfriend_cover_music` 两个工具操作：只有歌名时先搜歌，优先选准确匹配的原唱，确实无法区分同名歌曲时才确认歌手；不指定时间默认整首，不例行追问时长。明确指定时间段时才截取，用户只说“唱一段”而无时间时再确认，不自行猜副歌位置。`/菜单 翻唱` 可查看相关命令。

搜索列表有效期10分钟，只有本人能在原会话选择。收到文件时只暂存文件消息资料，明确请求翻唱后才下载内容。原曲上限200MB、60分钟。整首或超过30秒的片段以192kbps立体声MP3发送；明确指定的30秒内片段默认24kHz单声道QQ语音，末尾加“文件”也可取MP3。

## 音源与任务服务

初次调研中公共Meting接口出现超时或不支持搜索，因此当前取源复用现有解析器使用的平台接口与开源客户端：网易云搜索及音频接口、`qqmusic-api-python 0.9.0`、`bilibili-api-python 17.4.2`。B站仅提取独立音轨，不下载视频画面。

音乐平台可能要求账号权限或验证。没有完整播放地址、只返回试听，或实际音频时长与所选歌曲元数据不符时，任务明确失败，不更换成其他版本，也不调用第三方解锁站。可改用自己提供的文件、已登记曲库或可访问的B站音源。当前未配置音乐平台会员登录态，QQ音乐访客接口实测曾触发验证。

仅给歌名时优先保留准确匹配的标题，减少未请求的DJ或混音版本；查询中包含歌手、版本等额外条件时沿用平台搜索结果。搜索只查元数据，付费标记不能证明实际取源失败，自然语言工具不会将该标记直接报告为已经验证的权限错误。原唱与同名歌曲的识别仍依赖搜索结果和模型判断。

本地任务服务使用独立Python 3.12环境，不把取源依赖安装到聊天服务或GPU推理环境：

```powershell
uv venv runtime/singing-worker/.venv --python .venv/Scripts/python.exe
uv pip install --python runtime/singing-worker/.venv/Scripts/python.exe -r requirements-singing-worker.txt
```

本机私有配置为 `data/singing-local.json`，格式见 `config/singing-local.example.json`；服务器配置为 `data/singing.json`，格式见 `config/singing.example.json`。两者使用同一个独立随机密钥，参考录音及本地曲库路径只保存在本机配置。曲库中的编号、标题、歌手与时长需要与实际文件一致。

在原有本机 `data/siyin-link.json` 中增加：

```json
"singing": {"local_port": 9883, "remote_port": 19883}
```

连接器 `scripts/siyin_link.py` 负责隐藏启动本项目的任务服务，并通过已有SSH连接转发两个回环端口：19881用于祀音短回复，19883用于翻唱任务。任务服务监听本机127.0.0.1:9883；不需要开放公网或路由端口。祀音工作台仍由用户自行启动和关闭，翻唱任务服务可独立使用已有参考录音。

同一时间只处理一个翻唱任务，不额外排队。处理超时按实际片段长度放宽，至少10分钟，长音频按4倍时长加3分钟；服务器等待上限另留取源及传输时间。取消或任务服务退出时，Windows Job Object会结束该次推理及后代进程；虚拟环境启动器通过启动握手避免产生未纳入管理的推理进程。任务请求编号保证重复提交同一请求不会重复计算。

同一音源、片段、移调、参考录音及推理配置可复用成品。验证可发送成品后发布缓存，超过30秒只保留MP3，不额外缓存整首WAV。缓存保留最多7天、目标上限128MB，原曲分离等中间文件随任务释放。上传暂存保留1小时，总量不超过400MB；任务记录保留最近100项。服务器流式写入成品临时文件，单个结果上限90MB，不将整首音频积存在内存。

发送前持久化发送阶段，结果不明时不自动重发，也不自动改为另一种发送方式。插件重载后不补发旧任务；可用查询和取回命令主动操作。重新取回前应先检查QQ里是否已经收到。

## 命令行生成试听

使用可读取的本地歌曲文件，以及干净、单一角色的参考录音。建议先测试15—30秒，参考录音可先用3—10秒的正常讲话。不填`--start`和`--duration`时默认整首，`--start`单独使用时转换到音源末尾。

```powershell
.\runtime\singing\.venv\Scripts\python.exe -X utf8 scripts/sing_cover.py `
  --source "E:\Music\歌曲.mp3" `
  --reference "E:\Voices\ref.wav" `
  --start 90 --duration 24 `
  --pitch-shift 0 12 --steps 40 `
  --output data/singing/cover-sample
```

`--start` 是原曲起点秒数，`--duration` 是转换长度，音源最长60分钟。`--pitch-shift 0 12` 生成保留原音区与高八度两个版本，共用一次人声分离结果。高八度仍与原伴奏保持同一调性，但不一定更像参考角色；高音、气声和男声转女声的效果需要试听判断。

非整八度升降时，FFmpeg的Rubber Band滤镜同步调整伴奏调性并保留时长，避免人声与伴奏错调；0和正负12半音保留原伴奏音区。

每次使用新的输出目录。最终文件为 `si-cover-+0.wav`、`si-cover-+12.wav`，采样率44.1kHz、立体声、16位PCM。目录同时包含原始片段、分离音轨、转换人声、`report.json` 和推理日志，便于定位音源分离、转换或混音环节的问题。

混音按原始分离人声的均方根电平匹配转换音轨，保持伴奏音量关系，修正不超过0.25秒的尾部长度差，并为成品保留峰值余量。缺少模型、GPU占用、异常时长或接近静音的结果会停止生成，不自动重试。

## 验证与限制

针对性检查：

```powershell
.\runtime\singing\.venv\Scripts\python.exe -m unittest discover -s tests -p test_singing.py -v
```

检查覆盖短尾补齐、立体声与采样率、峰值余量、异常音轨拒绝，以及Windows共享锁的争用与异常释放。实际声线还需要用歌曲样片验收，测试通过不代表主观音色达到要求。

零样本转换依赖参考录音，可能残留原唱的唱腔或产生高音、辅音瑕疵。现场版中的混响、和声与观众声音也可能留在分离结果中。音高提取与语音识别可以辅助排查，不能代替对音色和音乐性的试听。

QQ任务在后台处理，不占用短回复TTS的15秒等待额度。自动定位副歌和音乐平台账号登录不在当前功能范围。已验证约5分钟歌曲，60分钟边界并不表示已完成该长度的GPU压力测试；实际耗时及显存仍受设备与音源影响。
