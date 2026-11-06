#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run-train.py — 命令行驱动 GPT-SoVITS 的数据预处理与训练。

在 GPT-SoVITS 整合包根目录放一份本文件，然后：

    runtime\\python.exe run-train.py --list data/mylist.list --exp myvoice --prepare
    runtime\\python.exe run-train.py --exp myvoice --only s2 --epochs 8
    runtime\\python.exe run-train.py --exp myvoice --only s1 --epochs 8

注意：
- GPT 模型必须配 v2/v2Pro 对应的 5kh 预训练权重，用 2kh 那个会因词表大小不匹配而失败。
- 用日语素材训练 GPT（t2s）会让中文合成口吃。音色用 SoVITS 学，韵律交给预训练中文 GPT。
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
os.chdir(ROOT)

PRETRAINED_GPT = "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
PRETRAINED_SOVITS = "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth"
PRETRAINED_SOVITS_D = "GPT_SoVITS/pretrained_models/v2Pro/s2Dv2Pro.pth"
SV_MODEL = "GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt"
BERT_DIR = "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"
HUBERT_DIR = "GPT_SoVITS/pretrained_models/chinese-hubert-base"


def env(**kw):
    e = os.environ.copy()
    e["PYTHONPATH"] = os.path.join(ROOT, "GPT_SoVITS") + os.pathsep + e.get("PYTHONPATH", "")
    hf = os.path.join(ROOT, "TEMP", "hf_home")
    os.makedirs(hf, exist_ok=True)
    e["HF_HOME"] = hf
    e["TORCH_HOME"] = os.path.join(hf, "torch")
    e["PYTHONIOENCODING"] = "utf-8"
    e.update({k: str(v) for k, v in kw.items()})
    return e


def run(cmd, extra=None, label=""):
    print(f"\n===== {label or cmd} =====", flush=True)
    t0 = time.time()
    p = subprocess.run(cmd, shell=True, env=extra or os.environ.copy())
    if p.returncode != 0:
        sys.exit(f"[FAIL] {label or cmd} -> {p.returncode}")
    print(f"[OK] {label} {time.time() - t0:.1f}s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default="", help="训练清单（.list）相对路径")
    ap.add_argument("--exp", default="myvoice", help="实验名")
    ap.add_argument("--version", default="v2Pro")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--prepare", action="store_true", help="跑数据预处理")
    ap.add_argument("--only", default="", help="s2 | s1 | 空=两个都跑")
    args = ap.parse_args()

    exp = args.exp
    opt_dir = f"logs/{exp}"
    os.makedirs(opt_dir, exist_ok=True)

    base = dict(exp_name=exp, opt_dir=opt_dir, i_part="0", all_parts="1",
                _CUDA_VISIBLE_DEVICES="0", is_half="False")

    if args.prepare:
        if not args.list:
            sys.exit("--prepare 需要 --list")
        wav_dir = os.path.dirname(args.list)
        base.update(inp_text=args.list, inp_wav_dir=wav_dir)

        run(f'"{PY}" -s GPT_SoVITS/prepare_datasets/1-get-text.py',
            env(version=args.version, bert_pretrained_dir=BERT_DIR, **base), "1-get-text")

        for src, dst in [(f"{opt_dir}/2-name2text-0.txt", f"{opt_dir}/2-name2text.txt")]:
            if os.path.exists(src):
                with open(src, encoding="utf8") as f:
                    lines = f.read().strip("\n").split("\n")
                with open(dst, "w", encoding="utf8") as f:
                    f.write("\n".join(lines) + "\n")
                os.remove(src)

        run(f'"{PY}" -s GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py',
            env(cnhubert_base_dir=HUBERT_DIR, sv_path=SV_MODEL, **base), "2-get-hubert")

        if args.version in ("v2Pro", "v2ProPlus"):
            run(f'"{PY}" -s GPT_SoVITS/prepare_datasets/2-get-sv.py',
                env(sv_path=SV_MODEL, **base), "2-get-sv")

        run(f'"{PY}" -s GPT_SoVITS/prepare_datasets/3-get-semantic.py',
            env(pretrained_s2G=PRETRAINED_SOVITS,
                s2config_path=f"GPT_SoVITS/configs/s2{args.version}.json", **base),
            "3-get-semantic")

        src = f"{opt_dir}/6-name2semantic-0.tsv"
        dst = f"{opt_dir}/6-name2semantic.tsv"
        if os.path.exists(src):
            with open(src, encoding="utf8") as f:
                lines = ["item_name\tsemantic_audio"] + f.read().strip("\n").split("\n")
            with open(dst, "w", encoding="utf8") as f:
                f.write("\n".join(lines) + "\n")
            os.remove(src)

    only = args.only

    if only in ("", "s2"):
        with open(f"GPT_SoVITS/configs/s2{args.version}.json", encoding="utf8") as f:
            data = json.load(f)
        os.makedirs(f"{opt_dir}/logs_s2_{args.version}", exist_ok=True)
        data["train"].update(
            fp16_run=False, batch_size=args.batch, epochs=args.epochs,
            text_low_lr_rate=0.4,
            pretrained_s2G=PRETRAINED_SOVITS, pretrained_s2D=PRETRAINED_SOVITS_D,
            if_save_latest=True, if_save_every_weights=True,
            save_every_epoch=2, gpu_numbers="0", grad_ckpt=False, lora_rank="32",
        )
        data["model"]["version"] = args.version
        data["data"]["exp_dir"] = data["s2_ckpt_dir"] = opt_dir
        data["save_weight_dir"] = f"SoVITS_weights_{args.version}"
        data["name"] = exp
        data["version"] = args.version
        os.makedirs("TEMP", exist_ok=True)
        cfg = "TEMP/tmp_s2.json"
        with open(cfg, "w", encoding="utf8") as f:
            json.dump(data, f)
        run(f'"{PY}" -s GPT_SoVITS/s2_train.py --config "{cfg}"', label="s2_train(SoVITS)")

    if only in ("", "s1"):
        import yaml
        with open("GPT_SoVITS/configs/s1longer-v2.yaml", encoding="utf8") as f:
            s1 = yaml.load(f, Loader=yaml.FullLoader)
        os.makedirs(f"{opt_dir}/logs_s1", exist_ok=True)
        s1["train"].update(
            precision="32", batch_size=args.batch, epochs=args.epochs,
            save_every_n_epoch=2, if_save_every_weights=True,
            if_save_latest=True, if_dpo=False,
            half_weights_save_dir="GPT_weights_v2Pro", exp_name=exp,
        )
        s1["pretrained_s1"] = PRETRAINED_GPT
        s1["train_semantic_path"] = f"{opt_dir}/6-name2semantic.tsv"
        s1["train_phoneme_path"] = f"{opt_dir}/2-name2text.txt"
        s1["output_dir"] = f"{opt_dir}/logs_s1_{args.version}"
        os.makedirs("TEMP", exist_ok=True)
        cfg1 = "TEMP/tmp_s1.yaml"
        with open(cfg1, "w", encoding="utf8") as f:
            yaml.dump(s1, f, default_flow_style=False, allow_unicode=True)
        run(f'"{PY}" -s GPT_SoVITS/s1_train.py --config_file "{cfg1}"', label="s1_train(GPT)")

    print("\n完成。权重在 SoVITS_weights_* / GPT_weights_* 目录。", flush=True)


if __name__ == "__main__":
    main()
