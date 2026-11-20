# KEY SHOP BOT (Telegram) — nối với tool sunviw

Bot bán key: nạp tiền bằng QR, **mua key do chính tool sunviw sinh** (cùng hệ key với key tạo ở panel), gifcode, feedback, CSKH, thông báo toàn bot (broadcast), admin phụ có phân quyền.
Mọi nút bấm **sửa thẳng vào tin nhắn hiện tại**, không gửi tin mới.

```
 Telegram ⇄ bot.py ──(HTTPS + ký HMAC, đường bí mật)──► sunviw.py (panel /api/admin)
   khách        └ shop.db (số dư, đơn nạp, gifcode…)       └ sinh key · giá · link game · cấu hình · doanh thu Tele · admin phụ
```

Hai file chạy **tách nhau**: `sunviw.py` (server tool, đã sửa) và thư mục bot này.

---

## 1. Cài đặt nhanh (3 bước)

**① Server sunviw** — đặt biến môi trường (Railway → Variables), rồi redeploy `sunviw.py`:

| Biến | Ý nghĩa |
|---|---|
| `SUNVIW_BOT_SECRET` | **Bắt buộc.** Chuỗi ngẫu nhiên dài ≥ 24 ký tự. Không đặt = mọi đường của bot đều **404** như không tồn tại. Tạo: `python -c "import secrets;print(secrets.token_urlsafe(40))"` |
| `SUNVIW_BOT_IPS` | *Khuyên dùng.* IP của VPS chạy bot (nhiều IP cách nhau dấu phẩy). Có đặt thì IP khác gọi vào cũng **404**. |
| `SUNVIW_BOT_SKEW` | (tuỳ chọn) lệch giờ cho phép, mặc định 90 giây — nhớ giữ đồng hồ VPS đúng giờ. |

**② Bot** — sửa `config.env`:

```
BOT_TOKEN=...                      # token mới từ @BotFather
SUNVIW_URL=https://ten-app.up.railway.app   # BẮT BUỘC https (http chỉ cho localhost)
SUNVIW_BOT_SECRET=...              # GIỐNG HỆT biến ở server sunviw
ADMIN_CHAT_IDS=8764934889          # ID đầu = admin chính
```

**③ Chạy:**

```
pip install -r requirements.txt
python bot.py
```

Cần Python 3.9+. Chạy 24/7 thì đưa lên VPS (`screen` / `pm2` / systemd). **Giữ nguyên file `shop.db` cũ của bạn** cạnh `bot.py` — bot tự nâng cấp (chỉ thêm cột / bảng, không mất số dư, đơn nạp, gifcode, ưu đãi). Gói ZIP này cố ý **không** kèm `shop.db`.

Lần chạy đầu bot tự đẩy cấu hình trong `config.env` lên panel (chỉ điền ô còn trống). Sau đó **chỉnh ở panel — panel được ưu tiên**; ô để trống ở panel = dùng giá trị `config.env`.

---

## 2. Mua key hoạt động thế nào

- Khách chọn game → gói → xác nhận. Bot **giữ tiền** trong DB, gọi sunviw sinh key (kèm mã đơn duy nhất), rồi gửi:
  **key · thời hạn (30 ngày, tính từ lần dùng đầu tiên) · link game bên dưới** (kèm nút "🎮 Mở game").
- **Game OFF = biến mất khỏi Mua Key.** Chỉ cần **1 bàn** của game bị OFF server (vd *off bàn MD5 LC* ⇒ LC79 và Auto cược LC đều ẩn) là cả game ẩn, cho tới khi mọi bàn của game online lại. Bot đồng bộ mỗi 6 giây; khách đang xem màn hình cũ mà bấm mua thì sunviw từ chối → bot **tự hoàn tiền**.
- **Mất liên lạc sunviw** (sập mạng, sai secret…): bot tự **ẩn Mua Key** sau 45 giây; đơn đang dở được giữ tiền và **thử lại đúng mã đơn đó** (sunviw không bao giờ tạo trùng key) rồi tự gửi key / tự hoàn tiền. Kẹt > 10 phút thì báo admin chính.
- Giá do sunviw giữ (panel chỉnh). Ưu đãi sự kiện + gifcode giảm giá vẫn hoạt động như cũ — tính trên giá gốc của sunviw. Gifcode tặng key (`/gifcode key lc79 30d`) cũng do sunviw sinh và **không tính doanh thu**.
- **Doanh thu ở panel chỉ tính key bán qua Tele** (đúng số khách trả). Key tạo ở panel, key tặng gifcode **không** tính. Mỗi giao dịch Tele được báo về panel (tab Doanh thu → "Giao dịch bot gần đây").

## 3. Feedback

Khách bấm **Feedback → chọn game → gửi ảnh + nội dung** → admin duyệt. Bài đăng lên box giữ **nguyên định dạng cũ**, thêm bên dưới:

```
━━━━━━━━━━━━━━━━━━
🛒 Mua key tại @tenbot
🎮 Link game LC79 VIP:
https://...            ← link do admin chính chỉnh ở panel
```

## 4. Admin chính & admin phụ

