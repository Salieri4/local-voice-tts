#!/usr/bin/env node
/**
 * say.mjs — 让本机用自然语音说话。
 *
 * 两个引擎：
 *   edge  —— 微软 Edge 在线神经语音，秒级出结果，需要联网（默认）
 *   local —— 自己训练的音色，走本地常驻服务，无限次，不联网
 *
 * 用法：
 *   node say.mjs "文本"
 *   node say.mjs "文本" --engine local
 *   node say.mjs "文本" --engine local --speed 1.1
 *   node say.mjs "文本" --out a.wav --no-play
 *   node say.mjs --play-file a.wav
 *   echo "文本" | node say.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HOME = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HOME, '..');
const TMP = path.join(HOME, '.tmp');
fs.mkdirSync(TMP, { recursive: true });

const EDGE_PRESETS = {
  default: { voice: 'zh-CN-XiaoyiNeural', rate: '+6%', pitch: '+0Hz' },
  warm: { voice: 'zh-CN-XiaoxiaoNeural', rate: '+6%', pitch: '+0Hz' },
  bright: { voice: 'zh-CN-XiaoxiaoNeural', rate: '+14%', pitch: '+3Hz' },
  male: { voice: 'zh-CN-YunxiNeural', rate: '+6%', pitch: '+0Hz' },
};

function loadConfig() {
  const p = process.env.LVT_CONFIG || path.join(ROOT, 'config.json');
  let cfg = {};
  if (fs.existsSync(p)) cfg = JSON.parse(fs.readFileSync(p, 'utf8'));
  return {
    host: cfg.host || '127.0.0.1',
    port: cfg.port || 9876,
    edge: cfg.edge || { enabled: true, voice: 'zh-CN-XiaoyiNeural', rate: '+6%' },
    playback: cfg.playback || {},
  };
}

function parseArgs(argv) {
  const pi = argv.findIndex(a => a === '--preset' || a === '-P');
  const preset = pi >= 0 && EDGE_PRESETS[argv[pi + 1]] ? EDGE_PRESETS[argv[pi + 1]] : EDGE_PRESETS.default;
  const o = { ...preset, text: [], play: true, stream: true, engine: 'edge', out: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const nx = () => argv[++i];
    if (a === '--engine' || a === '-E') o.engine = nx();
    else if (a === '--preset' || a === '-P') o.preset = nx();
    else if (a === '--voice' || a === '-v') o.voice = nx();
    else if (a === '--rate' || a === '-r') o.rate = nx();
    else if (a === '--pitch' || a === '-p') o.pitch = nx();
    else if (a === '--speed') o.speed = Number(nx());
    else if (a === '--out' || a === '-o') o.out = nx();
    else if (a === '--play-file') o.playFile = nx();
    else if (a === '--no-play') o.play = false;
    else if (a === '--no-stream') o.stream = false;
    else if (a.startsWith('-')) throw new Error('未知参数: ' + a);
    else o.text.push(a);
  }
  return o;
}

function readStdin() {
  return new Promise(res => {
    if (process.stdin.isTTY) return res('');
    let s = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', d => (s += d));
    process.stdin.on('end', () => res(s));
  });
}

// ---------- 播放 ----------

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], ...opts });
    let err = '';
    p.stderr.on('data', d => (err += d));
    p.on('error', reject);
    p.on('close', code => (code === 0 ? resolve() : reject(new Error(cmd + ' 失败(' + code + '): ' + err.trim().slice(-300)))));
  });
}

function findFfplay(cfg) {
  const cands = [
    cfg.playback?.ffplay_path && path.resolve(ROOT, cfg.playback.ffplay_path),
    path.join(ROOT, 'ffmpeg-master-latest-win64-lgpl-shared', 'bin', 'ffplay.exe'),
    'ffplay',
  ].filter(Boolean);
  for (const c of cands) {
    if (c === 'ffplay') return c;
    if (fs.existsSync(c)) return c;
  }
  return null;
}

function playWav(file) {
  return new Promise((resolve, reject) => {
    const ps = "$p = New-Object System.Media.SoundPlayer('" +
      file.replace(/'/g, "''") + "'); $p.Load(); $p.PlaySync(); $p.Dispose()";
    const p = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
      { windowsHide: true, stdio: 'ignore' });
    p.on('error', reject);
    p.on('close', resolve);
  });
}

// ---------- 引擎：edge ----------

async function sayEdge(text, o) {
  const require2 = createRequire(import.meta.url);
  const { MsEdgeTTS, OUTPUT_FORMAT } = require2('msedge-tts');
  const tts = new MsEdgeTTS({ connectionTimeoutMs: 15000 });
  await tts.setMetadata(o.voice, OUTPUT_FORMAT.AUDIO_24KHZ_48KBITRATE_MONO_MP3);
  const dir = path.join(TMP, 'edge-' + Date.now());
  fs.mkdirSync(dir, { recursive: true });
  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const ssml = `<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-CN">
  <voice name="${o.voice}"><prosody rate="${o.rate}" pitch="${o.pitch}">${esc(text)}</prosody></voice></speak>`;
  let res;
  try {
    res = await tts.rawToFile(dir, ssml);
  } finally {
    tts.close();
  }
  const file = res.audioFilePath;
  if (o.out) { fs.copyFileSync(file, o.out); console.log(path.resolve(o.out)); }
  else console.log(file);
  if (o.play) await playWav(file);
  return file;
}

// ---------- 引擎：local ----------

async function sayLocalPcm(text, speed, cfg) {
  const res = await fetch(`http://${cfg.host}:${cfg.port}/say_pcm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, speed }),
    signal: AbortSignal.timeout(20 * 60 * 1000),
  });
  if (!res.ok || !res.body) throw new Error('服务返回 ' + res.status);

  const ffplay = findFfplay(cfg);
  if (!ffplay) throw new Error('找不到 ffplay，无法流式播放');

  const sr = cfg.playback?.sample_rate || 32000;
  const proc = spawn(ffplay,
    ['-nodisp', '-autoexit', '-loglevel', 'quiet', '-f', 's16le', '-ar', String(sr), '-ch_layout', 'mono', '-'],
    { windowsHide: true, stdio: ['pipe', 'ignore', 'ignore'] });
  const closed = new Promise(r => proc.on('close', r));
  const reader = res.body.getReader();
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const buf = Buffer.from(value);
      bytes += buf.length;
      if (!proc.stdin.write(buf)) await new Promise(r => proc.stdin.once('drain', r));
    }
  } finally {
    try { proc.stdin.end(); } catch {}
  }
  await closed;
  return bytes;
}

async function sayLocalWhole(text, speed, o, cfg) {
  const res = await fetch(`http://${cfg.host}:${cfg.port}/say`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, speed }),
    signal: AbortSignal.timeout(20 * 60 * 1000),
  });
  if (!res.ok) throw new Error('服务返回 ' + res.status);
  const buf = Buffer.from(await res.arrayBuffer());
  const file = path.join(TMP, 'local-' + Date.now() + '.wav');
  fs.writeFileSync(file, buf);
  if (o.out) { fs.copyFileSync(file, o.out); console.log(path.resolve(o.out)); }
  else console.log(file);
  if (o.play) await playWav(file);
  return file;
}

// ---------- main ----------

async function main() {
  const o = parseArgs(process.argv.slice(2));
  const cfg = loadConfig();

  if (o.playFile) {
    const f = path.resolve(o.playFile);
    const ffplay = findFfplay(cfg);
    if (ffplay && /\.(mp3|m4a|wav|flac|ogg)$/i.test(f)) {
      // 用 ffplay 播，避免额外转码
      await run(ffplay, ['-nodisp', '-autoexit', '-loglevel', 'quiet', f]);
    } else {
      await playWav(f);
    }
    return;
  }

  let text = o.text.join(' ').trim();
  if (!text) text = (await readStdin()).trim();
  if (!text) { console.error('没有要说的文本'); process.exit(2); }

  if (o.engine === 'local') {
    let done = false;
    if (o.stream && o.play) {
      try {
        const bytes = await sayLocalPcm(text, o.speed || 1.0, cfg);
        if (bytes > 0) { console.log('[pcm] ' + bytes + ' bytes'); done = true; }
      } catch (e) {
        // 流式不可用，退回整段
      }
    }
    if (!done) await sayLocalWhole(text, o.speed || 1.0, o, cfg);
    return;
  }

  await sayEdge(text, o);
}

main().catch(e => { console.error('出错: ' + (e && e.message || e)); process.exit(1); });
