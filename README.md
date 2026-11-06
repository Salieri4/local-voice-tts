# local-voice-tts

一套把 GPT-SoVITS 变成「随时可用的本地语音服务」的封装：常驻进程只加载一次模型，客户端一行命令出声，**流式播放首声约 5 秒且无缝**。

不是又一个 WebUI。目标是把声音变成命令行里可以随手调用的东西。

```
node client/say.mjs "这句话用你自己的音色说出来" --engine local
```

## 为什么做这个

GPT-SoVITS 本身很强，但从「能跑」到「日常能用」隔着几道坎：

| 问题 | 原生情况 | 这里怎么解决 |
|---|---|---|
| 每次调用都重新加载模型 | `infer.py` 单次调用，加载占 2–20 秒 | 常驻 HTTP 服务，模型只加载一次 |
| 整段合成完才出声 | 5 秒音频要等 10–20 秒 | 服务端流式 + ffplay 连续播，首声 5 秒 |
| 播放器切换导致断点 | `SoundPlayer` 逐文件播必然有停顿 | 裸 PCM 管道喂 ffplay，一次启动连续输出 |
| 训练流程散落在 WebUI 里 | 只能点鼠标 | `run-train.py` 命令行驱动，可脚本化 |

## 架构

```
┌──────────────┐   POST /say_pcm    ┌─────────────────┐
│ client/      │ ─────────────────► │ server/server.py│
│  say.mjs     │   裸 PCM 流        │  常驻进程        │
│              │ ◄───────────────── │  GPT-SoVITS     │
│  ffplay ◄────┼── 管道             │  模型只载一次    │
└──────────────┘                    └─────────────────┘
```

三个接口，按需选：

- `POST /say` — 完整 WAV，最兼容
- `POST /say_stream` — WAV 分块流（4 字节长度前缀）
- `POST /say_pcm` — 裸 PCM 流（s16le），给 ffplay 这类连续播放器

还有 `GET /health`。

## 需要什么

- Windows（播放走 ffplay + PowerShell，Linux 需自行替换播放层）
- [GPT-SoVITS 整合包](https://github.com/RVC-Boss/GPT-SoVITS)（自带 Python 运行时，不用另装）
- Node.js 18+
- ffplay（[FFmpeg 构建包](https://github.com/BtbN/FFmpeg-Builds/releases)里的 `ffplay.exe`）

## 快速开始

### 1. 配置

```bash
cp config.example.json config.json
```

改 `config.json` 里的 `gpt_sovits_dir` 指向你的整合包目录，以及 `voice` 段的权重和参考音频路径。

### 2. 启动服务

```bash
"<整合包目录>/runtime/python.exe" server/server.py --config config.json
```

看到「服务已启动」就好了。首次加载约 10–20 秒。

### 3. 说话

```bash
node client/say.mjs "测试一下" --engine local
```

### 4. 开机自启（可选）

```powershell
powershell -ExecutionPolicy Bypass -File client\install-autostart.ps1
```

写入当前用户的 Startup 文件夹，不需要管理员权限。

## 训练自己的音色

前提：你**有权利使用**这些声音素材。见下方版权说明。

### 数据准备

素材要求：干净人声、无背景音乐、单条 2–10 秒、建议总量 10 分钟以上。每条音频配一份同名 `.txt`，内容是音频里的原话。

```bash
node train/prepare-data.mjs --src ./raw --dst ./data/mine --speaker myname --lang zh
```

产出统一到 32kHz 单声道的 wav 和一份 `.list` 清单。

### 训练

把 `train/run-train.py` 放进整合包根目录：

```bash
cd <整合包目录>
runtime\python.exe run-train.py --list data/mine.list --exp myname --prepare
runtime\python.exe run-train.py --exp myname --only s2 --epochs 8
```

CPU 上大约 10 分钟音频对应 8 轮训练需要 40–60 分钟。

### 两个关键取舍

**音色靠 SoVITS，韵律靠预训练 GPT。**

用日语素材训练 SoVITS 完全没问题，跨语种迁移效果很好。但**不要用日语素材微调 GPT（t2s）模型**——它负责中文的韵律生成，日语数据训过之后中文会口吃。用整合包自带的预训练中文 GPT 即可。

**参考音频必须和目标语言一致。**

推理时喂一句中文参考，GPT 才会按中文的调子生成。用日语参考，音色还是那个人，但中文会带日式腔调。

## 性能参考

测试环境：Ryzen 5 7500F（12 线程）、32 GB 内存、无显卡加速。

| 场景 | 耗时 |
|---|---|
| 模型加载（仅启动时一次） | 约 15 秒 |
| 短句（约 3 秒音频） | 首声 4–5 秒，总共 9 秒 |
| 长句（约 8 秒音频） | 首声 5–7 秒，总共 16–20 秒 |
| 实时率 | 约 2–2.5× |

## 关于显卡加速

试过了，结论是不值得：

- **DirectML**：LSTM 算子没实现，GPT-SoVITS 的 t2s 模型直接崩
- **ROCm (WSL2)**：环境能搭起来，`rocminfo` 也能认卡，但推理速度和 CPU 持平

原因在模型结构：瓶颈是自回归解码（逐步吐 semantic token），每步只有很小的前向计算，GPU 的并行能力用不上，反而多付 kernel 启动开销。纯算子层面 GPU 确实快 17–40 倍，但 TTS 的实际负载不是那个形状。

详细过程记在 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

## 目录

```
server/server.py          常驻合成服务
client/say.mjs            命令行客户端（edge + local 双引擎）
client/install-autostart.ps1  开机自启安装
train/prepare-data.mjs    训练数据预处理
train/run-train.py        训练驱动
docs/TROUBLESHOOTING.md   踩坑记录
```

## 版权说明

这个项目只包含**工具代码**，不包含任何语音模型权重、训练数据或角色素材。

用本工具训练音色时，你需要确保对所用音频素材拥有合法权利。用受版权保护的声音（包括动画、游戏中的角色配音）训练并分发模型，很可能构成侵权。个人自用和公开分发是两回事。

## License

MIT
