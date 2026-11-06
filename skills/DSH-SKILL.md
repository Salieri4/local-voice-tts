---
name: local-voice-tts
description: 用本机训练好的克隆音色说话。当需要「发声」「朗读」「用我的声音说一句话」时使用。走本地常驻 GPT-SoVITS 服务（端口 9876），不联网、无限次、首声约 5 秒且无缝。
---

# 本地克隆音色发声

把这台机器上训练好的音色模型当语音输出用。不是微软 TTS，是自己训练的克隆音色。

## 什么时候用

- 用户要求「说出来」「读一遍」「用我的声音」
- 需要语音反馈而不是纯文字
- 要给用户听一段音频（配合作品交付）

## 命令

默认引擎是 edge（微软在线语音，2 秒出结果，无需服务）。要**克隆音色**必须加 `--engine local`：

```bash
# 基本用法（克隆音色，流式播放，首声约 5 秒）
node "D:\deepseek workroom\tools\voice\say.mjs" "要说的文本" --engine local

# 调语速
node "D:\deepseek workroom\tools\voice\say.mjs" "文本" --engine local --speed 1.1

# 只出文件不出声
node "D:\deepseek workroom\tools\voice\say.mjs" "文本" --engine local --out D:\path\a.wav --no-play

# 播放已有音频
node "D:\deepseek workroom\tools\voice\say.mjs" --play-file D:\path\a.wav

# 快速模式（晓伊音色，不加载本地模型）
node "D:\deepseek workroom\tools\voice\say.mjs" "文本"
```

## 依赖的服务

克隆音色依赖常驻服务（端口 9876），由 Startup 里的 `tts-startup.vbs` 开机自启。

检查是否在跑：

```bash
curl -s --max-time 5 http://127.0.0.1:9876/health
# 正常返回 {"ok": true, "voice": "...", ...}
```

如果没跑，手动起：

```bash
cd "D:\deepseek workroom\tools\gpt-sovits\GPT-SoVITS-v2pro-20250604"
.\runtime\python.exe server.py --port 9876
```

服务没起时 `--engine local` 会自动退回一次性调用，能出声但慢几秒。

## 技术要点（改代码前必读）

- **模型组合**：SoVITS（音色，用日语 VN 原声训练）+ **预训练中文 GPT**（韵律）。不要换成微调过的 GPT——日语数据训过的 GPT 会让中文口吃。
- **参考音频必须是中文**：`data_kurisu/ref_zh.wav`。用日语参考会让中文带日式腔调。
- **流式播放靠 ffplay**：服务端 `/say_pcm` 吐裸 PCM，ffplay 从 stdin 连续吃。新版 ffplay 没有 `-ac` 参数，必须用 `-ch_layout mono`。
- **GPU 加速没用**：瓶颈是自回归解码不是算力，实测 ROCm 和 CPU 持平。别再试。

完整原理和踩坑见 `D:\deepseek workroom\projects\local-voice-tts\docs\TROUBLESHOOTING.md`。

## 如果用户要改音色或换声音

训练流程在 `D:\deepseek workroom\tools\gpt-sovits\`：

1. `fetch-dataset.mjs` 下载素材
2. `prepare-data.mjs` 预处理（重采样、过滤、生成 .list）
3. `run-train.py --prepare --only s2` 训练音色（CPU 上 10 分钟素材约 40-60 分钟）
4. 改 `server.py` 里的 `vits_weights_path` 指向新权重，重启服务

训练需要素材权利清晰。不要用未授权的版权声音训练后对外分发。

## 注意

- 生成一句话通常要 10–25 秒，不是即时反馈。用户催的时候直接说还要多久。
- 服务冷启动约 15 秒加载模型。
- 公开项目在 https://github.com/Salieri4/local-voice-tts
