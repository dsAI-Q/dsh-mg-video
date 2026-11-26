#!/usr/bin/env node
/**
 * mgvideo 命令行：把参数原样交给 Python 引擎。
 *
 *   mgvideo render spec.json -o out.mp4 [--workers N] [--shard i/n]
 *   mgvideo check spec.json
 *   mgvideo frames video.mp4 --duration 20 --out frames/
 *   mgvideo calib video.mp4 12.5 --rect 470,285,515,168
 *   mgvideo probe video.mp4
 *   mgvideo doctor        检查 Python / Pillow / numpy / ffmpeg 是否齐
 */
import { runCli, findPython, ENGINE_DIR, ffmpegHint } from './python.mjs'

const argv = process.argv.slice(2)

async function doctor() {
  console.log('engine dir :', ENGINE_DIR)
  try {
    const bin = await findPython()
    console.log('python     :', bin)
  } catch (e) {
    console.log('python     : 不可用 —', e.message)
    process.exitCode = 1
    return
  }
  const r = await runCli(['probe', '--help'], {})
  console.log('mgvideo    :', r.code === 0 ? 'ok' : '异常')
  const ff = await runCli(['probe', '.'], {})
  console.log('ffmpeg     :', /ffmpeg|Duration/i.test(ff.out) || ff.code === 0 ? 'ok' : ffmpegHint())
}

if (!argv.length || argv[0] === '-h' || argv[0] === '--help') {
  console.log(`mgvideo — MG 动画渲染器（本地 Pillow 逐帧 + ffmpeg）

用法：
  mgvideo render <spec.json> -o <out.mp4> [--workers N] [--shard i/n] [--keep-frames]
  mgvideo check  <spec.json>
  mgvideo frames <video> --duration <秒> --out <目录> [--start <秒>] [--fps 30]
  mgvideo calib  <video> <秒> [--rect x,y,w,h] [--out <目录>]
  mgvideo probe  <video>
  mgvideo doctor

示例： mgvideo render examples/spec-demo.json -o demo.mp4`)
  process.exit(0)
}

if (argv[0] === 'doctor') {
  await doctor()
  process.exit(process.exitCode || 0)
}

const { code, out } = await runCli(argv, { cwd: ENGINE_DIR })
if (out) console.log(out)
process.exit(code)
