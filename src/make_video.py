#!/usr/bin/env python3
"""Build a 9:16 Reels video: footage + hook/full overlay + voice + ducked music.

Audio: AAC stereo 48 kHz, đúng bằng độ dài video (voice được đắp silence tới hết
clip nên `tail_seconds` và fade-out hoạt động thật). GOP closed 2s. Duration clamp 3–90s.
Hook overlay for the first N seconds so the title is readable muted.
Ken Burns (zoom + pan) chạy hết độ dài clip thay vì đứng hình sau vài giây.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from checks import clamp_duration, fonts_ok, probe_duration, require_ffmpeg
from config import load_config, resolve_path
from render_overlay import render_pair
from schema import content_warnings, load_clip

KEN_BURNS_DEFAULT: dict[str, object] = {
    "enabled": True,
    "zoom_start": 1.0,
    "zoom_end": 1.12,
    "pan": "left_right",
}

# Trục pan theo tiến độ clip: (trục, hướng). None = căn giữa trục đó.
KEN_BURNS_PAN_AXES: dict[str, tuple[str | None, str | None]] = {
    "center": (None, None),
    "left_right": ("x", "forward"),
    "right_left": ("x", "backward"),
    "top_bottom": ("y", "forward"),
    "bottom_top": ("y", "backward"),
}


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def ken_burns_options(cfg) -> dict[str, object]:
    """video.ken_burns, chấp nhận cả dạng boolean cũ lẫn dạng dict đầy đủ."""
    raw = cfg.video.get("ken_burns", True)
    if isinstance(raw, bool):
        opts = dict(KEN_BURNS_DEFAULT)
        opts["enabled"] = raw
        return opts
    if hasattr(raw, "as_dict"):
        raw = raw.as_dict()
    if not isinstance(raw, dict):
        raise SystemExit(f"video.ken_burns phải là true/false hoặc object, đang là {type(raw).__name__}")
    opts = dict(KEN_BURNS_DEFAULT)
    opts.update({key: value for key, value in raw.items() if key in opts})
    return opts


def ken_burns_plan(cfg, duration: float, fps: int) -> dict[str, object]:
    """Biểu thức zoompan cho ảnh tĩnh: zoom đạt `zoom_end` đúng ở frame cuối clip."""
    opts = ken_burns_options(cfg)
    frames = max(int(round(duration * fps)) + 2, 2)
    zoom_start = float(opts["zoom_start"])
    zoom_end = float(opts["zoom_end"])
    if zoom_end <= zoom_start:
        raise SystemExit(
            f"video.ken_burns.zoom_end ({zoom_end}) phải lớn hơn zoom_start ({zoom_start})"
        )
    pan = str(opts["pan"])
    if pan not in KEN_BURNS_PAN_AXES:
        raise SystemExit(
            f"video.ken_burns.pan không hợp lệ: {pan!r} (chọn {' | '.join(KEN_BURNS_PAN_AXES)})"
        )
    progress = f"min(on,{frames})/{frames}"
    axis, direction = KEN_BURNS_PAN_AXES[pan]
    pan_expr = progress if direction == "forward" else f"(1-{progress})"
    zoom_span = zoom_end - zoom_start
    return {
        "enabled": bool(opts["enabled"]),
        "frames": frames,
        "zoom_start": zoom_start,
        "zoom_end": zoom_end,
        "zoom_span": zoom_span,
        "zoom_step": zoom_span / frames,
        "pan": pan,
        "progress": progress,
        "pan_expr": pan_expr,
        "z": f"{zoom_start:.4f}+{zoom_span:.6f}*{progress}",
        "x": "(iw-iw/zoom)*" + (pan_expr if axis == "x" else "0.5"),
        "y": "(ih-ih/zoom)*" + (pan_expr if axis == "y" else "0.5"),
    }


def ken_burns_zoom_at(plan: dict[str, object], on: int) -> float:
    """Zoom tại frame thứ `on` của zoompan (zoompan đếm `on` từ 1).

    @param plan kết quả của ken_burns_plan
    @param on giá trị biến `on` trong biểu thức zoompan
    @returns hệ số zoom, kẹp trong [zoom_start + zoom_step, zoom_end]
    """
    frames = int(plan["frames"])
    progress = min(max(on, 1), frames) / frames
    return float(plan["zoom_start"]) + float(plan["zoom_span"]) * progress


def subtitles_enabled(cfg=None) -> bool:
    """Phụ đề burn-in có bật trong config không."""
    cfg = cfg or load_config()
    return bool((cfg.get("subtitles", {}) or {}).get("enabled", False))


def build_subtitles(data: dict, voice: Path | None, cfg=None) -> Path | None:
    """Sinh file ASS từ timing của bản đọc.

    Không có timing (ví dụ giọng Vbee/FPT truyền tay) thì bỏ qua và cảnh báo, trừ khi
    `subtitles.require_timings: false`.

    @param data clip đã validate
    @param voice file mp3 đã dùng
    @param cfg config, mặc định đọc config.yaml
    @returns đường dẫn file .ass hoặc None nếu bỏ qua
    """
    cfg = cfg or load_config()
    if not subtitles_enabled(cfg):
        return None
    from subtitles import build_cues, shift_words, words_from_timings, write_ass
    from tts import load_timings

    timings = load_timings(voice) if voice else []
    section = cfg.get("subtitles", {}) or {}
    if not timings and bool(section.get("require_timings", True)):
        print("Cảnh báo: không có timing từng từ (bản đọc ngoài edge-tts) — bỏ qua phụ đề.")
        return None
    style = section.as_dict() if hasattr(section, "as_dict") else dict(section)
    head_ms = int(float(cfg.audio.head_seconds) * 1000)
    cues = build_cues(shift_words(words_from_timings(timings), head_ms), style)
    if not cues:
        print("Cảnh báo: không dựng được cue phụ đề nào — bỏ qua phụ đề.")
        return None
    out = resolve_path(cfg.paths.overlay_dir) / f"{data['id']}.ass"
    write_ass(out, cues, style, int(cfg.video.width), int(cfg.video.height))
    print(f"Phụ đề: {len(cues)} cue → {out.name}")
    return out


def background_filter(
    cfg, duration: float, fps: int, width: int, height: int, is_still: bool
) -> str:
    """Chain `[0:v]... [bg]`: ảnh tĩnh thì zoom/pan, video thì scale/crop/fps."""
    if is_still and ken_burns_options(cfg)["enabled"]:
        plan = ken_burns_plan(cfg, duration, fps)
        scaled_w, scaled_h = width * 12 // 10, height * 12 // 10
        return (
            f"[0:v]scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
            f"crop={scaled_w}:{scaled_h},"
            f"zoompan=z='{plan['z']}':x='{plan['x']}':y='{plan['y']}':"
            f"d={plan['frames']}:s={width}x{height}:fps={fps},"
            f"setsar=1,fade=t=in:st=0:d=0.4[bg];"
        )
    return (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps},setsar=1,fade=t=in:st=0:d=0.4[bg];"
    )


def audio_filter(cfg, duration: float, fade_out_start: float) -> str:
    """Voice + music trộn đúng `duration` giây.

    `apad=whole_dur` đắp silence cho voice tới hết clip; nếu thiếu bước này thì
    `amix=duration=first` cắt audio theo voice và fade-out ở cuối không bao giờ chạy.
    """
    sr = int(cfg.audio.sample_rate)
    vol = float(cfg.audio.music_volume)
    duck = cfg.audio.ducking
    head_ms = int(float(cfg.audio.head_seconds) * 1000)
    return (
        f"[1:a]aresample={sr},aformat=channel_layouts=stereo,"
        f"adelay={head_ms}|{head_ms},apad=whole_dur={duration:.3f}[voice];"
        f"[voice]asplit=2[voice_mix][voice_sc];"
        f"[2:a]aresample={sr},aformat=channel_layouts=stereo,volume={vol}[m];"
        f"[m][voice_sc]sidechaincompress=threshold={duck.threshold}:ratio={duck.ratio}:"
        f"attack={duck.attack}:release={duck.release}:makeup=2[ducked];"
        f"[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=2,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.25,afade=t=out:st={fade_out_start:.2f}:d=0.45[a]"
    )


def cover_path(data: dict, cfg=None) -> Path:
    """Đường dẫn ảnh cover của clip (dùng cho Reel cover, YouTube thumbnail…).

    @param data clip đã validate
    @param cfg config, mặc định đọc config.yaml
    @returns đường dẫn trong assets/overlays/
    """
    cfg = cfg or load_config()
    return resolve_path(cfg.paths.overlay_dir) / f"{data['id']}_cover.jpg"


def cover_at_seconds(cfg=None) -> float:
    """Mốc lấy ảnh cover: `cover.at_seconds`, mặc định giữa đoạn hook (tiêu đề hiện rõ).

    @param cfg config, mặc định đọc config.yaml
    @returns số giây
    """
    cfg = cfg or load_config()
    configured = float((cfg.get("cover", {}) or {}).get("at_seconds", 0) or 0)
    if configured > 0:
        return configured
    return max(float(cfg.hook.seconds) / 2, 0.1)


def make_cover(video: Path, data: dict, cfg=None) -> Path:
    """Trích 1 frame trong đoạn hook làm ảnh cover.

    @param video file mp4 đã render
    @param data clip đã validate
    @param cfg config, mặc định đọc config.yaml
    @returns đường dẫn ảnh jpg
    """
    cfg = cfg or load_config()
    out = cover_path(data, cfg)
    out.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{cover_at_seconds(cfg):.2f}", "-i", str(video),
            "-frames:v", "1", "-update", "1", "-q:v", "2", str(out),
        ]
    )
    return out


def first_file(folder: Path, exts: tuple[str, ...]) -> Path | None:
    """File media đầu tiên (theo alphabet) trong thư mục, bỏ placeholder.

    @param folder thư mục cần quét
    @param exts đuôi file chấp nhận
    @returns đường dẫn hoặc None
    """
    if not folder.exists():
        return None
    files = [
        p
        for p in sorted(folder.iterdir())
        if p.suffix.lower() in exts and p.is_file() and not p.name.startswith("_placeholder")
    ]
    return files[0] if files else None


def make_placeholder_footage(path: Path, seconds: float, width: int, height: int, fps: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"gradients=s={width}x{height}:d={seconds:.2f}:speed=0.04:c0=0x061428:c1=0x1b5f73:c2=0x0b2a3a:n=3",
            "-r",
            str(fps),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            str(path),
        ]
    )
    return path


def make_placeholder_music(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=brown:d={seconds:.2f}:a=0.15",
            "-af",
            "lowpass=f=600,highpass=f=80,volume=0.35",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "96k",
            str(path),
        ]
    )
    return path


def ensure_voice(data: dict, voice: Path | None) -> Path:
    cfg = load_config()
    if voice is not None:
        if not voice.exists():
            raise SystemExit(f"Không thấy file giọng: {voice}")
        return voice
    from tts import synth_with_timings, voice_path

    dest = voice_path(data, cfg)
    if dest.exists():
        print(f"Dùng lại bản đọc đã cache: {dest.name}")
        return dest
    import asyncio

    asyncio.run(
        synth_with_timings(data["voice_script"], dest, cfg.audio.voice_name, cfg.audio.voice_rate)
    )
    return dest


def build(
    json_path: Path,
    footage: Path | None,
    music: Path | None,
    voice: Path | None,
) -> Path:
    require_ffmpeg()
    fonts_ok()
    cfg = load_config()
    data = load_clip(json_path)
    for warning in content_warnings(data):
        print(f"Cảnh báo nội dung: {warning}")

    width, height, fps = int(cfg.video.width), int(cfg.video.height), int(cfg.video.fps)
    fonts_dir = resolve_path(cfg.paths.fonts_dir)
    overlay_dir = resolve_path(cfg.paths.overlay_dir)
    hook_png, full_png = render_pair(data, fonts_dir, overlay_dir)

    voice = ensure_voice(data, voice)
    voice_dur = probe_duration(voice)
    head = float(cfg.audio.head_seconds)
    tail = float(cfg.audio.tail_seconds)
    max_seconds = float(cfg.video.max_seconds)
    if voice_dur + head + tail > max_seconds:
        print(
            f"Cảnh báo: bản đọc dài {voice_dur:.1f}s, vượt {max_seconds:.0f}s của Reels — "
            f"video sẽ bị cắt giữa câu. Rút ngắn voice_script hoặc tách thành 2 clip."
        )
    duration = clamp_duration(voice_dur + head + tail)
    hook_s = min(float(cfg.hook.seconds), max(duration - 0.4, 0.8))

    still_ext = (".jpg", ".jpeg", ".png", ".webp")
    video_ext = (".mp4", ".mov", ".mkv", ".webm")
    if footage is None:
        footage = first_file(resolve_path(cfg.paths.footage_dir), video_ext + still_ext)
    if footage is None:
        footage = make_placeholder_footage(
            resolve_path(cfg.paths.footage_dir) / "_placeholder.mp4",
            max(duration, 12),
            width,
            height,
            fps,
        )
    is_still = footage.suffix.lower() in still_ext

    if music is None:
        music = first_file(resolve_path(cfg.paths.music_dir), (".mp3", ".wav", ".m4a", ".aac"))
    if music is None:
        music = make_placeholder_music(
            resolve_path(cfg.paths.music_dir) / "_placeholder.m4a", duration
        )

    out = resolve_path(cfg.paths.out_dir) / f"{data['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    sr = int(cfg.audio.sample_rate)
    ch = int(cfg.audio.channels)
    fade_out_start = max(duration - 0.5, 0.2)
    gop = int(cfg.video.gop)
    crf = int(cfg.video.crf)

    ass_path = build_subtitles(data, voice, cfg)
    subtitle_chain = ""
    if ass_path is not None:
        from subtitles import subtitles_filter

        subtitle_chain = "," + subtitles_filter(ass_path, fonts_dir)

    filter_complex = (
        background_filter(cfg, duration, fps, width, height, is_still)
        + f"[bg][3:v]overlay=0:0:enable='lt(t,{hook_s:.2f})':format=auto[v1];"
        + f"[v1][4:v]overlay=0:0:enable='gte(t,{hook_s:.2f})':format=auto"
        + subtitle_chain
        + f",fade=t=out:st={fade_out_start:.2f}:d=0.45[v];"
        + audio_filter(cfg, duration, fade_out_start)
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(footage),
        "-i",
        str(voice),
        "-stream_loop",
        "-1",
        "-i",
        str(music),
        "-loop",
        "1",
        "-t",
        f"{duration:.2f}",
        "-i",
        str(hook_png),
        "-loop",
        "1",
        "-t",
        f"{duration:.2f}",
        "-i",
        str(full_png),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{duration:.2f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.0",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        str(sr),
        "-ac",
        str(ch),
        "-movflags",
        "+faststart",
        str(out),
    ]
    run(cmd)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Render 1 Reels từ file JSON")
    parser.add_argument("--json", required=True)
    parser.add_argument("--footage", default="")
    parser.add_argument("--music", default="")
    parser.add_argument("--voice", default="", help="Đường dẫn file mp3 (Vbee/FPT/edge-tts)")
    args = parser.parse_args()

    out = build(
        Path(args.json),
        Path(args.footage) if args.footage else None,
        Path(args.music) if args.music else None,
        Path(args.voice) if args.voice else None,
    )
    print(f"\nXONG: {out}")


if __name__ == "__main__":
    main()