| Vai trò | Cách có | Quyền |
|---|---|---|
| **Admin chính** | ID đầu trong `ADMIN_CHAT_IDS` (hoặc ô "Chat ID admin chính" ở panel → Bot Tele) | Toàn quyền |
| **Admin phụ** | Admin chính tạo **key admin phụ** ở panel. Người đó nhập **key + tên + Chat ID của họ** (lấy bằng lệnh `/id` trong bot) ở trang panel | Mặc định: **duyệt đơn nạp · duyệt feedback · broadcast**. Admin chính **tích thêm** quyền cho từng người |

Khi admin phụ `/start`, bot hiện **"Vai trò: Admin phụ · TÊN"** và nút **Bảng Admin** chỉ chứa chức năng họ có quyền. Đổi quyền ở panel có hiệu lực **trong vài giây**, không cần đăng nhập lại / khởi động lại bot.

Danh sách quyền (panel → tab Key → mở key admin phụ → mục "Quyền admin phụ" → tích → **Lưu quyền**):

| Quyền | Dùng ở |
|---|---|
| Duyệt đơn nạp tiền · Duyệt feedback · Broadcast *(mặc định)* | bot (+ panel với broadcast) |
| Tạo / xóa gifcode · Tạo / sửa ưu đãi | bot |
| Quản lý MỌI key · Duyệt/ban thiết bị · Bật/tắt server từng bàn · Gửi thông báo tới tool · Xem doanh thu · Xem/dừng auto cược · Cấu hình bot (giá, link, CSKH, box) | panel |

Luôn **chỉ admin chính** làm được: tạo key admin phụ, cấp / thu hồi quyền, đổi thông tin ngân hàng · danh sách Chat ID admin · QR động.

## 5. Thông báo toàn server (Broadcast)

- **Trong bot:** *Bảng Admin → 📢 Thông Báo Toàn Bot* (hoặc `/broadcast Nội dung`) → xem trước → xác nhận → gửi tới tất cả khách (~20 tin/giây, khách đã chặn bot tự bị bỏ qua lần sau).
- **Ở panel sunviw:** tab **Broadcast** → soạn → bot nhận trong vài giây và gửi; kết quả (đã gửi / lỗi) hiện lại ở panel.
- Nội dung là chữ thuần (mã HTML không chạy được), tối đa 3500 ký tự.

## 6. Panel sunviw (admin chính) — các mục mới

- **Bot Tele:** bật/tắt bán từng game, đổi tên, **giá gốc từng gói** (1 · 3 · 7 · 30 · 60 · 90 ngày; giá 0 = không bán gói đó), **URL link game**, và cấu hình bot đầy đủ: Chat ID admin, @CSKH, box feedback/thông báo, ẩn tên khách, nạp tối thiểu/tối đa, ngân hàng + QR động (mục nhạy cảm chỉ admin chính sửa).
- **Broadcast:** soạn + lịch sử.
- **Doanh thu:** chỉ doanh thu bán qua Tele.
- **Key (admin phụ):** hộp tích quyền từng người.

## 7. Lệnh trong bot

```
/admin                          bảng admin (nút bấm)
/game  hoặc /stock              trạng thái từng game: online / OFF bàn nào, giá gói, kết nối sunviw
/pending                        đơn nạp & feedback chờ duyệt (theo quyền)
/gifcode money 50000 [lượt] [MÃ]       (quyền gifcode)   dep · keypct · keyflat · money · key lc79 30d
/promo keypct 20 game=lc79 goi=30d han=2d   (quyền ưu đãi)
/broadcast Nội dung             (quyền broadcast)
/id                             xem ID của bạn / ID chat (admin phụ dùng để lấy Chat ID)
```

`/addgame`, `/setprice`, `/addkey` đã chuyển sang panel sunviw (bot báo hướng dẫn).

## 8. Bảo mật

- Mọi đường API của bot nằm ở **đường dẫn bí mật** suy ra từ `SUNVIW_BOT_SECRET`; mỗi request **ký HMAC-SHA256** (method, op, thời gian, nonce một lần, hash nội dung), **phản hồi cũng được ký** — bot bỏ phản hồi giả. Sai bất cứ gì (secret, chữ ký, giờ, IP, lặp nonce) ⇒ **404** y như đường không tồn tại, đồng thời cộng điểm khóa IP chống dò quét.
- Bot **từ chối chạy qua `http://`** (trừ localhost) để secret + key không đi trần trên mạng.
- Key không bao giờ đăng lên box. Gifcode chống dò (sai 5 lần / 10 phút khóa tạm).
- Mọi thao tác admin trong bot kiểm tra quyền **tại thời điểm bấm** (không tin nút cũ trong lịch sử chat).
- Nếu token bot từng bị lộ (gửi vào chat…), vào @BotFather → `/revoke` để lấy token mới rồi dán vào `config.env`.
- Nếu thấy log `Mất liên lạc sunviw … HTTP 404`: sai `SUNVIW_BOT_SECRET`, sai `SUNVIW_BOT_IPS`, hoặc đồng hồ VPS lệch > 90 giây.

## 9. Luồng nạp tiền (không đổi)

1. Khách **Nạp Tiền** → chọn mức / nhập số → bot hiện **QR** + nút *Tôi đã chuyển khoản* / *Hủy đơn*.
2. *Tôi đã chuyển khoản* → đơn gửi cho admin có quyền duyệt nạp (admin chính + admin phụ được cấp quyền) kèm nút **Duyệt / Từ chối**.
3. Duyệt → tiền cộng ngay, tin "vui lòng đợi" của khách tự đổi thành "Nạp thành công", đăng thông báo lên box (tên che bớt).
