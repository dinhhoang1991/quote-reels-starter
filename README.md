# Quote Reels Starter

Bộ khởi động làm video Reels Facebook kiểu list đời sống / tài chính.

Pipeline:

```
JSON list  →  PNG chữ (Pillow)  →  Voice (edge-tts)
                 ↓
        Footage thật + nhạc
                 ↓
              FFmpeg  →  mp4 9:16
                 ↓
         Facebook Page Reels API
```

## Cài đặt

Cần Python 3.10+ và FFmpeg.

```bash
cd quote-reels-starter
pip install -r requirements.txt
```

Font Be Vietnam Pro (OFL) đã nằm trong `assets/fonts/`.

## Chạy clip mẫu ngay

```bash
python3 src/make_video.py --json data/samples/clip_001.json
```

File ra: `assets/out/clip_001.mp4`

Chưa có footage / nhạc thì script tự tạo nền gradient + nhạc placeholder để bạn kiểm tra chữ và giọng.

Làm cả 2 mẫu:

```bash
python3 src/batch.py
```

## Làm clip mới

1. Copy prompt trong `prompts/generate_list.md` vào Claude / ChatGPT / Grok.
2. Dán JSON vào `data/samples/clip_003.json`.
3. (Khuyên dùng) thả 1 video biển/hoàng hôn vào `assets/footage/`.
4. Chạy:

```bash
python3 src/make_video.py --json data/samples/clip_003.json
```

Chỉ render lớp chữ:

```bash
python3 src/render_overlay.py --json data/samples/clip_001.json
```

Chỉ tạo giọng:

```bash
python3 src/tts.py --json data/samples/clip_001.json
python3 src/tts.py --json data/samples/clip_001.json --voice vi-VN-HoaiMyNeural
```

## Footage đẹp hơn

Tải clip miễn phí:

- https://pixabay.com/videos/search/ocean%20sunset/
- https://www.pexels.com/search/videos/sunset%20ocean/

Rồi chuẩn hóa về 1080x1920:

```bash
bash scripts/prepare_footage.sh
```

Giọng production nên đổi sang Vbee / FPT.AI rồi truyền file vào:

```bash
python3 src/make_video.py --json data/samples/clip_001.json --voice assets/voice/clip_001_vbee.mp3
```

## Cấu trúc

```
quote-reels-starter/
  prompts/generate_list.md
  data/samples/clip_001.json
  src/render_overlay.py
  src/tts.py
  src/make_video.py
  src/batch.py
  assets/fonts/
  assets/footage/
  assets/music/
  assets/voice/
  assets/overlays/
  assets/out/
```

## Tự động đăng Facebook Page

API chính thức **chỉ đăng lên Page**, không đăng profile cá nhân. Tối đa **30 Reels API / Page / 24 giờ**.

### 1. Lấy token

1. Vào [developers.facebook.com](https://developers.facebook.com/) tạo App loại Business.
2. Thêm sản phẩm **Facebook Login** (hoặc Pages API).
3. Quyền cần có: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`.
4. User phải là admin Page và có quyền `CREATE_CONTENT`.
5. Graph API Explorer → chọn App → Generate Page Access Token của đúng Page.
6. Đổi sang long-lived token (kéo dài ~60 ngày), hoặc dùng System User token trong Business Manager nếu làm production.

```bash
cp .env.example .env
# điền FB_PAGE_ID và FB_PAGE_ACCESS_TOKEN
```

Lấy Page ID: vào Page → Giới thiệu, hoặc gọi:

```bash
curl "https://graph.facebook.com/v26.0/me/accounts?access_token=USER_TOKEN"
```

### 2. Đăng clip đã render

```bash
python3 src/upload_facebook.py \
  --video assets/out/clip_001.mp4 \
  --json data/samples/clip_001.json
```

Lưu nháp trước:

```bash
python3 src/upload_facebook.py --video assets/out/clip_001.mp4 --json data/samples/clip_001.json --state DRAFT
```

Hẹn giờ (UNIX timestamp):

```bash
python3 src/upload_facebook.py --video assets/out/clip_001.mp4 --json data/samples/clip_001.json --state SCHEDULED --at 1756400400
```

### 3. Render xong đăng luôn

```bash
python3 src/publish.py --json data/samples/clip_001.json
```

### 4. Lịch mỗi ngày (cron)

```cron
0 7 * * * cd /path/quote-reels-starter && /usr/bin/python3 src/publish.py --json data/queue/today.json >> logs/publish.log 2>&1
```

Muốn kéo hàng từ Google Sheet thì dùng n8n template sẵn:
https://n8n.io/workflows/10122-automate-facebook-reels-publishing-with-google-sheets-and-drive/

## Lưu ý

- Giữ footage thật, đừng gen video AI.
- 1 giọng / 1 template xuyên suốt kênh.
- 3 giây đầu phải đọc được tiêu đề khi tắt tiếng.
- Đăng page phụ 7–14 ngày trước khi đưa sang page chính.
- Không dùng tool giả lập app / cookie / selenium để đăng profile — dễ khóa tài khoản.
- Token hết hạn thì clip render vẫn chạy, chỉ bước upload lỗi.
