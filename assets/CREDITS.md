# Nguồn và giấy phép asset

## Font — `assets/fonts/`

**Be Vietnam Pro** (Black / ExtraBold / SemiBold), giấy phép SIL Open Font License 1.1,
đi kèm trong `assets/fonts/OFL.txt`. Nguồn: https://github.com/bettergui/BeVietnamPro

## Ảnh/nền — `assets/footage/`

Ảnh mẫu `ocean-sunset.jpg` **chỉ để chạy thử pipeline**, không rõ nguồn gốc/giấy phép nên
**đừng dùng để đăng kênh thật**. Thay bằng footage bạn có quyền dùng: tự quay, hoặc tải từ
nguồn có giấy phép rõ ràng và **ghi lại link gốc + giấy phép vào chính file này**.

Nguồn hay dùng (luôn mở trang giấy phép của từng file trước khi dùng cho kênh kiếm tiền):

| Nguồn | Loại | Ghi chú |
|---|---|---|
| https://www.pexels.com/videos/ · /photos/ | Video + ảnh | Giấy phép Pexels: dùng thương mại được, không cần ghi nguồn, không được bán lại nguyên bản |
| https://pixabay.com/videos/ · /photos/ | Video + ảnh | Giấy phép Pixabay (kiểu CC0); vài file đánh dấu "Pixabay Content License" |
| https://unsplash.com | Ảnh | Giấy phép Unsplash; không dùng để tạo sản phẩm cạnh tranh trực tiếp |
| https://coverr.co | Video | Miễn phí cho thương mại, kiểm tra điều kiện từng clip |
| https://mixkit.co | Video + nhạc | Miễn phí, có mục Free Stock Video và Free Music |
| Tự quay bằng điện thoại | Video | An toàn nhất về bản quyền; quay dọc 9:16 là tốt nhất |

Video tải về nên chuẩn hoá về 1080×1920/30fps trước khi dùng:

```bash
./scripts/prepare_footage.sh   # ghi ra assets/footage/_normalized/
```

Ảnh tĩnh và video trộn lẫn được — pipeline tự nhận đuôi file.

## Nhạc — `assets/music/`

Repo không kèm nhạc. Khi thư mục trống, pipeline tự sinh "brown noise" để test
(`assets/music/_placeholder.m4a`) — **không đăng bản này**. Thả file nhạc có quyền dùng vào
`assets/music/` (mp3/wav/m4a/aac).

| Nguồn | Ghi chú |
|---|---|
| https://www.youtube.com/audiolibrary | Nhạc + hiệu ứng miễn phí của YouTube; một số bài yêu cầu ghi nguồn trong mô tả |
| https://pixabay.com/music/ | Giấy phép Pixabay, không cần ghi nguồn |
| https://freemusicarchive.org | Nhiều giấy phép khác nhau (CC0/CC BY/CC BY-NC) — **đọc kỹ từng bài**, tránh CC BY-NC nếu kiếm tiền |
| https://mixkit.co/free-stock-music/ | Miễn phí cho thương mại |
| https://incompetech.com | CC BY — bắt buộc ghi nguồn |

**Cảnh báo bản quyền:** nhạc trong video Reels/Shorts có thể bị Content ID của Facebook/YouTube
nhận diện. Ưu tiên CC0/Public Domain, hoặc nhạc của chính bạn; nếu bắt buộc ghi nguồn thì ghi
vào caption hoặc comment đầu tiên.

## Nền/nhạc được chọn thế nào

Pipeline **luân phiên** file trong 2 thư mục trên để clip nào cũng khác nhau (config `assets.*`):

```yaml
assets:
  pick: rotate          # first | rotate | random
  recent: 3             # (chỉ random) tránh lặp lại 3 file vừa dùng
  segment_seconds: 10   # đổi nền sau mỗi 10s trong CÙNG một clip; 0 = mỗi clip 1 nền
```

- Vị trí luân phiên lưu ở `data/asset_rotation.json` (không commit) nên lần chạy sau không lặp lại.
- Muốn clip trông khác nhau thật: **≥ 6 ảnh/nền** và **≥ 3 bài nhạc**. Ít hơn thì `doctor.py`
  vẫn chạy được nhưng sẽ cảnh báo `độ phong phú footage` / `độ phong phú nhạc`.
- Một clip dài ~40s với `segment_seconds: 10` sẽ dùng 4 nền khác nhau; nếu thư mục có ít hơn thì
  pipeline dùng tối đa số file đang có.

## Giọng đọc

`edge-tts` gọi dịch vụ Microsoft Edge TTS (miễn phí, không cần API key). Giọng Vbee/FPT.AI xuất mp3
rồi truyền `--voice <file>`; tự chịu trách nhiệm về quyền sử dụng giọng của nhà cung cấp đó.
