# -*- coding: utf-8 -*-
"""server.py — GPT-SoVITS 常驻合成服务。

模型只在启动时加载一次，之后每次请求只做推理。三个接口：

  POST /say        返回完整 WAV（最简单，兼容性最好）
  POST /say_stream 返回 WAV 分块流（自定义帧：4 字节大端长度 + WAV）
  POST /say_pcm    返回裸 PCM 流（s16le，供 ffplay 等连续播放器吃）

  GET  /health     健康检查

启动：
  <gpt-sovits-package>/runtime/python.exe server.py --config config.json
"""
import argparse
import io
import json
import os
import struct
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {
    "gpt_sovits_dir": "",
    "python_exec": "runtime/python.exe",
    "host": "127.0.0.1",
    "port": 9876,
    "voice": {
        "name": "my-voice",
        "sovits_weights": "",
        "gpt_weights": None,
        "version": "v2Pro",
        "ref_audio": "",
        "ref_text": "",
        "ref_lang": "zh",
    },
}


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULTS))
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    elif os.environ.get("GPT_SOVITS_DIR"):
        cfg["gpt_sovits_dir"] = os.environ["GPT_SOVITS_DIR"]
    if not cfg["gpt_sovits_dir"]:
        sys.exit("缺少 gpt_sovits_dir：请通过 --config 或环境变量 GPT_SOVITS_DIR 指定 GPT-SoVITS 整合包目录")
    return cfg


CFG = None
TTS_PIPE = None
SYNTH_LOCK = threading.Lock()


def load_model():
    """加载 TTS 模型（只做一次）。"""
    global TTS_PIPE
    root = os.path.abspath(CFG["gpt_sovits_dir"])
    os.chdir(root)
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "GPT_SoVITS"))

    import torch
    torch.set_num_threads(os.cpu_count() or 8)
    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

    v = CFG["voice"]
    pretrained_gpt = v.get("gpt_weights") or \
        "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"

    t0 = time.time()
    cfg = TTS_Config({
        "custom": {
            "bert_base_path": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
            "cnhuhbert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
            "device": "cpu",
            "is_half": False,
            "t2s_weights_path": pretrained_gpt,
            "vits_weights_path": v["sovits_weights"],
            "version": v.get("version", "v2Pro"),
        }
    })
    TTS_PIPE = TTS(cfg)
    print("模型加载完成 %.1fs（音色：%s）" % (time.time() - t0, v.get("name", "?")), flush=True)


def _inputs(text, speed=1.0, seed=-1, streaming=False):
    v = CFG["voice"]
    vcfg = TTS_PIPE.configs
    return {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": v["ref_audio"],
        "prompt_text": v["ref_text"],
        "prompt_lang": v.get("ref_lang", "zh"),
        "top_k": 15, "top_p": 1.0, "temperature": 1.0,
        "text_split_method": "cut5", "batch_size": 1,
        "speed_factor": float(speed), "seed": int(seed),
        "parallel_infer": False, "repetition_penalty": 1.35,
        "return_fragment": False,
        "streaming_mode": streaming,
    }


def synth(text, speed=1.0, seed=-1):
    import numpy as np
    import soundfile as sf
    gen = TTS_PIPE.run(_inputs(text, speed, seed, streaming=False))
    item = next(gen)
    sr, audio = item if isinstance(item, tuple) else (32000, item)
    arr = np.asarray(audio)
    if arr.ndim > 1:
        arr = arr.squeeze()
    buf = io.BytesIO()
    sf.write(buf, arr, sr, format="WAV")
    return buf.getvalue(), sr, len(arr) / sr


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_req(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            body = raw.decode("gbk", errors="replace")
        return json.loads(body or "{}")

    def do_GET(self):
        if self.path.startswith("/health"):
            if TTS_PIPE is None:
                return self._json(503, {"ok": False})
            return self._json(200, {
                "ok": True,
                "voice": CFG["voice"].get("name"),
                "port": CFG["port"],
            })
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/say_pcm"):
            return self._stream(pcm=True)
        if self.path.startswith("/say_stream"):
            return self._stream(pcm=False)
        if self.path.startswith("/say"):
            return self._whole()
        self._json(404, {"error": "not found"})

    def _whole(self):
        import numpy as np
        import soundfile as sf
        try:
            req = self._read_req()
            text = (req.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "empty text"})
            t0 = time.time()
            with SYNTH_LOCK:
                wav, sr, dur = synth(text, req.get("speed", 1.0), req.get("seed", -1))
            print("合成 %.1fs 音频 耗时 %.1fs" % (dur, time.time() - t0), flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.send_header("X-Audio-Duration", "%.2f" % dur)
            self.end_headers()
            self.wfile.write(wav)
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": str(e)[:300]})

    def _stream(self, pcm=False):
        import numpy as np
        import soundfile as sf
        try:
            req = self._read_req()
            text = (req.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "empty text"})

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            if not pcm:
                self.send_header("Content-Type", "application/x-wav-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            t0 = time.time()
            first = True
            with SYNTH_LOCK:
                gen = TTS_PIPE.run(_inputs(text, req.get("speed", 1.0), req.get("seed", -1), streaming=True))
                for item in gen:
                    sr, chunk = item if isinstance(item, tuple) else (32000, item)
                    arr = np.asarray(chunk)
                    if arr.ndim > 1:
                        arr = arr.squeeze()
                    if pcm:
                        payload = arr.astype("<i2").tobytes()
                        frame = payload
                    else:
                        buf = io.BytesIO()
                        sf.write(buf, arr, sr, format="WAV")
                        payload = buf.getvalue()
                        frame = struct.pack(">I", len(payload)) + payload
                    if not frame:
                        continue
                    self.wfile.write(("%x\r\n" % len(frame)).encode("ascii"))
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    if first:
                        print("首块 %.2fs" % (time.time() - t0), flush=True)
                        first = False
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            print("流式完成 %.2fs" % (time.time() - t0), flush=True)
        except Exception as e:
            traceback.print_exc()
            try:
                self._json(500, {"error": str(e)[:300]})
            except Exception:
                pass


def main():
    global CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "..", "config.json"))
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    CFG = load_config(args.config)
    if args.host:
        CFG["host"] = args.host
    if args.port:
        CFG["port"] = args.port

    load_model()
    srv = ThreadingHTTPServer((CFG["host"], CFG["port"]), Handler)
    print("服务已启动 http://%s:%d" % (CFG["host"], CFG["port"]), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
