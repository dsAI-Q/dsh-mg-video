/**
 * 找 Python、跑 mgvideo CLI。
 *
 * 全程不用管道：stdout/stderr 直接重定向到临时文件再读回来。
 * 原因：受限环境（沙箱）里禁止子进程打开命名管道，spawn 的默认
 * stdio:'pipe' 会 EPERM。写文件这条路在所有环境都能用。
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
export const ENGINE_DIR = path.resolve(HERE, '..', 'engine')

const NEED = 'import PIL, numpy'

function candidates() {
  const env = process.env.MG_PYTHON || process.env.DSH_MG_PYTHON
  const list = []
  if (env) list.push(env)
  if (process.platform === 'win32') {
    list.push(path.join(ENGINE_DIR, '.venv', 'Scripts', 'python.exe'))
    list.push('python')
    list.push('py')
  } else {
    list.push(path.join(ENGINE_DIR, '.venv', 'bin', 'python'))
    list.push('python3')
    list.push('python')
  }
  return list
}

let cached = null

function probe(bin) {
  return new Promise((resolve) => {
    const tmp = path.join(os.tmpdir(), `mgprobe-${Date.now()}-${Math.random().toString(36).slice(2)}.log`)
    let fd
    try {
      fd = fs.openSync(tmp, 'w')
    } catch {
      return resolve(null)
    }
    let child
    try {
      child = spawn(bin, ['-c', NEED], { stdio: ['ignore', fd, fd] })
    } catch {
      fs.closeSync(fd)
      return resolve(null)
    }
    child.on('error', () => {
      try { fs.closeSync(fd) } catch {}
      try { fs.unlinkSync(tmp) } catch {}
      resolve(null)
    })
    child.on('exit', (code) => {
      try { fs.closeSync(fd) } catch {}
      let text = ''
      try { text = fs.readFileSync(tmp, 'utf8') } catch {}
      try { fs.unlinkSync(tmp) } catch {}
      resolve(code === 0 ? { bin, text } : null)
    })
  })
}

/** 返回可用的 Python（必须在 sys.path 里能 import PIL + numpy） */
export async function findPython() {
  if (cached) return cached
  for (const bin of candidates()) {
    const ok = await probe(bin)
    if (ok) {
      cached = ok.bin
      return cached
    }
  }
  throw new Error(
    'mgvideo 需要 Python 3.9+ 且装好 Pillow 和 numpy。\n' +
      '装法： python -m pip install pillow numpy\n' +
      '如果 Python 在别处，设环境变量 MG_PYTHON 指向它。',
  )
}

/** 跑 `python -m mgvideo ...`，返回 { code, out } */
export async function runCli(args, { cwd = ENGINE_DIR, env = {} } = {}) {
  const bin = await findPython()
  const tmp = path.join(os.tmpdir(), `mgvideo-${Date.now()}-${Math.random().toString(36).slice(2)}.log`)
  const fd = fs.openSync(tmp, 'w')
  const merged = { ...process.env, ...env, PYTHONPATH: cwd, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }
  const child = spawn(bin, ['-m', 'mgvideo', ...args], { cwd, env: merged, stdio: ['ignore', fd, fd] })
  const code = await new Promise((resolve) => {
    child.on('error', () => resolve(-1))
    child.on('exit', (c) => resolve(c ?? -1))
  })
  try { fs.closeSync(fd) } catch {}
  let out = ''
  try { out = fs.readFileSync(tmp, 'utf8') } catch {}
  try { fs.unlinkSync(tmp) } catch {}
  return { code, out: out.trim() }
}

export function ffmpegHint() {
  return (
    'mgvideo 还需要 ffmpeg：装好后确保在 PATH 里，或设环境变量 MG_FFMPEG 指向可执行文件。'
  )
}
