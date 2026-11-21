# 本地翻唱试听

这是一套独立的短片段歌声转换工具：输入现有歌曲和角色参考录音，经过人声分离、音色转换和混音，生成带伴奏的试听文件。当前用于验证音色与咬字，尚未接入 QQ 点歌命令。

普通 TTS 负责从文字合成讲话。翻唱使用原曲人声提供旋律、时值与演唱方式，由 Seed-VC 转换音色；祀音的 GGUF 权重不直接参与歌声转换。

## 组件与资源

- [Seed-VC](https://github.com/Plachtaa/seed-vc)：源码固定到 `51383efd921027683c89e5348211d93ff12ac2a8`，使用 v1 的44.1kHz歌声转换模型。上游已归档，后续兼容维护需要自行承担。
- [Demucs 4.0.1](https://pypi.org/project/demucs/4.0.1/)：使用 `htdemucs` 分离人声与伴奏。
- Python 3.10、PyTorch 2.4.0 / CUDA 12.4、NVIDIA GPU，以及基础项目已有的 Windows FFmpeg。
- 独立依赖见 `requirements-singing.txt`。运行环境和模型保存在被 Git 忽略的 `runtime/singing/`，音频和生成记录保存在 `data/singing/`。

推理按需启动子进程，完成后退出，释放该进程占用的显存。Windows 上复用祀音的 `Local\SiVoiceInference` 互斥锁，避免同时执行 GPU 推理；已在合成的祀音会使翻唱立即报忙。翻唱运行期间，新语音请求可能等待，QQ 短回复仍按原有15秒上限回退为文字。

8GB显存电脑已在祀音运行时完成22.5秒的双版本试听，但转换期间曾观测到整卡占用约7.4GB，余量较小。游戏等其他GPU负载可能使推理失败，不适合作为常驻模型并行运行。

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

## 生成试听

使用可读取的本地歌曲文件，以及干净、单一角色的参考录音。建议先测试15—30秒，参考录音可先用3—10秒的正常讲话。

```powershell
.\runtime\singing\.venv\Scripts\python.exe -X utf8 scripts/sing_cover.py `
  --source "E:\Music\歌曲.mp3" `
  --reference "E:\Voices\ref.wav" `
  --start 90 --duration 24 `
  --pitch-shift 0 12 --steps 40 `
  --output data/singing/cover-sample
```

`--start` 是原曲起点秒数，`--duration` 最多30秒。`--pitch-shift 0 12` 生成保留原音区与高八度两个版本，共用一次人声分离结果。高八度仍与原伴奏保持同一调性，但不一定更像参考角色；高音、气声和男声转女声的效果需要试听判断。

每次使用新的输出目录。最终文件为 `si-cover-+0.wav`、`si-cover-+12.wav`，采样率44.1kHz、立体声、16位PCM。目录同时包含原始片段、分离音轨、转换人声、`report.json` 和推理日志，便于定位音源分离、转换或混音环节的问题。

混音按原始分离人声的均方根电平匹配转换音轨，保持伴奏音量关系，修正不超过0.25秒的尾部长度差，并为成品保留峰值余量。缺少模型、GPU占用、异常时长或接近静音的结果会停止生成，不自动重试。

## 验证与限制

针对性检查：

```powershell
.\runtime\singing\.venv\Scripts\python.exe -m unittest discover -s tests -p test_singing.py -v
```

检查覆盖短尾补齐、立体声与采样率、峰值余量、异常音轨拒绝，以及Windows共享锁的争用与异常释放。实际声线还需要用歌曲样片验收，测试通过不代表主观音色达到要求。

零样本转换依赖参考录音，可能残留原唱的唱腔或产生高音、辅音瑕疵。现场版中的混响、和声与观众声音也可能留在分离结果中。音高提取与语音识别可以辅助排查，不能代替对音色和音乐性的试听。

QQ 点歌仍需在样片音色确认后接入独立任务流程；当前短回复 TTS 的15秒等待上限不适合整首歌曲。这里生成的文件可先在本机试听。
