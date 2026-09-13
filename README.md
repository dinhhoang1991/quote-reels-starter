# Quote Reels Starter

Bộ khởi động làm video Reels Facebook kiểu list đời sống / tài chính.

English: [README.en.md](README.en.md)

Pipeline:

```
JSON list  →  PNG chữ (Pillow, hook 3s + full)
                 ↓
        Voice (edge-tts) + timing từng từ + footage + nhạc
                 ↓
        FFmpeg 9:16 · phụ đề burn-in · AAC stereo 48 kHz · ducking · fade
                 ↓
        Facebook Page Reels API (+ YouTube/TikTok) — retry, quota, chống trùng
```

## Cài đặt

Cần Python 3.10+ và FFmpeg.

```bash
cd quote-reels-starter
pip install -r requirements.txt
```

Repo chạy trực tiếp từ checkout (`python3 src/...`): `config.yaml`, `assets/` và `data/` được
resolve theo thư mục gốc repo, nên `pip install .` chỉ lấy metadata chứ không cài script —
đừng kỳ vọng chạy được từ site-packages.

Font Be Vietnam Pro (OFL) nằm trong `assets/fonts/`.
Footage mẫu biển hoàng hôn nằm trong `assets/footage/ocean-sunset.jpg` — **chỉ để chạy thử**,
xem [assets/CREDITS.md](assets/CREDITS.md) trước khi dùng cho kênh thật.

Muốn footage chuyển động: thả video vào `assets/footage/` rồi chuẩn hoá về 1080x1920/30fps:

```bash
./scripts/prepare_footage.sh   # ghi ra assets/footage/_normalized/
```

`config.yaml` **được đọc thật** — màu, giọng, volume, safe zone, hook 3s, ken burns (zoom/pan).

