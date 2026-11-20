# Đưa KEY SHOP BOT lên Railway

Bot là **worker** (Telegram long-polling) — không cần mở cổng, không cần domain.

## 1. Các file trong thư mục

| File | Việc |
|---|---|
| `bot.py`, `sunclient.py`, `requirements.txt`, `qr.jpg` | Code bot (giữ nguyên) |
| `Dockerfile` | Đóng gói bot (Python 3.12) — Railway tự dùng |
| `railway.json` | Báo Railway build bằng Dockerfile, tự khởi động lại khi bot chết |
| `start.sh` | Chọn chỗ lưu `shop.db` trên Volume rồi chạy `bot.py` |
| `.gitignore` / `.dockerignore` | Chặn `config.env`, `shop.db` bị đưa lên GitHub / vào image |
| `railway.env.example` | Danh sách biến cần dán vào Railway (thay cho `config.env`) |

> **Không đưa `config.env` lên GitHub** (có token bot). Trên Railway mọi cấu hình đặt ở **Variables**.

## 2. Đưa code lên Railway

1. Tạo repo GitHub **private**, đẩy cả thư mục này lên (`config.env` đã bị `.gitignore` chặn).
2. Railway → **New Project → Deploy from GitHub repo** → chọn repo.
3. Chưa cần bấm gì thêm — làm tiếp bước 3 và 4 rồi để nó deploy.

(Không dùng GitHub: cài Railway CLI, đứng trong thư mục này gõ `railway login`, `railway link`, `railway up`.)

## 3. Gắn Volume (BẮT BUỘC — giữ số dư khách)

Ổ đĩa của Railway **bị xóa mỗi lần deploy**. `shop.db` chứa số dư, đơn nạp, gifcode, ưu đãi nên phải nằm trên Volume:

1. Mở service bot → **Settings → Volumes → New Volume** (hoặc chuột phải vào service trên canvas → Attach volume).
2. **Mount path:** `/data`
3. Có biến `DB_PATH=/data/shop.db` (đã có trong `railway.env.example`). Nếu quên đặt, `start.sh` tự dùng `RAILWAY_VOLUME_MOUNT_PATH`.

Xem log lúc khởi động, phải thấy dòng `[start] shop.db: /data/shop.db`. Thấy dòng `CANH BAO` nghĩa là chưa gắn Volume.

## 4. Đặt biến môi trường

Service bot → **Variables → RAW Editor** → dán nội dung `railway.env.example` (đã điền các chỗ `<...>`):

| Biến | Giá trị |
|---|---|
| `BOT_TOKEN` | Token từ @BotFather (nếu từng lộ: `/revoke` lấy token mới) |
| `SUNVIW_URL` | Địa chỉ **https** của sunviw, vd `https://ten-app.up.railway.app` |
| `SUNVIW_BOT_SECRET` | **Giống hệt** biến cùng tên ở service sunviw (≥ 24 ký tự) |
| `ADMIN_CHAT_IDS` | ID đầu = admin chính |
| `DB_PATH` | `/data/shop.db` |
| còn lại | Ngân hàng, box feedback, nạp tối thiểu... như `config.env` cũ |

Biến đổi xong Railway tự deploy lại.

## 5. Kiểm tra sau khi chạy

Log (service → Deployments → View logs) phải có:

```
[start] shop.db: /data/shop.db
Bot đang chạy...
```

Rồi vào Telegram `/start` → `/game`: dòng **Kết nối sunviw** phải là ✅ ổn.

## 6. Lỗi thường gặp

| Triệu chứng | Nguyên nhân |
|---|---|
| `Mất liên lạc sunviw … HTTP 404` | Sai `SUNVIW_BOT_SECRET`, hoặc server sunviw đang đặt `SUNVIW_BOT_IPS` mà IP của Railway không nằm trong đó, hoặc lệch giờ > 90 giây |
| `Chưa đặt SUNVIW_URL / SUNVIW_BOT_SECRET` | Thiếu biến, hoặc secret < 24 ký tự |
| `SUNVIW_URL phải là HTTPS` | Dùng `http://` — đổi sang `https://` |
| `Conflict: terminated by other getUpdates request` | Có **2 bản bot cùng token đang chạy** (vd máy bạn + Railway). Tắt bản còn lại |
| Deploy xong mất số dư | Chưa gắn Volume (mục 3) |
| `Chưa có BOT_TOKEN` | Chưa đặt `BOT_TOKEN` ở Variables |

**Lưu ý `SUNVIW_BOT_IPS`:** IP gửi ra của Railway không cố định (trừ gói có Static Outbound IP). Nếu bật lọc IP ở sunviw, bot sẽ bị 404 mỗi khi IP đổi — nên **để trống** `SUNVIW_BOT_IPS`, hệ ký HMAC + đường dẫn bí mật đã đủ bảo vệ.

## 7. Cập nhật bot về sau

Sửa code → `git push` → Railway tự build và deploy lại. Dữ liệu trên Volume vẫn còn.
