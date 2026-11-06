#!/usr/bin/env node
/**
 * prepare-data.mjs — 把一批音频整理成 GPT-SoVITS 训练用的清单文件。
 *
 * 输入：一个目录（里面是若干音频文件）+ 可选的旁注文本
 * 输出：统一采样率/单声道/音量归一后的 wav + 一个 .list 文件
 *
 * 用法：
 *   node prepare-data.mjs --src ./raw --dst ./prepared --speaker myvoice --lang zh
 *   node prepare-data.mjs --src ./raw --dst ./prepared --speaker myvoice --lang ja --min 1.5 --max 10
 *
 * .list 格式（GPT-SoVITS 要求）：音频路径|说话人|语言|文本
 * 文本从同名 .txt 或 --map 指定的 JSON 里读；没有文本的条目会被跳过。
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

function parseArgs(argv) {
  const o = { min: 1.5, max: 10.5, samplerate: 32000 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i], nx = () => argv[++i];
    if (a === '--src') o.src = nx();
    else if (a === '--dst') o.dst = nx();
    else if (a === '--speaker') o.speaker = nx();
    else if (a === '--lang') o.lang = nx();
    else if (a === '--min') o.min = Number(nx());
    else if (a === '--max') o.max = Number(nx());
    else if (a === '--map') o.map = nx();
    else if (a === '--ffmpeg') o.ffmpeg = nx();
  }
  if (!o.src || !o.dst || !o.speaker) {
    console.error('用法: node prepare-data.mjs --src <原音频目录> --dst <输出目录> --speaker <说话人名> [--lang zh] [--map 文本.json]');
    process.exit(2);
  }
  return o;
}

const o = parseArgs(process.argv.slice(2));
const FFMPEG = o.ffmpeg || process.env.FFMPEG || 'ffmpeg';

function run(args, cwd) {
  return new Promise(res => {
    const p = spawn(FFMPEG, args, { windowsHide: true, cwd });
    let err = '';
    p.stderr.on('data', d => (err += d));
    p.on('close', code => res({ code, err }));
  });
}

function wavDuration(buf) {
  let off = 12, byteRate = 0, dataSize = 0;
  while (off + 8 <= buf.length) {
    const id = buf.toString('ascii', off, off + 4);
    const sz = buf.readUInt32LE(off + 4);
    if (id === 'fmt ') byteRate = buf.readUInt32LE(off + 8 + 8);
    if (id === 'data') { dataSize = sz; break; }
    off += 8 + sz + (sz % 2);
  }
  return byteRate && dataSize ? dataSize / byteRate : 0;
}

async function main() {
  fs.mkdirSync(o.dst, { recursive: true });
  const textMap = o.map && fs.existsSync(o.map) ? JSON.parse(fs.readFileSync(o.map, 'utf8')) : {};

  const files = fs.readdirSync(o.src).filter(f => /\.(wav|mp3|m4a|flac|ogg)$/i.test(f));
  console.log('发现音频', files.length, '个');

  const lines = [];
  let kept = 0, dropped = 0, totalDur = 0;

  for (const f of files) {
    const base = path.basename(f, path.extname(f));
    const txtFile = path.join(o.src, base + '.txt');
    let text = textMap[base] || (fs.existsSync(txtFile) ? fs.readFileSync(txtFile, 'utf8').trim() : '');
    text = text.replace(/[|\r\n]/g, ' ').trim();
    if (!text) { dropped++; continue; }

    const outRel = path.join(path.basename(o.dst), base + '.wav').replace(/\\/g, '/');
    const srcRel = path.relative(path.dirname(o.dst), path.join(o.src, f)).replace(/\\/g, '/');
    const { code } = await run(['-y', '-loglevel', 'error', '-i', srcRel,
      '-ar', String(o.samplerate), '-ac', '1', '-c:a', 'pcm_s16le', outRel],
      path.dirname(o.dst));
    if (code !== 0) { dropped++; continue; }

    const outAbs = path.join(o.dst, base + '.wav');
    let dur = 0;
    try { dur = wavDuration(fs.readFileSync(outAbs)); } catch {}
    if (dur < o.min || dur > o.max) { fs.rmSync(outAbs, { force: true }); dropped++; continue; }

    lines.push(`${outRel}|${o.speaker}|${o.lang}|${text}`);
    kept++; totalDur += dur;
    if (kept % 50 === 0) console.log('处理', kept);
  }

  const listPath = path.join(path.dirname(o.dst), path.basename(o.dst) + '.list');
  fs.writeFileSync(listPath, lines.join('\n') + '\n', 'utf8');
  console.log(`保留 ${kept} 条，丢弃 ${dropped} 条，总时长 ${(totalDur / 60).toFixed(1)} 分钟`);
  console.log('清单:', listPath);
}

main().catch(e => { console.error(e); process.exit(1); });