Kiểm tra môi trường trước khi chạy (xem thêm [doctor](#kiểm-tra-trước-khi-chạy-doctor)):

```bash
python3 src/doctor.py
```

## Chạy clip mẫu

```bash
python3 src/make_video.py --json data/samples/clip_001.json
# hoặc
./scripts/run_sample.sh
```

File ra: `assets/out/clip_001.mp4`

- 3 giây đầu: chỉ tiêu đề (đọc được khi tắt tiếng).
- Sau đó: đủ list.
- Nhạc tự hạ khi có giọng (sidechain ducking).
- Audio dài **đúng bằng video**: voice được đắp silence hết `tail_seconds` nên fade-out ở cuối chạy thật.
- Audio AAC stereo 48 kHz, GOP 2s — đúng spec Reels API (3–90 giây).
- Ảnh tĩnh được zoom/pan (Ken Burns) trải hết độ dài clip — xem `video.ken_burns` trong `config.yaml`.

Làm cả 2 mẫu:

```bash
python3 src/batch.py
```

## Làm clip mới

1. Copy prompt trong `prompts/generate_list.md` vào Claude / ChatGPT / Grok.
2. Dán JSON vào `data/samples/clip_003.json` (có đủ `caption` + `voice_script`).
3. Thả video biển/hoàng hôn vào `assets/footage/` nếu muốn footage chuyển động.
4. Chạy:

```bash
python3 src/make_video.py --json data/samples/clip_003.json
```

Chỉ overlay:

```bash
python3 src/render_overlay.py --json data/samples/clip_001.json
```

Chỉ giọng:

```bash
python3 src/tts.py --json data/samples/clip_001.json
python3 src/tts.py --json data/samples/clip_001.json --voice vi-VN-HoaiMyNeural
```

Giọng Vbee / FPT.AI: xuất mp3 rồi

```bash
python3 src/make_video.py --json data/samples/clip_001.json --voice assets/voice/clip_001_vbee.mp3
```

`--voice` ở `make_video.py` / `publish.py` là **đường dẫn file**.
`--voice` ở `tts.py` là **tên giọng Neural**.

Bản đọc được cache trong `assets/voice/` theo tên `clip_001.<hash>.mp3`, hash của
(lời thoại + giọng + rate) — sửa lời thoại hoặc đổi giọng là render ra bản đọc mới,
không dùng lại bản cũ. File cũ không tự xoá, thỉnh thoảng dọn tay.

## Kiểm tra trước khi chạy (doctor)

```bash
python3 src/doctor.py                                    # môi trường, asset, config, hàng chờ
python3 src/doctor.py --json data/samples/clip_001.json  # thêm 1 clip: nội dung, độ dài đọc, overlay
python3 src/doctor.py --online                           # gọi Graph debug_token: hạn + quyền
```

Mỗi dòng là `[ok]` / `[warn]` / `[fail]`; exit code 1 khi có `[fail]`.
`--online` cần `FB_APP_ID` + `FB_APP_SECRET` trong `.env` và cảnh báo trước khi token hết hạn.

## Hàng chờ mỗi ngày

```bash
python3 src/jobqueue.py add data/samples/clip_001.json
python3 src/jobqueue.py list
python3 src/publish.py --queue                 # 1 clip mỗi lần chạy
python3 src/publish.py --queue --max 3         # tối đa 3 clip
python3 src/publish.py --queue --max 0         # dọn hết hàng chờ
```

Batch dừng sớm nếu **2 clip liên tiếp lỗi cùng một lỗi** (thường là token hỏng) để không đốt cả hàng
chờ vào cùng một nguyên nhân; mọi clip lỗi vẫn được đưa sang `failed/` + gửi cảnh báo.

Clip xong → `data/queue/done/`. Lỗi → `data/queue/failed/`.
Log đăng: `data/published.json` (chống đăng trùng; hạn mức 30 Reels/24h chỉ tính bài `PUBLISHED`,
bản `DRAFT` không bị trừ oan).

Mọi lỗi trong lúc render/upload (kể cả FFmpeg chết) đều đẩy clip sang `data/queue/failed/`
kèm `_queue.attempts` + `_queue.last_error`, nên cron không bị kẹt ở đúng clip lỗi. Muốn thử lại:

```bash
python3 src/jobqueue.py move data/queue/failed/clip_001.json --to pending
```

`--queue` giữ lock `data/queue/.queue.lock`: cron chạy chồng sẽ bị chặn thay vì lấy trùng clip.
Lock cũ hơn `queue.stale_lock_seconds` (mặc định 1 giờ) bị coi là rác và lấy lại.
Log in ra có timestamp UTC; `scripts/cron.example` ghi vào `logs/`.

Cron: xem `scripts/cron.example`.

## Tự động đăng Facebook Page

API chính thức **chỉ đăng lên Page**. Tối đa **30 Reels API / Page / 24 giờ**.

Quyền: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`.
User là admin Page, task `CREATE_CONTENT`.
App phải **Live** (hoặc user là tester) — Development mode không đăng được cho người ngoài.

Lỗi hạn mức của Graph trả về HTTP 400 kèm `error.code` 4 / 17 / 32 / 613 — script nhận diện
và thử lại theo `estimated_time_to_regain_access` (kẹp bởi `facebook.retry_max_delay_seconds`),
đồng thời in `X-App-Usage` / `X-Business-Use-Case-Usage` để biết đang dùng bao nhiêu % hạn mức.
Lỗi thật (token sai, thiếu quyền) fail ngay, không thử lại.

### Token ~60 ngày

```bash
cp .env.example .env
# điền FB_APP_ID, FB_APP_SECRET, FB_PAGE_ID
python3 src/fbtoken.py --short USER_SHORT_LIVED_TOKEN
```

Copy Page token vào `FB_PAGE_ACCESS_TOKEN`. Production: System User trong Business Manager.

### Đăng clip đã render

```bash
python3 src/upload_facebook.py \
  --video assets/out/clip_001.mp4 \
  --json data/samples/clip_001.json
```

Nháp (Graph có thể từ chối — docs Reels API chỉ nêu `PUBLISHED`):

```bash
python3 src/upload_facebook.py --video assets/out/clip_001.mp4 --json data/samples/clip_001.json --state DRAFT
```

Đăng lại clip đã có trong log: thêm `--force`.

### Render xong đăng luôn

```bash
python3 src/publish.py --json data/samples/clip_001.json
```

## Xoay vòng chủ đề và chống trùng nội dung

```bash
python3 src/topics.py list    # chủ đề, số lần đã đăng, lần cuối
python3 src/topics.py next    # in chủ đề nên làm tiếp (ít dùng nhất, lâu nhất)
```

Danh sách chủ đề nằm trong `config.yaml` (`content.topics`). `publish.py` ghi `topic` của clip vào
`data/published.json`, nên vòng xoay tự tính — không cần đánh dấu tay.

Thứ tự chọn: **ít dùng nhất → điểm insights cao nhất → lâu nhất**. Nghĩa là vẫn phủ đều mọi chủ đề,
nhưng khi hai chủ đề ngang số lần thì chủ đề đang hiệu quả hơn được làm trước (điểm lấy từ
`src/insights.py refresh`). Chạy `python3 src/topics.py list` để xem số lần + điểm từng chủ đề.

Trước khi đăng, nội dung được so với các bài đã publish (bỏ dấu, chữ thường, so theo Jaccard):
bài trùng khít hoặc giống từ `content.duplicate_similarity` (mặc định 85%) sẽ bị chặn kèm gợi ý
`--force`. Đặt ngưỡng 0 để tắt.

## Hiệu quả bài đăng (Insights)

```bash
python3 src/insights.py refresh   # gọi Graph cho các Reel đã publish, lưu data/insights.json
python3 src/insights.py summary   # top clip + điểm trung bình theo chủ đề
```

Metric lấy theo `insights.metrics` trong `config.yaml`; Graph có thể từ chối vài metric tuỳ loại
video/quyền token nên script thử lại từng metric và ghi lại cái lỗi vào `metrics_failed`.
Dùng `summary` để biết chủ đề nào đang hiệu quả rồi ưu tiên ở vòng xoay.

## Phụ đề burn-in

Phụ đề được đốt thẳng vào video từ **timing từng từ** của edge-tts (`WordBoundary`), nên chữ hiện
đúng lúc đọc. Bật/tắt và chỉnh trong `config.yaml` (`subtitles.*`): số từ mỗi dòng, số ký tự tối đa,
cắt theo khoảng lặng, cỡ chữ, màu, viền, `margin_v` (mặc định 470 — phải cao hơn footer của overlay),
`overlay_gap` (khe hở 16px giữa item cuối và dải phụ đề), `karaoke: true` nếu muốn từ đã đọc được tô màu dần.

- Khi `enabled: true`, overlay tự dừng trước dải phụ đề (`chiều cao - margin_v - font_size -
  overlay_gap`) nên danh sách 8-10 item không bị phụ đề vẽ đè; tắt phụ đề thì overlay lấy lại phần đất đó.

- Timing lưu cạnh file mp3: `assets/voice/<id>.<hash>.mp3.words.json`.
- Bản đọc ngoài edge-tts (Vbee/FPT truyền qua `--voice file.mp3`) **không có timing** nên phụ đề tự
  bỏ qua kèm cảnh báo; đặt `subtitles.require_timings: false` nếu muốn vẫn render (không có phụ đề).
- Mốc thời gian được dịch theo `audio.head_seconds` để khớp với voice đã bị đẩy trễ trong mix.
- `scripts/smoke_render.py` kiểm cả phụ đề: render thật rồi đếm pixel trắng ở nửa dưới khung hình.

## Comment đầu tiên, ảnh cover

Comment đầu (Page tự comment dưới Reel) lấy từ `first_comment` trong JSON clip, hoặc template
`facebook.first_comment` trong `config.yaml` (placeholder `{title}`, `{footer}`, `{topic}`, `{caption}`).
Để trống cả hai = không đăng comment. Bỏ qua bằng `--no-first-comment`.
Comment lỗi không làm job fail vì Reel đã lên rồi — lỗi được ghi vào `first_comment_error`.

Ảnh cover được trích từ 1 frame trong đoạn hook (`cover.at_seconds`, mặc định giữa hook) thành
`assets/overlays/<id>_cover.jpg`; dùng làm thumbnail YouTube. Facebook nhận mốc `cover` qua
`facebook.thumb_offset_ms` (0 = để Meta tự chọn) — nếu Graph/phiên bản API của bạn từ chối tham số
này thì để 0.

## Cross-post YouTube Shorts / TikTok

```bash
# xem trước request, không gọi mạng
python3 src/upload_youtube.py --video assets/out/clip_001.mp4 --json data/samples/clip_001.json --dry-run
python3 src/upload_tiktok.py  --video assets/out/clip_001.mp4 --json data/samples/clip_001.json --dry-run

# đăng thật (cần credential trong .env, xem .env.example)
python3 src/publish.py --json data/samples/clip_001.json --crosspost youtube,tiktok
```

Bật mặc định bằng `crosspost.targets: [youtube, tiktok]` trong `config.yaml`; `--crosspost none` để tắt
cho một lần chạy. Lỗi ở một nền tảng chỉ được ghi lại, không làm hỏng bước đăng Facebook.
Đã cross-post rồi thì lần sau tự bỏ qua (thêm `--force` để đăng lại).

**Caption/hashtag riêng theo nền tảng**: `youtube_caption` và `tiktok_caption` trong JSON clip (nếu có)
được dùng thay cho caption Facebook; hashtag thêm bằng `crosspost.youtube_tags` / `crosspost.tiktok_tags`
(không thêm trùng). TikTok đặt hashtag ngay trong caption và cắt theo giới hạn 2200 ký tự.

- **YouTube**: cần OAuth client (scope `youtube.upload`) + `YOUTUBE_REFRESH_TOKEN`; video được đẩy
  bằng resumable upload, tiêu đề tự thêm `#Shorts`, ảnh cover đặt qua `thumbnails.set`.
- **TikTok**: cần `TIKTOK_ACCESS_TOKEN` (scope `video.publish`) và app đã được TikTok duyệt.
  App chưa duyệt chỉ đăng được ở chế độ riêng tư — vì vậy mặc định `TIKTOK_PRIVACY_LEVEL=SELF_ONLY`.

Hai uploader này **chưa được chạy với API thật** ở môi trường phát triển (không có app/account được
duyệt); chúng được kiểm bằng HTTP giả trong `tests/test_crosspost.py` và có `--dry-run` để bạn soi
request trước khi đăng.

## Dọn file cũ

```bash
python3 src/cleanup.py                # liệt kê file cũ hơn cleanup.keep_days (mặc định 30 ngày)
python3 src/cleanup.py --apply        # xoá thật
python3 src/cleanup.py --days 7 --apply
```

Chỉ đụng tới `.mp3/.wav` trong `assets/voice`, `.png` trong `assets/overlays`, `.mp4` trong
`assets/out`; **không** xoá file của clip đang nằm trong `data/queue/pending|failed`, không xoá
`_placeholder*`/`.gitkeep`.

## Chạy trong Docker

```bash
cp .env.example .env          # điền token FB (+ YouTube/TikTok nếu cross-post)
docker compose up -d --build
docker compose logs -f
docker compose exec reels python3 src/doctor.py --online
```

Container cài sẵn FFmpeg, chạy `doctor.py` lúc khởi động (đặt `DOCTOR_STRICT=1` để dừng nếu có `[fail]`)
rồi vào lịch bằng `src/scheduler.py` — không cần cron của host. Giờ chạy hiểu theo `SCHEDULE_TZ`
(mặc định `Asia/Ho_Chi_Minh`, tên IANA), đổi bằng `SCHEDULE_HOUR`/`SCHEDULE_MINUTE`, và
`SCHEDULE_COMMAND` để đổi việc (ví dụ `--max 3`). `assets/`, `data/`, `logs/` được mount từ host nên
build lại image không mất dữ liệu.

Lịch khác (ví dụ dọn file mỗi tuần) thì đổi `SCHEDULE_COMMAND`:

```bash
SCHEDULE_COMMAND="python3 src/cleanup.py --apply" SCHEDULE_HOUR=3 docker compose up -d
```

## Test

```bash
python3 -m unittest discover -s tests -t .
```

Không cần FFmpeg, edge-tts hay token — test dùng thư viện chuẩn, tmp dir và fake HTTP layer.
CI (`.github/workflows/ci.yml`) chạy ruff + test trên Python 3.10–3.13 và một job `doctor.py`.
Dependency pin trong `requirements.txt`/`pyproject.toml` (hai bên phải khớp — có test kiểm tra).

Góp code thì cài thêm công cụ dev (đã pin ruff để gate lint không tự đổi):

```bash
pip install -r requirements-dev.txt
ruff check src tests
```

## Chạy thật lần đầu

Checklist từng bước cho lần đầu đăng bằng tài khoản thật — điền `.env` → `doctor --online` →
diễn tập `--dry-run` → đăng 1 clip → kiểm comment/cover/hạn mức: xem [RUNBOOK.md](RUNBOOK.md).

## Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `Thiếu ffmpeg` | Cài FFmpeg rồi chạy lại. Thiếu `ffprobe` vẫn chạy được (đọc duration bằng ffmpeg, chậm hơn). |
| `Chưa cài edge-tts` / TTS lỗi mạng | `pip install edge-tts`, hoặc xuất mp3 từ Vbee/FPT rồi `--voice assets/voice/xxx.mp3`. |
| Graph trả `code 190` | Token hết hạn/sai → `python3 src/fbtoken.py --short TOKEN`; chạy `doctor.py --online` để biết trước. |
| Graph trả `code 4/17/32/613` | Hạn mức — script tự chờ theo `estimated_time_to_regain_access` rồi thử lại; nếu vẫn lỗi thì chờ hoặc giảm `facebook.daily_limit`. |
| Queue đứng mãi ở một clip | Xem `data/queue/failed/<id>.json` → `_queue.last_error`; sửa rồi `python3 src/jobqueue.py move data/queue/failed/<id>.json --to pending`. |
| `Cron khác đang chạy` | Còn lock `data/queue/.queue.lock`; nếu chắc chắn không tiến trình nào chạy thì xoá file đó. |
| Cron không ghi log | Phải có thư mục `logs/` (cron mẫu đã `mkdir -p logs`); kiểm tra `logs/publish.log`. |
| `OverlayFitError` | Tiêu đề/item quá dài — rút ngắn hoặc bớt item; xem trước bằng `doctor.py --json <file>`. |
| Bản đọc bị cắt giữa câu | `voice_script` dài quá 90s; `doctor.py --json` cảnh báo trước khi gọi TTS. |
| Sửa lời thoại mà audio không đổi | Không còn xảy ra: file cache có hash. Kiểm tra `assets/voice/` xem có file hash mới không. |
| Phụ đề không hiện | Bản đọc không có timing (giọng ngoài edge-tts) — xem cảnh báo khi render; dùng giọng edge-tts hoặc chấp nhận không có phụ đề. |
| Phụ đề sớm/muộn so với tiếng | Do `audio.head_seconds` đổi mà timing dịch theo; chỉnh `subtitles.margin_v`/kiểm lại `head_seconds`. |
| Phụ đề đè lên item cuối của overlay | Không còn xảy ra: overlay tự chừa dải phụ đề + `subtitles.overlay_gap`. Nếu vẫn chạm, tăng `overlay_gap` hoặc giảm `margin_v`. |
| `Thiếu subtitles` trong doctor | FFmpeg không có libass; cài bản ffmpeg đầy đủ hoặc đặt `subtitles.enabled: false`. |
| Video dùng nhạc/nền giả | `assets/music/` hoặc `assets/footage/` trống nên pipeline tự sinh placeholder — `doctor.py` cảnh báo. |
| `Clip ... trùng nội dung với bài đã đăng` | Chống trùng đang chặn; sửa nội dung cho khác, hoặc `--force`, hoặc hạ `content.duplicate_similarity`. |
| Cross-post lỗi nhưng Reel đã lên | Đúng thiết kế: lỗi từng nền tảng chỉ được ghi lại. Xem dòng `crosspost <nền tảng>: LỖI ...` hoặc `doctor.py` để biết thiếu credential nào. |
| YouTube trả `308` | File quá lớn cho upload 1 request (giới hạn ~64MB) — Shorts thường không gặp. |
| TikTok chỉ đăng ở chế độ riêng tư | App chưa được TikTok duyệt; đặt `TIKTOK_PRIVACY_LEVEL` cao hơn sẽ bị từ chối. |
| Lịch chạy sai giờ | Giờ hiểu theo `SCHEDULE_TZ`/`schedule.timezone` (mặc định `Asia/Ho_Chi_Minh`), không phải UTC — đổi tz hoặc giờ cho khớp. |
| Batch dừng giữa đường | Hai clip lỗi cùng một lỗi thì batch dừng (tránh đốt cả hàng chờ); xem `data/queue/failed/*/_queue.last_error`. |
| Container không chạy lịch | Xem `docker compose logs`; lịch tính theo **giờ UTC** (`SCHEDULE_HOUR`), và `DOCTOR_STRICT=1` sẽ chặn container khi thiếu token. |
| Muốn xoá file cũ cho nhẹ máy | `python3 src/cleanup.py` xem trước, `--apply` mới xoá; file của clip đang trong queue luôn được giữ. |

## Lưu ý

- Giữ footage thật, đừng gen video AI cho kênh chính.
- 1 giọng / 1 template xuyên suốt kênh.
- 3 giây đầu phải đọc được tiêu đề khi tắt tiếng — script tự render overlay hook.
- Tiêu đề và items tự co cỡ chữ cho vừa safe zone. Nếu vẫn không vừa (quá nhiều item/quá dài),
  script dừng và báo lỗi rõ thay vì vẽ tràn ra ngoài khung.
- Nội dung vượt luật biên tập trong `prompts/generate_list.md` (title quá 8 chữ, item quá dài,
  không đủ 8–10 items) chỉ **cảnh báo**, không chặn render.
- Safe zone: top 200px, bottom 360px (UI Facebook).
- Đăng page phụ 7–14 ngày trước khi đưa sang page chính.
- Không dùng tool giả lập app / cookie / selenium để đăng profile.
- Token hết hạn thì clip render vẫn chạy, chỉ bước upload lỗi. Chạy
  `python3 src/doctor.py --online` định kỳ (xem `scripts/cron.example`) để biết trước khi hết hạn.
- Bản đọc quá 90 giây sẽ bị cắt giữa câu — script cảnh báo trước khi render (và `doctor.py`
  cảnh báo trước cả khi gọi TTS, dựa trên số ký tự).
- `SCHEDULED` không nằm trong docs Reels API — đừng phụ thuộc.
