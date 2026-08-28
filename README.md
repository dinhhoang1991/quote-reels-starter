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

`config.yaml` **được đọc thật** — màu, giọng, volume, safe zone, hook 3s.

## Chạy clip mẫu

```bash
python3 src/make_video.py --json data/samples/clip_001.json
```

File ra: `assets/out/clip_001.mp4`

- 3 giây đầu: chỉ tiêu đề (đọc được khi tắt tiếng).
- Sau đó: đủ list.
- Nhạc tự hạ khi có giọng (sidechain ducking).
- Audio AAC stereo 48 kHz, GOP 2s — đúng spec Reels API (3–90 giây).

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

## Hàng chờ mỗi ngày

```bash
python3 src/jobqueue.py add data/samples/clip_001.json
python3 src/jobqueue.py list
python3 src/publish.py --queue
```

Clip xong → `data/queue/done/`. Lỗi → `data/queue/failed/`.
Log đăng: `data/published.json` (chống đăng trùng + đếm 30 Reels / 24h).

Cron: xem `scripts/cron.example`.

## Tự động đăng Facebook Page

API chính thức **chỉ đăng lên Page**. Tối đa **30 Reels API / Page / 24 giờ**.

Quyền: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`.
User là admin Page, task `CREATE_CONTENT`.
App phải **Live** (hoặc user là tester) — Development mode không đăng được cho người ngoài.

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
python3 -m unittest tests/test_schema.py
```

## Lưu ý

- Giữ footage thật, đừng gen video AI cho kênh chính.
- 1 giọng / 1 template xuyên suốt kênh.
- 3 giây đầu phải đọc được tiêu đề khi tắt tiếng — script tự render overlay hook.
- Safe zone: top 200px, bottom 360px (UI Facebook).
- Đăng page phụ 7–14 ngày trước khi đưa sang page chính.
- Không dùng tool giả lập app / cookie / selenium để đăng profile.
- Token hết hạn thì clip render vẫn chạy, chỉ bước upload lỗi.
- `SCHEDULED` không nằm trong docs Reels API — đừng phụ thuộc.
