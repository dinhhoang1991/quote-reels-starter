# Quote Reels Starter

Bộ khởi động làm video Reels Facebook kiểu list đời sống / tài chính.

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

Font Be Vietnam Pro (OFL) nằm trong `assets/fonts/`.
Footage mẫu biển hoàng hôn nằm trong `assets/footage/ocean-sunset.jpg`.

`config.yaml` **được đọc thật** — màu, giọng, volume, safe zone, hook 3s, ken burns (zoom/pan).

## Chạy clip mẫu

```bash
python3 src/make_video.py --json data/samples/clip_001.json
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

## Test

```bash
python3 -m unittest discover -s tests -t .
```

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
