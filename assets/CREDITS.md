# Nguồn và giấy phép asset

## Font — `assets/fonts/`

**Be Vietnam Pro** (Black / ExtraBold / SemiBold), giấy phép SIL Open Font License 1.1,
đi kèm trong `assets/fonts/OFL.txt`. Nguồn: https://github.com/bettergui/BeVietnamPro

## Ảnh mẫu — `assets/footage/ocean-sunset.jpg`

Ảnh **chỉ để chạy thử pipeline**, không rõ nguồn gốc/giấy phép nên **đừng dùng để đăng kênh thật**.
Trước khi lên clip chính thức, thay bằng footage bạn có quyền dùng (quay tay, hoặc Unsplash/Pexels
và ghi lại link gốc + giấy phép vào file này). `python3 src/doctor.py` sẽ nhắc khi thư mục footage
chỉ có file mẫu này.

## Nhạc — `assets/music/`

Repo không kèm nhạc. Khi thư mục trống, pipeline tự sinh "brown noise" để test
(`assets/music/_placeholder.m4a`) — **không đăng bản này**. Thả file nhạc có quyền dùng vào
`assets/music/` (mp3/wav/m4a/aac), nhạc đầu tiên theo thứ tự alphabet sẽ được dùng.

## Giọng đọc

`edge-tts` gọi dịch vụ Microsoft Edge TTS (miễn phí, không cần API key). Giọng Vbee/FPT.AI xuất mp3
rồi truyền `--voice <file>`; tự chịu trách nhiệm về quyền sử dụng giọng của nhà cung cấp đó.
