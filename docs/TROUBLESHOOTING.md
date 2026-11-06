# 踩坑记录

这份文档记的是实际踩过的坑，不是抄文档。每条都标注了症状和解决办法。

## 1. 推理慢，但不是算力问题

**症状**：CPU 上 5 秒音频要合成 10–20 秒。

**排查**：模型加载只占 2.4 秒，不是主要开销。真正的时间在自回归解码——t2s 模型逐步生成 semantic token，日志里 `0/1500` 那个进度条就是步数。

**结论**：这是模型结构决定的，换硬件帮助有限（见第 2 条）。

## 2. 显卡加速：两条路都试过，都不值得

### DirectML

`pip install torch-directml` 后 7900 GRE 能被识别（`privateuseone:0`），矩阵乘法快 30 倍、卷积快 17 倍。但 GPT-SoVITS 的 t2s 模型用 LSTM，而 DirectML 没有实现 `aten::_thnn_fused_lstm_cell`，一跑就 `NotImplementedError`。

### ROCm (WSL2)

环境能完整搭起来，`rocminfo` 能看到 `gfx1100 / AMD Radeon RX 7900 GRE`，PyTorch 也能用。但实际推理速度和 CPU 持平甚至略慢：

| 音频长度 | CPU | GPU |
|---|---|---|
| 2.0 s | 9.4 s | 13.5 s |
| 7.6 s | 16.8 s | 15.1 s |
| 4.6 s | 9.0 s | 10.6 s |

原因：自回归链是串行的，每步一个很小的前向，GPU 并行用不上，还要付 kernel 启动开销。

**如果仍然要在 WSL 里搭 ROCm**，有两个关键点（文档里没写清楚、靠试出来的）：

1. **必须用官方脚本安装**，不能直接 `apt install rocm`：
   ```bash
   wget https://repo.radeon.com/amdgpu-install/7.2/ubuntu/jammy/amdgpu-install_7.2.70200-1_all.deb
   apt install ./amdgpu-install_7.2.70200-1_all.deb
   amdgpu-install -y --usecase=wsl,rocm --no-dkms
   ```
   直接 apt 装 rocm 包会缺 ROCDXG 驱动桥接，`hsa_init` 失败。

2. **要换掉 PyTorch 自带的 hsa-runtime**。torch 的 ROCm wheel 里那个 `libhsa-runtime64.so` 依赖 `/dev/kfd`，WSL 里没有：
   ```bash
   TL=$(python -c "import torch,os;print(os.path.dirname(torch.__file__))")/lib
   mv $TL/libhsa-runtime64.so $TL/libhsa-runtime64.so.bak
   ln -sfn /opt/rocm/lib/libhsa-runtime64.so.1 $TL/libhsa-runtime64.so
   ```
   不做这一步，`torch.cuda.is_available()` 永远是 False，报 `Cannot load librocdxg.so`。

另外：如果只是要跑本地大模型，Windows 上的 Ollama 已经原生支持 ROCm 了（看 `%LOCALAPPDATA%\Ollama\server.log` 里有没有 `library=ROCm`），不需要 WSL。

## 3. 流式播放的断点

**症状**：分块返回音频，每块用 `SoundPlayer` 播，块与块之间有明显停顿。

**原因**：`SoundPlayer` 只能整文件播放，每换一个文件就有进程启动 + 加载开销（几百毫秒）。

**试过但没解决的方案**：

- **常驻播放器**（一个 PowerShell 进程，stdin 喂文件路径）：单独测试正常，集成到流式流程里会卡死。根因没定位到，怀疑是播放器和合成线程抢 CPU 时的时序问题。
- **WAV 块拼接**：治标不治本，块边界还在。

**最终方案**：服务端直接吐**裸 PCM**，客户端管道喂给 `ffplay`。一次启动，连续输出，真正无缝。

```python
# 服务端：streaming_mode=True 的生成器，每个 chunk 直接 tobytes()
payload = arr.astype("<i2").tobytes()
```

```js
// 客户端：把响应流直接写进 ffplay 的 stdin
const proc = spawn(ffplay, ['-nodisp', '-autoexit', '-f', 's16le', '-ar', '32000', '-ch_layout', 'mono', '-']);
```

**坑**：新版 ffplay 移除了 `-ac` 参数，必须用 `-ch_layout mono`。用 `-ac 1` 会报 `Option not found`，进程秒退，表现为 Node 端 `write EOF`。

## 4. HTTP chunked 编码套两层

自己写长度前缀的同时又声明 `Transfer-Encoding: chunked`，等于套了两层框，Node 报 `HPE_INVALID_CHUNK_SIZE`。要么实现标准 chunked（十六进制长度行 + 数据 + CRLF），要么干脆不用 chunked、靠连接关闭分界。

## 5. 训练相关

### GPT 预训练权重版本要对

`v2` / `v2Pro` 必须配 `gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt`。用 `s1bert25hz-2kh-...` 那个（v1 的）会报：

```
size mismatch for model.ar_text_embedding.word_embeddings.weight:
copying a param with shape torch.Size([512, 512]) from checkpoint,
the shape in current model is torch.Size([732, 512])
```

词表大小不同。

### v2Pro 需要额外提取 SV 特征

`s2_train.py` 里 `assert os.path.exists(self.path7)`，path7 是 `7-sv_cn` 目录。要单独跑 `prepare_datasets/2-get-sv.py`，否则训练直接失败。

### 日语数据微调 GPT 会让中文口吃

SoVITS 学音色，跨语种没问题；但 GPT 学韵律，用日语数据训过之后生成中文会结巴。音色用自己训练的 SoVITS，韵律用预训练中文 GPT。

### 参考音频必须是目标语言

推理时喂日语参考音频，音色还是那个人，但中文会有日式腔调。喂中文参考，调子才对。

## 6. Windows 文件系统坑

- **ffmpeg / soundfile 写 `%TEMP%` 或某些目录会 Permission denied**，但写 cwd 下的相对路径正常。解决办法：让 ffmpeg 用 `cwd + 相对路径`，不要用绝对路径。
- **Git Bash 不认 Windows 反斜杠绝对路径**，bash 脚本里给 Python 传路径要用正斜杠或相对路径。
- **node 调 bash 用 `execFileSync` 比 `spawn` 稳**，spawn 在某些情况下 stdout 收不到内容。
- **中文参数不要走命令行**，Windows 下编码会乱。写临时文件传递，或者走 stdin。

## 7. OpenAI 兼容的 HF Space 有配额

拿公开 Space 做零样本克隆时，ZeroGPU 免费配额是每天 300 秒，每次请求固定扣 60 秒——一天只有 5 次机会。想稳定用要么上 token（免费账号每天 5 分钟），要么本地部署。
