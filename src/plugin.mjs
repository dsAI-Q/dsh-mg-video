/**
 * DSH 插件入口：把 mgvideo 暴露成 4 个 agent 工具。
 *
 *   mg_render  spec → mp4（可能耗时，长片建议用 shell 后台跑）
 *   mg_check   校验清单并打印时间表
 *   mg_frames  从视频切逐帧，供 shot 模板（截图动图）用
 *   mg_calib   给视频某一秒出带坐标网格的预览图，用来读截图矩形
 *
 * 所有渲染都在本地完成：Pillow 逐帧 + ffmpeg，不调用任何在线服务。
 */
import path from 'node:path'
import { runCli, findPython, ffmpegHint, ENGINE_DIR } from './python.mjs'

export const name = 'mgvideo'
export const inject = ['tools']

const output = {
  schema: { type: 'string' },
  render: (_args, value) => [{ type: 'text', text: String(value) }],
}

const MAX_OUT = 6000

function clip(text, max = MAX_OUT) {
  const s = String(text ?? '')
  return s.length > max ? s.slice(0, max) + `\n…（已截断，共 ${s.length} 字）` : s
}

async function call(args, cwd) {
  try {
    const { code, out } = await runCli(args, { cwd })
    return clip(out ? (code === 0 ? out : `exit ${code}\n${out}`) : `exit ${code}`)
  } catch (e) {
    return `${e.message}\n${ffmpegHint()}`
  }
}

