# Runbook — chạy thật lần đầu

Checklist cho lần đầu đăng bằng tài khoản thật. Đi theo đúng thứ tự: mỗi bước đều có cách
kiểm tra trước khi sang bước sau, và **không bước nào ở mục 1–2 gọi mạng**.

## 0. Chuẩn bị `.env`

```bash
cp .env.example .env
```

Tối thiểu để đăng Reels (cross-post để sau):

| Biến | Lấy ở đâu |
|---|---|
| `FB_PAGE_ID` | `python3 src/fbtoken.py --list-pages` (in ra id + page token) |
| `FB_PAGE_ACCESS_TOKEN` | như trên; token ~60 ngày, kiểm hạn ở mục [Preflight](#1-preflight) |
| `FB_APP_ID`, `FB_APP_SECRET` | App Facebook → Settings → Basic (dùng để gia hạn token và `doctor --online`) |

Đổi short-lived token lấy từ Graph Explorer thành long-lived rồi dán lại vào `.env`:

```bash
python3 src/fbtoken.py --short <SHORT_LIVED_TOKEN>
```

Cross-post (chỉ cần khi `crosspost.targets` trong `config.yaml` khác `[]`):
`YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` (scope
`youtube.upload`), `TIKTOK_ACCESS_TOKEN` (scope `video.publish`).

Cảnh báo lỗi (không cấu hình = lỗi im lặng): `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`,
hoặc `NOTIFY_WEBHOOK_URL`. Kiểm tra nhanh: `python3 src/notify.py "Tin nhắn thử"` (thêm
`--dry-run` để chỉ in ra chứ không gửi).

## 1. Preflight

```bash
python3 src/doctor.py --json data/samples/clip_001.json           # phải 0 fail
python3 src/doctor.py --json data/samples/clip_001.json --online   # hỏi Graph hạn/quyền token
```

`--online` gọi `debug_token` (chỉ đọc) và in hạn của token, scope, quyền trên Page; cảnh báo
khi token còn ≤ 7 ngày. Chưa có `.env`/token thì chỉ là `[warn]` (doctor vẫn dùng được trước khi
điền credential); có token mà thiếu `FB_APP_ID`/`FB_APP_SECRET` mới là `[fail]`.
`[warn] ffprobe: thiếu` không phải lỗi — duration được đọc bằng ffmpeg, chỉ chậm hơn.

## 2. Diễn tập — không gọi mạng, không đổi hàng chờ

`--dry-run` **render video thật** (để xem được mp4) rồi in kế hoạch request; không cần
credential, không upload, không ghi log, không chuyển job khỏi `pending/`.

```bash
python3 src/publish.py --json mysclip.json --dry-run --crosspost youtube,tiktok
```

Đọc JSON in ra và kiểm:

- `facebook.duration` trong `video.min_seconds`–`max_seconds` (mặc định 3–90s).
- `facebook.state`, `facebook.caption_chars`, `facebook.first_comment` (rỗng = không comment;
  comment trong clip được ưu tiên hơn template `facebook.first_comment`).
- `facebook.thumb_offset_ms` (mốc lấy cover) và `facebook.quota_remaining`.
- `cover` + `crosspost.<nền tảng>` nếu bật cross-post.

Từng nền tảng chạy riêng cũng được:

```bash
python3 src/upload_facebook.py --video assets/out/mysclip.mp4 --json mysclip.json --dry-run
python3 src/upload_youtube.py  --video assets/out/mysclip.mp4 --json mysclip.json \
    --cover assets/overlays/mysclip_cover.jpg --dry-run
python3 src/upload_tiktok.py   --video assets/out/mysclip.mp4 --json mysclip.json --dry-run
```

## 3. Đăng thật 1 clip

```bash
# đăng thẳng file JSON
python3 src/publish.py --json mysclip.json --no-first-comment

# hoặc qua hàng chờ
python3 src/jobqueue.py add mysclip.json
python3 src/publish.py --queue --max 1
```

`--no-first-comment` cho lần đầu để giảm số thứ có thể sai. `--state DRAFT` nếu muốn thử
mà không lên công khai — docs Reels API chỉ nêu `PUBLISHED` nên DRAFT/SCHEDULED có thể bị
Graph từ chối (script in cảnh báo trước).

## 4. Kiểm ngay sau khi đăng

| Kiểm gì | Ở đâu |
|---|---|
| Bài đã lên | `data/published.json` → entry `state: PUBLISHED`, `url: https://www.facebook.com/reel/<id>` |
| Comment đầu | `first_comment.id` trong entry đó; mở reel xem comment nằm dưới bài (`--no-first-comment` thì không có) |
| Ảnh cover | khung hình ở `cover.at_seconds`; log có `cover <id>_cover.jpg` |
| Hạn mức | dòng log `quota còn N Reels trong 24h` (đếm từ `data/published.json`, trần `facebook.daily_limit`) |
| Cross-post | entry `CROSSPOST_YOUTUBE` / `CROSSPOST_TIKTOK`. TikTok app chưa audit chỉ đăng được `SELF_ONLY` (riêng tư) |
| Hiệu quả | sau vài giờ: `python3 src/insights.py refresh` rồi `python3 src/insights.py summary` |

Nếu bật cảnh báo, thêm `notify.on_success: true` để nhận tin mỗi lần đăng thành công.

## 5. Khi có lỗi

- Clip lỗi nằm ở `data/queue/failed/` kèm `_queue.last_error`; đồng thời gửi cảnh báo.
- Batch tự dừng sau **2 lỗi liên tiếp giống nhau** (thường là token hỏng) để không đốt cả hàng chờ.
- Sửa xong đưa lại hàng chờ: `python3 src/jobqueue.py move data/queue/failed/x.json --to pending`.

## 6. Bẫy đã biết

- **Phụ đề** chỉ có khi voice do edge-tts đọc (`subtitles.require_timings: true`); voice ngoài
  (Vbee/FPT qua `--voice`) không có timing nên phụ đề tự bỏ qua.
- **Nhạc/nền**: `assets/music/` trống thì pipeline tự sinh tiếng ồn placeholder;
  `assets/footage/` chỉ có 1 ảnh mẫu → mọi clip cùng một nền. Thả file thật vào rồi chạy
  `./scripts/prepare_footage.sh`.
- **Xoá `data/published.json`** là mất luôn bộ đếm hạn mức 24h và chống trùng nội dung.
- **TikTok**: app chưa được duyệt thì mọi bài ở dạng riêng tư, dù `TIKTOK_PRIVACY_LEVEL` đặt gì.
- **Phụ đề và overlay**: overlay tự chừa dải phụ đề (`subtitles.margin_v` + `font_size` +
  `overlay_gap`). Đổi `margin_v` xuống thấp hơn `safe_zone.bottom` sẽ đẩy phụ đề vào vùng UI
  dưới của Facebook.
