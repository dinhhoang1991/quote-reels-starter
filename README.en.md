# Quote Reels Starter

Starter kit for building Facebook Reels from list-style script JSON: overlay PNG (Pillow),
Vietnamese voiceover (edge-tts), FFmpeg render, and publishing through the official
Facebook Page Reels API.

Tiếng Việt: [README.md](README.md)

```
list JSON → hook/full PNG overlay (Pillow)
              ↓
      voice (edge-tts) + footage + music
              ↓
   FFmpeg 9:16 · AAC stereo 48 kHz · ducking · fade
              ↓
   Facebook Page Reels API (retry, 30/24h quota, duplicate guard)
```

## Install

Python 3.10+ and FFmpeg.

```bash
pip install -r requirements.txt
python3 src/doctor.py          # check env, assets, config, queue
python3 src/doctor.py --json data/samples/clip_001.json   # check one clip
```

Run the pipeline from a checkout: `config.yaml`, `assets/` and `data/` resolve from the repo root,
so `pip install .` only installs metadata, not the scripts.

## Render a sample

```bash
python3 src/make_video.py --json data/samples/clip_001.json   # -> assets/out/clip_001.mp4
python3 src/batch.py                                          # both samples
python3 src/render_overlay.py --json data/samples/clip_001.json   # overlay only
python3 src/tts.py --json data/samples/clip_001.json           # voice only
```

Output is 1080x1920, 30 fps, H.264 High@4.0, 2s GOP, AAC stereo 48 kHz, 3–90s, with a 3s
title-only hook so the clip reads muted. Still footage gets a Ken Burns zoom/pan across the
whole clip (`video.ken_burns` in `config.yaml`).

## Daily queue

```bash
python3 src/jobqueue.py add data/samples/clip_001.json
python3 src/publish.py --queue          # render + publish the next pending clip
```

Finished clips move to `data/queue/done/`; every failure (including an FFmpeg crash) moves the
clip to `data/queue/failed/` with `_queue.attempts` and `_queue.last_error`, so cron never gets
stuck on one bad clip. `--queue` holds `data/queue/.queue.lock` so two cron runs cannot grab the
same clip. See [scripts/cron.example](scripts/cron.example).

## Publishing to a Facebook Page

The Reels API only publishes to a Page, up to 30 Reels per Page per 24h. You need
`pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, and the app must be Live.

```bash
cp .env.example .env      # fill FB_APP_ID, FB_APP_SECRET, FB_PAGE_ID
python3 src/fbtoken.py --short USER_SHORT_LIVED_TOKEN   # exchange + list Page tokens
python3 src/upload_facebook.py --video assets/out/clip_001.mp4 --json data/samples/clip_001.json
```

Rate-limit errors (HTTP 400 with `error.code` 4/17/32/613) are retried using Meta's
`estimated_time_to_regain_access`; real errors (bad token, missing permission) fail immediately.
`doctor.py --online` calls `debug_token` and warns before the ~60-day token expires.

## Topics, duplicate guard and insights

```bash
python3 src/topics.py next        # next topic to write (least used, longest idle)
python3 src/insights.py refresh   # pull Graph video_insights into data/insights.json
python3 src/insights.py summary   # top clips + average per topic
```

The rotation list lives in `config.yaml` (`content.topics`); `publish.py` records each clip's
`topic`, so rotation needs no bookkeeping. Before publishing, content is compared with earlier
posts (diacritics stripped, Jaccard similarity) and blocked above `content.duplicate_similarity`
(default 0.85) unless `--force` is passed.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

No FFmpeg, edge-tts or token needed: tests use the standard library, temp dirs and a fake HTTP layer.
CI runs ruff and the suite on Python 3.10–3.13 plus a `doctor.py` job.

Contributing: `pip install -r requirements-dev.txt` (ruff is pinned so the lint gate stays stable),
then `ruff check src tests`.

## Notes

- Keep real footage; do not use AI-generated video for the main channel.
- One voice and one template for the whole channel.
- The sample photo and audio files are placeholders for testing — see [assets/CREDITS.md](assets/CREDITS.md).
- Instagram-style safe zones: 200px top, 360px bottom kept clear of the Facebook UI.
- `SCHEDULED` is not part of the documented Reels API — do not depend on it.