export function apply(ctx, config = {}) {
  const workDir = config.workDir || process.cwd()

  ctx.effect(() =>
    ctx.tools.register({
      name: 'mg_render',
      description:
        'Render a motion-graphics video from a JSON manifest (spec) into an mp4, fully locally (Pillow per-frame + ffmpeg, no API). ' +
        'The spec lists scenes; each scene picks a template (title / number / compare / flow / timeline / quote / bullets / shot / end) plus a background, ' +
        'a transition, an optional caption and optional impact effects. ' +
        'Use mg_check first to validate and see the timeline. ' +
        'Rendering 1080p runs at roughly 0.2 s per frame on one core; a 30 s clip is about 3 minutes. ' +
        'For long videos prefer running the CLI in a background shell instead of this blocking call.',
      parameters: {
        specPath: { type: 'string', required: true, description: 'Absolute path of the JSON spec file.' },
        outPath: { type: 'string', required: true, description: 'Absolute output path for the mp4.' },
        workers: { type: 'number', description: 'Parallel worker processes. Defaults to CPU count capped at 8; falls back to 1 where processes are not allowed.' },
        keepFrames: { type: 'boolean', description: 'Keep the rendered PNG frames next to the output (for inspection).' },
      },
      output,
      async execute({ specPath, outPath, workers, keepFrames }) {
        if (!specPath || !outPath) return 'Missing specPath or outPath.'
        const args = ['render', path.resolve(workDir, specPath), '-o', path.resolve(workDir, outPath)]
        if (workers) args.push('--workers', String(workers))
        if (keepFrames) args.push('--keep-frames')
        return call(args, ENGINE_DIR)
      },
    }),
  )

  ctx.effect(() =>
    ctx.tools.register({
      name: 'mg_check',
      description:
        'Validate an mgvideo JSON spec and print its timeline (scene index, template, start/end time, duration). ' +
        'Also flags unknown templates and missing frames directories. Fast, no rendering.',
      parameters: {
        specPath: { type: 'string', required: true, description: 'Absolute path of the JSON spec file.' },
      },
      output,
      async execute({ specPath }) {
        if (!specPath) return 'Missing specPath.'
        return call(['check', path.resolve(workDir, specPath)], ENGINE_DIR)
      },
    }),
  )

  ctx.effect(() =>
    ctx.tools.register({
      name: 'mg_frames',
      description:
        'Extract consecutive frames out of a screen recording into a folder, so the "shot" template can crop and annotate regions of it ' +
        '(boxes, brackets, arrows, labels, magnifier, scan). Frames are 1-based JPEG files named 0001.jpg, 0002.jpg …',
      parameters: {
        video: { type: 'string', required: true, description: 'Absolute path of the source video.' },
        duration: { type: 'number', required: true, description: 'How many seconds to extract.' },
        outDir: { type: 'string', required: true, description: 'Folder to write frames into.' },
        start: { type: 'number', description: 'Start time in seconds. Default 0.' },
        fps: { type: 'number', description: 'Frame rate to extract. Default 30; must match the spec fps.' },
        quality: { type: 'number', description: 'JPEG quality (2 = best). Default 2.' },
      },
      output,
      async execute({ video, duration, outDir, start, fps, quality }) {
        if (!video || !duration || !outDir) return 'Missing video, duration or outDir.'
        const args = ['frames', path.resolve(workDir, video), '--duration', String(duration),
          '--out', path.resolve(workDir, outDir)]
        if (start !== undefined) args.push('--start', String(start))
        if (fps) args.push('--fps', String(fps))
        if (quality) args.push('--quality', String(quality))
        return call(args, ENGINE_DIR)
      },
    }),
  )

  ctx.effect(() =>
    ctx.tools.register({
      name: 'mg_calib',
      description:
        'Produce a calibration image: one frame of a video with a 100 px coordinate grid drawn on it, so crop rectangles can be read off ' +
        'instead of guessed. Optionally draws candidate rectangles back onto the image to verify them in one look.',
      parameters: {
        video: { type: 'string', required: true, description: 'Absolute path of the source video.' },
        sec: { type: 'number', required: true, description: 'Timestamp in seconds to grab.' },
        rect: { type: 'string', description: 'Candidate rectangles as "x,y,w,h", several separated by ";".' },
        outDir: { type: 'string', description: 'Folder for the calibration image.' },
      },
      output,
      async execute({ video, sec, rect, outDir }) {
        if (!video || sec === undefined) return 'Missing video or sec.'
        const args = ['calib', path.resolve(workDir, video), String(sec)]
        if (rect) args.push('--rect', rect)
        if (outDir) args.push('--out', path.resolve(workDir, outDir))
        return call(args, ENGINE_DIR)
      },
    }),
  )

  ctx.effect(() =>
    ctx.tools.register({
      name: 'mg_lint',
      description:
        'Check a spec before rendering, and a finished video after rendering. Spec level: unknown template, non-positive duration, ' +
        'caption wider than the safe area, caption/impact time windows outside [0,1], transitions longer than the scene, and scenes whose ' +
        'duration is not a whole number of frames (a one-frame jump). Video level (optional): runs of more than 0.5 s with nothing to look ' +
        'at, audio peak above -1 dBFS, and duration drift against the spec. Exit code is 1 when an error-level problem is found, so it can ' +
        'gate a render pipeline.',
      parameters: {
        specPath: { type: 'string', required: true, description: 'Absolute path of the JSON spec file.' },
        video: { type: 'string', description: 'Absolute path of a rendered mp4 to also check. Optional.' },
      },
      output,
      async execute({ specPath, video }) {
        if (!specPath) return 'Missing specPath.'
        const args = ['lint', path.resolve(workDir, specPath)]
        if (video) args.push('--video', path.resolve(workDir, video))
        return call(args, ENGINE_DIR)
      },
    }),
  )

  ctx.effect(() =>
    ctx.tools.register({
      name: 'mg_layers',
      description:
        'List the layers a spec declares, as JSON: per scene the background, template content, impact, caption and chrome layers with ' +
        'z-order and absolute start/end seconds. Use this instead of looking at pixels: edit the spec fields it names, then re-render ' +
        'only the scene you touched with mg_patch.',
      parameters: {
        specPath: { type: 'string', required: true, description: 'Absolute path of the JSON spec file.' },
        outPath: { type: 'string', description: 'Write the JSON to this file instead of returning it on stdout.' },
      },
      output,
      async execute({ specPath, outPath }) {
        if (!specPath) return 'Missing specPath.'
        const args = ['layers', path.resolve(workDir, specPath)]
        if (outPath) args.push('--out', path.resolve(workDir, outPath))
        return call(args, ENGINE_DIR)
      },
    }),
  )

  ctx.effect(() =>
    ctx.tools.register({
      name: 'mg_patch',
      description:
        'Re-render ONE scene and splice it back into the film, using an existing frames folder (produce it first with mg_render + keepFrames). ' +
        'Much faster than a full re-render when you only change a caption or a number. Output duration is identical to the original; ' +
        'other scenes keep their already-rendered frames.',
      parameters: {
        specPath: { type: 'string', required: true, description: 'Absolute path of the JSON spec file.' },
        scene: { type: 'number', required: true, description: '1-based scene number to re-render.' },
        framesDir: { type: 'string', required: true, description: 'Folder holding every frame of the film.' },
        outPath: { type: 'string', required: true, description: 'Where to write the patched mp4.' },
        sets: {
          type: 'array',
          description: 'Field overrides for that scene, e.g. ["caption.text=新文案", "duration=3.5"]. Values are parsed as JSON first.',
        },
      },
      output,
      async execute({ specPath, scene, framesDir, outPath, sets }) {
        if (!specPath || !scene || !framesDir || !outPath) return 'Missing specPath, scene, framesDir or outPath.'
        const args = ['patch', path.resolve(workDir, specPath), '--scene', String(scene),
          '--frames-dir', path.resolve(workDir, framesDir), '-o', path.resolve(workDir, outPath)]
        for (const s of sets || []) args.push('--set', String(s))
        return call(args, ENGINE_DIR)
      },
    }),
  )

  // 启动时做一次环境自检，把结论写进日志，方便排查
  ctx.effect(() => {
    findPython()
      .then((bin) => ctx.logger?.info?.(`[mgvideo] python: ${bin}`))
      .catch((e) => ctx.logger?.warn?.(`[mgvideo] ${e.message}`))
  })
}
