# Quote Reels Starter

Bộ khởi động làm video Reels Facebook kiểu list đời sống / tài chính.

English: [README.en.md](README.en.md)

Pipeline:

```
JSON list  →  PNG chữ (Pillow, hook 3s + full)
                 ↓
        Voice (edge-tts) + footage + nhạc
                 ↓
        FFmpeg 9:16 · AAC stereo 48 kHz · ducking · fade
                 ↓
        Facebook Page Reels API (retry, quota 30/24h, chống trùng)
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
python3 src/publish.py --queue
```

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
| Video dùng nhạc/nền giả | `assets/music/` hoặc `assets/footage/` trống nên pipeline tự sinh placeholder — `doctor.py` cảnh báo. |
| `Clip ... trùng nội dung với bài đã đăng` | Chống trùng đang chặn; sửa nội dung cho khác, hoặc `--force`, hoặc hạ `content.duplicate_similarity`. |

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
