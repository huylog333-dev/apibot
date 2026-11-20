# -*- coding: utf-8 -*-
"""
Client gọi API BOT của sunviw (đường dẫn bí mật + ký HMAC-SHA256, phản hồi cũng được ký).

  · Chỉ dùng thư viện chuẩn (urllib) -> không cần cài thêm gì. Gọi trong thread riêng (asyncio.to_thread) nên không chặn bot.
  · Đường dẫn bí mật + chữ ký đều suy ra từ SUNVIW_BOT_SECRET (đặt GIỐNG HỆT ở server sunviw và config.env của bot).
  · Bắt buộc HTTPS (trừ localhost) -> không ai nghe lén / chen giữa được; phản hồi sai chữ ký bị bỏ.
  · Mọi lỗi mạng / 404 (sai secret / sai IP / lệch giờ) / 5xx đều ném ApiDown -> bot coi như "chưa liên lạc được" (KHÔNG tự coi là thất bại).
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

# mã lỗi API trả về mà KẾT LUẬN là thất bại thật (không retry): bot hoàn tiền ngay
DEFINITIVE = frozenset({"bad_ref", "bad_game", "bad_args", "ref_conflict", "game_off", "no_package", "price_changed", "full"})
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class ApiDown(Exception):
    """Không liên lạc / không xác thực được API (mạng lỗi, 404, 429, 5xx, sai chữ ký phản hồi...). Có thể thử lại."""


def slug_of(secret):
    return "b" + hmac.new(secret.encode("utf-8"), b"sunviw-bot-path-v1", hashlib.sha256).hexdigest()[:31]


class SunClient:
    def __init__(self, base_url, secret, timeout=12, allow_http=False):
        self.base = (base_url or "").strip().rstrip("/")
        self.secret = (secret or "").strip()
        self.timeout = timeout
        self.ready = False
        self.why = ""
        if not self.base or len(self.secret) < 24:
            self.why = "Chưa đặt SUNVIW_URL / SUNVIW_BOT_SECRET (>= 24 ký tự) trong config.env"
            return
        u = urllib.parse.urlsplit(self.base)
        if u.scheme not in ("http", "https") or not u.hostname:
            self.why = "SUNVIW_URL không hợp lệ (cần dạng https://ten-mien-cua-ban)"
            return
        if u.scheme == "http" and u.hostname not in LOCAL_HOSTS and not allow_http:
            self.why = "SUNVIW_URL phải là HTTPS (http chỉ cho localhost) — bí mật + key không được đi trên đường truyền không mã hoá"
            return
        self.slug = slug_of(self.secret)
        self.ready = True

    # ---- ký + gửi (đồng bộ) ----
    def _call_sync(self, op, body=None):
        if not self.ready:
            raise ApiDown(self.why)
        method = "POST" if body is not None else "GET"
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if body is not None else b""
        ts = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        msg = "\n".join((method, op, ts, nonce, hashlib.sha256(raw).hexdigest()))
        sig = hmac.new(self.secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            f"{self.base}/{self.slug}/{op}", data=raw if method == "POST" else None, method=method,
            headers={"X-B-Ts": ts, "X-B-Nonce": nonce, "X-B-Sig": sig, "Content-Type": "application/json",
                     "User-Agent": "keyshop-bot/1", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data, status, rsig = r.read(2_000_000), r.status, r.headers.get("X-B-Rsig", "")
        except urllib.error.HTTPError as e:      # 404 (sai secret/IP/giờ — server không nói rõ), 429, 5xx
            raise ApiDown(f"HTTP {e.code}") from None
        except Exception as e:  # noqa: BLE001
            raise ApiDown(f"{type(e).__name__}") from None
        want = hmac.new(self.secret.encode("utf-8"), ("R\n" + nonce + "\n" + hashlib.sha256(data).hexdigest()).encode("utf-8"), hashlib.sha256).hexdigest()
        if status != 200 or not hmac.compare_digest(want, rsig):
            raise ApiDown("phản hồi sai chữ ký")           # có kẻ chen giữa / sai secret -> bỏ, KHÔNG tin nội dung
        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise ApiDown("phản hồi không phải JSON") from None
        if not isinstance(obj, dict):
            raise ApiDown("phản hồi lạ")
        return obj

    async def call(self, op, body=None):
        return await asyncio.to_thread(self._call_sync, op, body)
