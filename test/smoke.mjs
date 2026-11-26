/**
 * 冒烟测试：确认引擎能跑通一条最短的片子。
 *
 *   node test/smoke.mjs
 *   MG_PYTHON=<python 路径> node test/smoke.mjs
 *
 * 检查项：
 *   1. 找到可用的 Python（Pillow + numpy）
 *   2. mgvideo check 能校验清单
 *   3. 渲染一条 2 秒 2 段的片子，产出 mp4 且有音轨
 *
 * 注意：这里所有子进程输出都走文件重定向，不用管道——受限环境里
 * Node 的 stdio:'pipe' 会 EPERM。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { runCli, findPython } from '../src/python.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mgvideo-smoke-'))

let pass = 0
let fail = 0
function ok(name, cond, detail = '') {
  if (cond) {
    pass++
    console.log(`  ok   ${name}`)
  } else {
    fail++
    console.log(`  FAIL ${name} ${detail}`)
  }
}

const spec = {
  size: [1280, 720],
  fps: 24,
  background: 'grid',
  transition: 'circle',
  transition_duration: 0.25,
  chrome: { brand: 'smoke', chapter: true },
  audio: { bgm: true },
  scenes: [
    { template: 'title', duration: 1.0, transition: 'none', title: '冒烟', subtitle: '测试' },
    { template: 'bullets', duration: 1.0, items: ['第一条', '第二条'] },
  ],
}
const specPath = path.join(tmp, 'spec.json')
fs.writeFileSync(specPath, JSON.stringify(spec, null, 2))

console.log('mgvideo smoke\n')

// 0. 仓库自身的基本卫生：package.json 不能带 BOM（带了 JSON.parse 直接失败）
{
  const pkgPath = path.join(ROOT, 'package.json')
  const buf = fs.readFileSync(pkgPath)
  const hasBom = buf.length >= 3 && buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf
  ok('package.json 无 BOM', !hasBom, hasBom ? 'UTF-8 BOM 会让 JSON.parse 失败' : '')
  let parsed = null
  try { parsed = JSON.parse(buf.toString('utf8').replace(/^\uFEFF/, '')) } catch {}
  ok('package.json 可解析且声明 dsh.bundle', !!(parsed && parsed.dsh && parsed.dsh.bundle), '')
}

try {
  const bin = await findPython()
  ok('python 可用且有 Pillow/numpy', !!bin, bin)
} catch (e) {
  ok('python 可用且有 Pillow/numpy', false, e.message)
  console.log(`\n${pass} passed, ${fail} failed`)
  process.exit(1)
}

const check = await runCli(['check', specPath])
ok('check 通过', check.code === 0 && /total\s*:/.test(check.out), check.out.slice(0, 400))

const self = await runCli(['selftest'])
ok('selftest 全过', self.code === 0 && !/FAIL/.test(self.out), self.out.split('\n').slice(-3).join(' | '))

const out = path.join(tmp, 'smoke.mp4')
const r = await runCli(['render', specPath, '-o', out, '--workers', '1'])
ok('render 成功', fs.existsSync(out) && fs.statSync(out).size > 2000, r.out.slice(0, 600))

if (fs.existsSync(out)) {
  const p = await runCli(['probe', out])
  let info = null
  try { info = JSON.parse(p.out) } catch {}
  if (!info) {
    console.log('  skip  ffprobe 不可用，跳过容器检查')
  } else {
    ok('时长 ≈ 2.0s', Math.abs((info.duration_s ?? -1) - 2.0) < 0.15, `duration_s=${info.duration_s}`)
    ok('有视频轨', Array.isArray(info.video) ? info.video.length > 0 : /Video:/.test(String(info.video)))
    ok('有音轨', Array.isArray(info.audio) ? info.audio.length > 0 : /Audio:/.test(String(info.audio)))
  }
}

console.log(`\n${pass} passed, ${fail} failed`)
console.log(`临时目录: ${tmp}`)
process.exit(fail ? 1 : 0)
