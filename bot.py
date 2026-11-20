# -*- coding: utf-8 -*-
"""
KEY SHOP BOT - Telegram bot bán key / nạp tiền / gifcode / feedback / CSKH
Chạy:  pip install -r requirements.txt  ->  python bot.py
Cấu hình trong file config.env (+ tab "Bot Tele" ở panel sunviw: giá, link game, CSKH, box, admin... — panel được ưu tiên)

KEY được SINH BỞI TOOL SUNVIW (cùng hệ key với panel) qua API ẩn + ký HMAC — xem sunclient.py.
  · Game nào có BẤT KỲ bàn nào OFF server  ->  tự biến mất khỏi "Mua Key" cho tới khi bàn đó ONLINE lại.
  · Admin phụ = key admin phụ (admin chính tạo ở panel) + Chat ID của họ; admin chính cấp thêm quyền ngay trên panel.
"""
import asyncio
import hashlib
import html
import logging
import os
import random
import re
import secrets
import sqlite3
import string
import time
import unicodedata
import urllib.request
from urllib.parse import quote
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as K
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, ChatMigrated, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, "config.env"))

from sunclient import DEFINITIVE, ApiDown, SunClient  # noqa: E402

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("keyshop")

# ============================== CẤU HÌNH ==============================


def _ints(s):
    return [int(x) for x in re.findall(r"-?\d+", s or "")]


def _chat_val(s):
    """'@kenh' | '-1001234567890' | '' -> str | int | None."""
    s = str(s if s is not None else "").strip()
    return s if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", s) else (int(s) if re.fullmatch(r"-?\d+", s) else None)


TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Kết nối tool sunviw: key bán ra do sunviw sinh. SUNVIW_BOT_SECRET phải GIỐNG HỆT biến SUNVIW_BOT_SECRET ở server sunviw.
SUNVIW_URL = os.getenv("SUNVIW_URL", "").strip()
SUNVIW_BOT_SECRET = os.getenv("SUNVIW_BOT_SECRET", "").strip()
api = SunClient(SUNVIW_URL, SUNVIW_BOT_SECRET, allow_http=os.getenv("SUNVIW_ALLOW_HTTP", "0").strip() == "1")

# Giá trị mặc định (config.env). Panel sunviw (tab "Bot Tele") GHI ĐÈ khi có -> xem apply_cfg().
ENV_CFG = {
    "bot_name": os.getenv("BOT_NAME", "KEY SHOP").strip(),
    "admin_ids": _ints(os.getenv("ADMIN_CHAT_IDS")),  # ID đầu tiên = admin chính; các ID sau = admin phụ cũ (quyền mặc định)
    "cskh_username": os.getenv("CSKH_USERNAME", "").strip().lstrip("@"),
    "cskh_id": os.getenv("CSKH_ID", "").strip(),
    "bank_name": os.getenv("BANK_NAME", "MB Bank"),
    "bank_code": os.getenv("BANK_CODE", "MB"),
    "bank_account": os.getenv("BANK_ACCOUNT", "666666660108"),
    "bank_owner": os.getenv("BANK_OWNER", "VU DUY MANH"),
    "min_deposit": int(os.getenv("MIN_DEPOSIT", "10000")),
    "max_deposit": int(os.getenv("MAX_DEPOSIT", "50000000")),
    "use_dynamic_qr": os.getenv("USE_DYNAMIC_QR", "0").strip() == "1",
    "feedback_chat": os.getenv("FEEDBACK_CHAT_ID", "").strip(),
    "feedback_thread": os.getenv("FEEDBACK_THREAD_ID", "").strip(),
    "feedback_mask": os.getenv("FEEDBACK_MASK_NAME", "0").strip() == "1",
    "notify_chat": os.getenv("NOTIFY_CHAT_ID", "").strip(),
    "notify_thread": os.getenv("NOTIFY_THREAD_ID", "").strip(),
}
QR_FILE = os.path.join(BASE, os.getenv("QR_FILE", "qr.jpg"))
FB_MAX = 600  # độ dài tối đa nội dung feedback
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE, "shop.db"))

# Các biến dưới đây do apply_cfg() điền (config.env + panel):
BOT_NAME = ""
ADMIN_IDS, MAIN_ADMIN = [], None
CSKH_USERNAME = CSKH_ID = ""
BANK_NAME = BANK_CODE = BANK_ACCOUNT = BANK_OWNER = ""
MIN_DEPOSIT, MAX_DEPOSIT, USE_DYNAMIC_QR = 10000, 50000000, False
FEEDBACK_CHAT = FEEDBACK_THREAD_ID = None
FEEDBACK_MASK_NAME = False
NOTIFY_CHAT = NOTIFY_THREAD_ID = None


def apply_cfg(remote=None):
    """Gộp config.env (mặc định) + cấu hình panel sunviw (ưu tiên nếu có, ô trống = dùng config.env) -> biến toàn cục."""
    global BOT_NAME, ADMIN_IDS, MAIN_ADMIN, CSKH_USERNAME, CSKH_ID, BANK_NAME, BANK_CODE, BANK_ACCOUNT, BANK_OWNER
    global MIN_DEPOSIT, MAX_DEPOSIT, USE_DYNAMIC_QR, FEEDBACK_CHAT, FEEDBACK_THREAD_ID, FEEDBACK_MASK_NAME
    global NOTIFY_CHAT, NOTIFY_THREAD_ID
    r = remote if isinstance(remote, dict) else {}

    def pick(k):
        v = r.get(k)
        return ENV_CFG[k] if v is None or v == "" or v == [] else v

    def num(k, lo, default):
        try:
            n = int(pick(k))
            return n if n >= lo else default
        except (TypeError, ValueError):
            return default

    BOT_NAME = str(pick("bot_name"))[:40] or "KEY SHOP"
    ids = []
    for x in pick("admin_ids"):
        try:
            if int(x) not in ids:
                ids.append(int(x))
        except (TypeError, ValueError):
            pass
    ADMIN_IDS = ids
    MAIN_ADMIN = ids[0] if ids else None
    CSKH_USERNAME = str(pick("cskh_username")).strip().lstrip("@")
    CSKH_ID = str(pick("cskh_id")).strip() or (str(MAIN_ADMIN) if MAIN_ADMIN else "")
    BANK_NAME, BANK_CODE = str(pick("bank_name")), str(pick("bank_code"))
    BANK_ACCOUNT, BANK_OWNER = str(pick("bank_account")), str(pick("bank_owner"))
    MIN_DEPOSIT = num("min_deposit", 1000, 10000)
    MAX_DEPOSIT = max(num("max_deposit", 1000, 50000000), MIN_DEPOSIT)
    USE_DYNAMIC_QR = True  # luôn dùng QR động (tự điền số tiền + nội dung CK); qr.jpg tĩnh chỉ là phương án dự phòng
    FEEDBACK_CHAT = _chat_val(pick("feedback_chat"))
    FEEDBACK_THREAD_ID = (_ints(str(pick("feedback_thread"))) or [None])[0]  # chỉ cần nếu box bật Topics
    FEEDBACK_MASK_NAME = bool(pick("feedback_mask"))  # True = ẩn tên khách: D***
    nc = _chat_val(pick("notify_chat"))
    if nc is not None:  # box thông báo nạp / mua riêng; không điền = dùng chung box feedback
        NOTIFY_CHAT, NOTIFY_THREAD_ID = nc, (_ints(str(pick("notify_thread"))) or [None])[0]
    else:
        NOTIFY_CHAT, NOTIFY_THREAD_ID = FEEDBACK_CHAT, FEEDBACK_THREAD_ID
    if "build_prompts" in globals():
        build_prompts()


apply_cfg()

VN = timezone(timedelta(hours=7))
LINE = "━━━━━━━━━━━━━━━━━━"
QUICK_AMOUNTS = [10000, 20000, 50000, 100000, 200000, 500000]

# ============================== DATABASE ==============================

db = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")


@contextmanager
def tx():
    """Giao dịch an toàn (chống mua trùng key / cộng tiền 2 lần)."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, name TEXT, username TEXT,
    balance INTEGER NOT NULL DEFAULT 0, total_deposit INTEGER NOT NULL DEFAULT 0,
    created INTEGER);
CREATE TABLE IF NOT EXISTS games(
    code TEXT PRIMARY KEY, name TEXT NOT NULL, online INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS packages(
    id INTEGER PRIMARY KEY AUTOINCREMENT, game TEXT NOT NULL, code TEXT NOT NULL,
    price INTEGER NOT NULL, UNIQUE(game, code));
CREATE TABLE IF NOT EXISTS keys(
    id INTEGER PRIMARY KEY AUTOINCREMENT, game TEXT NOT NULL, pkg_code TEXT NOT NULL,
    key_text TEXT NOT NULL UNIQUE, sold_to INTEGER, sold_at INTEGER);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
    status TEXT NOT NULL, code TEXT, msg_id INTEGER, created INTEGER, submitted INTEGER,
    handled_by INTEGER, handled_at INTEGER);
CREATE TABLE IF NOT EXISTS giftcodes(
    code TEXT PRIMARY KEY, kind TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 0,
    game TEXT, pkg_code TEXT, max_uses INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0, created_by INTEGER, created INTEGER);
CREATE TABLE IF NOT EXISTS giftcode_uses(
    code TEXT NOT NULL, user_id INTEGER NOT NULL, at INTEGER, PRIMARY KEY(code, user_id));
CREATE TABLE IF NOT EXISTS logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    detail TEXT, extra TEXT, amount INTEGER NOT NULL DEFAULT 0, at INTEGER);
CREATE TABLE IF NOT EXISTS feedback(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS promos(
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, value INTEGER NOT NULL,
    game TEXT, pkg_code TEXT, min_amount INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1, ends INTEGER, created INTEGER);
CREATE INDEX IF NOT EXISTS idx_logs_user ON logs(user_id, id);
CREATE TABLE IF NOT EXISTS purchases(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL,
    game TEXT NOT NULL, pkg_code TEXT NOT NULL, days INTEGER NOT NULL,
    base INTEGER NOT NULL DEFAULT 0, paid INTEGER NOT NULL DEFAULT 0, gift TEXT, src TEXT NOT NULL DEFAULT 'buy',
    status TEXT NOT NULL, key_text TEXT, link TEXT, from_use INTEGER, fail TEXT,
    chat_id INTEGER, msg_id INTEGER, created INTEGER, done INTEGER, tries INTEGER NOT NULL DEFAULT 0, last_try INTEGER,
    alerted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_pur_status ON purchases(status);
"""
)


def _ensure_cols(table, cols):
    """Nâng cấp DB cũ: thêm cột còn thiếu (không mất dữ liệu)."""
    have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
    for name, ddl in cols.items():
        if name not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


_ensure_cols(
    "feedback",
    {"photo_id": "TEXT", "status": "TEXT", "msg_id": "INTEGER", "submitted": "INTEGER",
     "handled_by": "INTEGER", "handled_at": "INTEGER"},
)


db.execute(
    "CREATE TABLE IF NOT EXISTS order_msgs(order_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL)"
)
db.execute("CREATE INDEX IF NOT EXISTS idx_order_msgs ON order_msgs(order_id)")

_ensure_cols(
    "orders",
    {
        "bonus": "INTEGER NOT NULL DEFAULT 0",  # tiền tặng thêm (ưu đãi sự kiện + gifcode) chốt lúc tạo đơn
        "gift": "TEXT",  # gifcode "nạp tặng thêm %" đã dùng cho đơn này
    },
)

# Nâng cấp từ bản cũ (kho key nội bộ): game / gói giờ là bản sao danh mục từ sunviw (đồng bộ mỗi vài giây).
_ensure_cols(
    "games",
    {
        "api": "INTEGER NOT NULL DEFAULT 0",  # 1 = game lấy từ sunviw; 0 = game mẫu cũ (ẩn hẳn khỏi bot)
        "sell": "INTEGER NOT NULL DEFAULT 1",  # panel bật/tắt bán game này
        "link": "TEXT",  # link game (panel chỉnh) — hiện sau khi mua key + dưới bài feedback ở box
        "offt": "TEXT",  # tên các bàn đang OFF server (chỉ admin xem)
    },
)
_ensure_cols("feedback", {"game": "TEXT"})  # game khách chọn khi gửi feedback
_ensure_cols("users", {"blocked": "INTEGER NOT NULL DEFAULT 0"})  # khách đã chặn bot -> broadcast bỏ qua


# ============================== TIỆN ÍCH ==============================


def esc(s):
    return html.escape(str(s or ""))


def money(n):
    return f"{int(n):,}".replace(",", ".") + "đ"


def fmt_time(ts):
    return datetime.fromtimestamp(ts, VN).strftime("%H:%M %d/%m")


DAY_LABEL = {1: "1 Ngày", 3: "3 Ngày", 7: "1 Tuần", 30: "1 Tháng", 60: "2 Tháng", 90: "3 Tháng"}


def pkg_label(code):
    """'30d' -> '1 Tháng' · '1d' -> '1 Ngày' · '12h' -> '12 Giờ' (gói do sunviw đặt tên theo số ngày)."""
    m = re.fullmatch(r"(\d+)([hd])", (code or "").lower())
    if not m:
        return (code or "").upper()
    n = int(m.group(1))
    if m.group(2) == "h":
        return f"{n} Giờ"
    return DAY_LABEL.get(n, f"{n} Ngày")


def pkg_days(code):
    """'30d' -> 30. Gói không phải dạng ngày -> None."""
    m = re.fullmatch(r"(\d+)d", (code or "").lower())
    return int(m.group(1)) if m else None


def parse_amount(s):
    """'50000' / '50.000' / '50k' / '1tr' -> int. Sai định dạng -> None."""
    s = (s or "").lower().replace("đ", "").replace("d", "").replace(" ", "").replace(",", "").replace(".", "")
    m = re.fullmatch(r"(\d+)(k|tr)?", s)
    if not m:
        return None
    n = int(m.group(1))
    return n * {"k": 1000, "tr": 1_000_000, None: 1}[m.group(2)]


def gen_code(prefix="GC", n=8):
    return prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


# ---------- Vai trò & quyền ----------
# Admin chính  = ID đầu tiên trong danh sách admin (config.env hoặc panel). Có MỌI quyền.
# Admin phụ    = người dùng KEY admin phụ (admin chính tạo ở panel sunviw) + đã khai Chat ID khi nhập key. Quyền do admin chính tích ở panel
#                (mặc định: duyệt đơn nạp · duyệt feedback · broadcast) — đổi quyền có hiệu lực trong vài giây, không cần làm gì ở bot.
# Các ID sau ID đầu trong danh sách admin cũ (config.env) vẫn được giữ như admin phụ với quyền mặc định.
PERM_NAME = {
    "deposit": "Duyệt đơn nạp tiền", "feedback": "Duyệt feedback", "broadcast": "Thông báo toàn bot (broadcast)",
    "gift": "Tạo / xóa gifcode", "promo": "Tạo / sửa ưu đãi",
}
BOT_PERMS = tuple(PERM_NAME)  # các quyền có tác dụng ở bot (các quyền còn lại của admin chính chỉ có ở panel)
DEFAULT_PERMS = ("deposit", "feedback", "broadcast")
SUBS = {}  # chat_id -> {"name": ..., "perms": set(...)}  (đồng bộ từ sunviw)
SUBS_TTL = 600  # mất liên lạc sunviw quá lâu -> admin phụ tạm mất quyền (admin chính vẫn dùng được)
SYNC = {"ok_at": 0.0, "seeded": False, "err": ""}
SYNC_FRESH = 45  # quá số giây này chưa đồng bộ được với sunviw -> coi như bảo trì (không bán key)


def api_fresh():
    return api.ready and time.time() - SYNC["ok_at"] <= SYNC_FRESH


def is_main(uid):
    return MAIN_ADMIN is not None and uid == MAIN_ADMIN


def sub_of(uid):
    s = SUBS.get(uid)
    return s if s and time.time() - SYNC["ok_at"] <= SUBS_TTL else None


def role_of(uid):
    if is_main(uid):
        return "main"
    return "sub" if (sub_of(uid) or uid in ADMIN_IDS) else None


def perms_of(uid):
    if is_main(uid):
        return set(BOT_PERMS)
    out = set()
    if uid in ADMIN_IDS:
        out |= set(DEFAULT_PERMS)
    s = sub_of(uid)
    if s:
        out |= set(s["perms"]) & set(BOT_PERMS)
    return out


def has_perm(uid, perm):
    return perm in perms_of(uid)


def is_admin(uid):
    return role_of(uid) is not None


def admins_with(perm):
    """Chat ID mọi admin có quyền `perm` (admin chính + admin phụ)."""
    ids = list(ADMIN_IDS) + [i for i in SUBS if i not in ADMIN_IDS]
    return [i for i in ids if has_perm(i, perm)]


def role_text(uid):
    r = role_of(uid)
    if r == "main":
        return "Admin chính"
    if r == "sub":
        s = sub_of(uid)
        return "Admin phụ" + (f" · {esc(s['name'])}" if s and s.get("name") else "")
    return ""


def cskh_url():
    if CSKH_USERNAME:
        return f"https://t.me/{CSKH_USERNAME}"
    if CSKH_ID:
        return f"tg://user?id={CSKH_ID}"
    return None


def get_user(tg_user):
    db.execute(
        "INSERT INTO users(id,name,username,created) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username, blocked=0",
        (tg_user.id, tg_user.full_name or "Bạn", tg_user.username, int(time.time())),
    )
    return db.execute("SELECT * FROM users WHERE id=?", (tg_user.id,)).fetchone()


def reload_user(uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def add_log(c, uid, kind, detail="", extra="", amount=0):
    c.execute(
        "INSERT INTO logs(user_id,kind,detail,extra,amount,at) VALUES(?,?,?,?,?,?)",
        (uid, kind, detail, extra, amount, int(time.time())),
    )


# --- Game & gói: bản sao danh mục từ sunviw (đồng bộ mỗi vài giây — xem sync_catalog). ---
# games.online = 1 chỉ khi: panel đang bật bán + KHÔNG bàn nào của game bị OFF server + có gói có giá. Game OFF -> biến mất khỏi "Mua Key".
def games_online():
    if not api_fresh():
        return []
    return db.execute("SELECT * FROM games WHERE api=1 AND sell=1 AND online=1 ORDER BY sort, code").fetchall()


def games_all():
    return db.execute("SELECT * FROM games WHERE api=1 ORDER BY sort, code").fetchall()


def game_link(code):
    r = db.execute("SELECT link FROM games WHERE code=?", (code,)).fetchone()
    return (r["link"] or "").strip() if r else ""


# ============================== ƯU ĐÃI ==============================
# kind: key_pct  = giảm X% giá key | key_flat = giảm X đồng mỗi key | dep_pct = nạp tặng thêm X% giá trị nạp
# game / pkg_code = NULL -> áp dụng mọi game / mọi gói.   ends = NULL -> không hết hạn.
# Nhiều ưu đãi cùng loại trùng nhau: lấy cái có lợi nhất cho khách (KHÔNG cộng dồn).
PROMO_KINDS = {"keypct": "key_pct", "keyflat": "key_flat", "dep": "dep_pct"}


def active_promos(kinds=None):
    t = int(time.time())
    rows = db.execute(
        "SELECT * FROM promos WHERE active=1 AND (ends IS NULL OR ends>?) ORDER BY id", (t,)
    ).fetchall()
    return [p for p in rows if not kinds or p["kind"] in kinds]


def promo_discount(p, base):
    """Số tiền giảm của 1 ưu đãi giảm giá key trên giá gốc `base`."""
    d = base * p["value"] // 100 if p["kind"] == "key_pct" else p["value"]
    return max(0, min(d, base))


def key_price(game, pkg_code, base):
    """Giá key sau ưu đãi -> (giá, ưu đãi đang áp dụng hoặc None)."""
    best, best_d = None, 0
    for p in active_promos(("key_pct", "key_flat")):
        if p["game"] not in (None, game) or p["pkg_code"] not in (None, pkg_code):
            continue
        d = promo_discount(p, base)
        if d > best_d:
            best, best_d = p, d
    return base - best_d, best


def dep_bonus(amount):
    """Tiền tặng thêm khi nạp `amount` -> (bonus, ưu đãi đang áp dụng hoặc None)."""
    best, best_b = None, 0
    for p in active_promos(("dep_pct",)):
        if amount < p["min_amount"]:
            continue
        b = amount * p["value"] // 100
        if b > best_b:
            best, best_b = p, b
    return best_b, best


def promo_tag(p):
    return f"-{p['value']}%" if p["kind"] == "key_pct" else f"-{money(p['value'])}"


def promo_scope(p):
    if p["game"]:
        g = db.execute("SELECT name FROM games WHERE code=?", (p["game"],)).fetchone()
        s = g["name"] if g else p["game"]
    else:
        s = "tất cả game"
    if p["pkg_code"]:
        s += f" · gói {pkg_label(p['pkg_code'])}"
    return s


def promo_left(p):
    if not p["ends"]:
        return ""
    s = p["ends"] - int(time.time())
    if s <= 0:
        return "đã hết hạn"
    if s >= 86400:
        return f"còn {s // 86400} ngày"
    if s >= 3600:
        return f"còn {s // 3600} giờ"
    return f"còn {max(1, s // 60)} phút"


def promo_desc(p):
    """Nội dung 1 ưu đãi (HTML), không kèm thời gian."""
    if p["kind"] == "dep_pct":
        cond = f"từ {money(p['min_amount'])}" if p["min_amount"] else "mọi mức nạp"
        return f"Nạp {cond}: tặng thêm <b>{p['value']}%</b> giá trị nạp"
    what = f"{p['value']}%" if p["kind"] == "key_pct" else money(p["value"])
    return f"Giảm <b>{what}</b> giá key — {esc(promo_scope(p))}"


def promo_line(p):
    """Nội dung 1 ưu đãi kèm thời gian còn lại (HTML)."""
    left = promo_left(p)
    return promo_desc(p) + (f" <i>({left})</i>" if left else "")


def fmt_span(s):
    """Số giây -> '2 ngày' / '36 giờ' / '30 phút'."""
    if s % 86400 == 0:
        return f"{s // 86400} ngày"
    if s % 3600 == 0:
        return f"{s // 3600} giờ"
    return f"{max(1, s // 60)} phút"


def promo_box_text(p):
    """Tin thông báo công khai khi admin vừa tạo ưu đãi: nội dung + chạy trong bao lâu + hết hạn lúc nào."""
    if p["ends"]:
        when = f"trong <b>{fmt_span(p['ends'] - p['created'])}</b> (đến {fmt_time(p['ends'])})"
    else:
        when = "<b>không giới hạn thời gian</b>"
    return (
        f"🔥 <b>ƯU ĐÃI MỚI</b>\n{LINE}\n"
        f"{'💰' if p['kind'] == 'dep_pct' else '🛒'} {promo_desc(p)}\n"
        f"⏳ Áp dụng {when}\n{LINE}\n"
        f"👉 Vào bot, ưu đãi <b>tự động áp dụng</b> khi nạp tiền / mua key!"
    )


def parse_duration(s):
    """'2d' / '12h' / '30m' -> số giây; '0' -> 0 (không hết hạn); sai định dạng -> None."""
    m = re.fullmatch(r"0|(\d+)([mhd])", (s or "").strip().lower())
    if not m:
        return None
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)] if m.group(1) else 0


# ============================== GIFCODE ==============================
# kind: money    = tặng tiền vào số dư
#       key      = tặng 1 key
#       dep_pct  = nạp tiền được tặng thêm X% giá trị đơn nạp
#       key_pct  = giảm X% giá key          |  key_flat = giảm X đồng giá key
# giftcodes.amount = giá trị (số tiền hoặc %), max_uses = SỐ LƯỢNG được nhập, mỗi khách chỉ nhập 1 lần / mã.
# 3 loại cuối (GIFT_DEFERRED) không cộng ngay: khách nhập mã -> mã tự áp dụng vào đơn nạp / đơn mua key kế tiếp.
#   - dep_pct : lượt dùng bị giữ khi tạo đơn nạp; đơn bị hủy / từ chối / quá GIFT_HOLD giây chưa chuyển khoản -> trả lại lượt.
#   - key_*   : lượt dùng chỉ bị trừ khi mua key thành công.
# Gifcode CỘNG DỒN với ưu đãi sự kiện (ưu đãi trước, gifcode tính trên giá đã giảm).
GIFT_KINDS = {"money": "money", "key": "key", "dep": "dep_pct", "keypct": "key_pct", "keyflat": "key_flat"}
GIFT_DEFERRED = ("dep_pct", "key_pct", "key_flat")
GIFT_HOLD = 1800
GIFT_HINT = {
    "dep_pct": "Gifcode sẽ <b>tự áp dụng cho đơn nạp tiếp theo</b> — vào <b>Nạp Tiền</b> rồi chọn số tiền.",
    "key_pct": "Gifcode sẽ <b>tự áp dụng khi mua key</b> — giá giảm hiện ở bước xác nhận mua.",
    "key_flat": "Gifcode sẽ <b>tự áp dụng khi mua key</b> — giá giảm hiện ở bước xác nhận mua.",
}
GIFT_USAGE = (
    "Cú pháp:\n"
    "<code>/gifcode dep 10 [lượt] [MÃ]</code>  nạp tặng thêm 10% giá trị đơn\n"
    "<code>/gifcode keypct 20 [lượt] [MÃ]</code>  giảm 20% giá key\n"
    "<code>/gifcode keyflat 5000 [lượt] [MÃ]</code>  giảm 5.000đ giá key\n"
    "<code>/gifcode money 50000 [lượt] [MÃ]</code>  tặng tiền vào số dư\n"
    "<code>/gifcode key lc79 30d [lượt] [MÃ]</code>  tặng 1 key (sunviw sinh key)\n"
    "<i>lượt = số lượng được nhập (mặc định 1). Hoặc dùng nút trong /admin → Tạo Gifcode.</i>"
)


def parse_value(kind, s):
    """Giá trị nhập tay: loại % -> số nguyên trong khoảng cho phép; loại tiền -> '5000' / '5k'. Sai -> None."""
    s = (s or "").strip()
    if kind in ("key_pct", "dep_pct"):
        s = s.rstrip("%").strip()
        if not re.fullmatch(r"[0-9]+", s):
            return None
        n = int(s)
        return n if 1 <= n <= (99 if kind == "key_pct" else 200) else None
    n = parse_amount(s)
    return n if n and n >= 1 else None


def gift_lookup(code, uid, c=None):
    """Gifcode có dùng được cho khách không -> (row, None) hoặc (None, 'invalid' | 'used_up' | 'already')."""
    c = c or db
    code = (code or "").strip().upper()
    g = c.execute("SELECT * FROM giftcodes WHERE code=?", (code,)).fetchone()
    if not g:
        return None, "invalid"
    if c.execute("SELECT 1 FROM orders WHERE user_id=? AND gift=? AND status='waiting_pay'", (uid, code)).fetchone():
        return g, None  # lượt đang được giữ bởi chính đơn nạp (chờ chuyển khoản) của khách này
    if g["used_count"] >= g["max_uses"]:
        return None, "used_up"
    if c.execute("SELECT 1 FROM giftcode_uses WHERE code=? AND user_id=?", (code, uid)).fetchone():
        return None, "already"
    return g, None


def gift_take(c, g, uid):
    c.execute("UPDATE giftcodes SET used_count=used_count+1 WHERE code=?", (g["code"],))
    c.execute("INSERT INTO giftcode_uses(code,user_id,at) VALUES(?,?,?)", (g["code"], uid, int(time.time())))


def gift_release(c, code, uid):
    """Trả lại 1 lượt gifcode (đơn nạp bị hủy / từ chối)."""
    if c.execute("DELETE FROM giftcode_uses WHERE code=? AND user_id=?", (code, uid)).rowcount:
        c.execute("UPDATE giftcodes SET used_count=MAX(used_count-1,0) WHERE code=?", (code,))


def release_waiting(c, uid, oid=None):
    """Hủy đơn nạp đang chờ chuyển khoản của khách (1 đơn hoặc tất cả) và trả lại lượt gifcode nếu có."""
    sql, args = "SELECT * FROM orders WHERE user_id=? AND status='waiting_pay'", [uid]
    if oid:
        sql, args = sql + " AND id=?", args + [oid]
    for o in c.execute(sql, args).fetchall():
        c.execute("UPDATE orders SET status='canceled' WHERE id=?", (o["id"],))
        if o["gift"]:
            gift_release(c, o["gift"], uid)


def expire_gift_orders(c):
    """Đơn nạp có gifcode mà quá GIFT_HOLD giây vẫn chưa bấm 'Tôi đã chuyển khoản' -> hủy, nhả lượt cho người khác."""
    old = c.execute(
        "SELECT * FROM orders WHERE status='waiting_pay' AND gift IS NOT NULL AND created<?",
        (int(time.time()) - GIFT_HOLD,),
    ).fetchall()
    for o in old:
        c.execute("UPDATE orders SET status='canceled' WHERE id=?", (o["id"],))
        gift_release(c, o["gift"], o["user_id"])


def gift_discount(g, base):
    """Số tiền giảm của gifcode giảm giá key trên giá `base`."""
    d = base * g["amount"] // 100 if g["kind"] == "key_pct" else g["amount"]
    return max(0, min(d, base))


def gift_tag(g):
    return f"-{g['amount']}%" if g["kind"] == "key_pct" else f"-{money(g['amount'])}"


def gift_reward_text(g):
    """Mô tả phần thưởng của gifcode (HTML). g có thể là Row hoặc dict: kind, amount, game, pkg_code."""
    k = g["kind"]
    if k == "money":
        return f"💲 Tặng <b>{money(g['amount'])}</b> vào số dư"
    if k == "key":
        gm = db.execute("SELECT name FROM games WHERE code=?", (g["game"],)).fetchone()
        return f"🔑 Tặng 1 key <b>{esc(gm['name'] if gm else g['game'])} · {pkg_label(g['pkg_code'])}</b>"
    if k == "dep_pct":
        return f"💰 Nạp tiền được tặng thêm <b>{g['amount']}%</b> giá trị đơn nạp"
    if k == "key_pct":
        return f"🛒 Giảm <b>{g['amount']}%</b> giá key"
    return f"🛒 Giảm <b>{money(g['amount'])}</b> giá key"


def active_gift(ctx, uid, slot, kinds):
    """Gifcode khách đang giữ trong phiên (slot = 'dep_gift' | 'key_gift'). Hết hiệu lực -> bỏ."""
    code = ctx.user_data.get(slot) if ctx else None
    if not code:
        return None
    g, bad = gift_lookup(code, uid)
    if bad or g["kind"] not in kinds:
        ctx.user_data.pop(slot, None)
        return None
    return g


def create_promo(kind, value, game, pkg, min_amount, secs):
    now = int(time.time())
    pid = db.execute(
        "INSERT INTO promos(kind,value,game,pkg_code,min_amount,ends,created) VALUES(?,?,?,?,?,?,?)",
        (kind, value, game, pkg, min_amount, now + secs if secs else None, now),
    ).lastrowid
    return db.execute("SELECT * FROM promos WHERE id=?", (pid,)).fetchone()


def create_gift(uid, kind, value, game, pkg, uses, code):
    """Tạo gifcode -> row, hoặc None nếu mã đã tồn tại."""
    try:
        db.execute(
            "INSERT INTO giftcodes(code,kind,amount,game,pkg_code,max_uses,created_by,created) VALUES(?,?,?,?,?,?,?,?)",
            (code, kind, value, game, pkg, uses, uid, int(time.time())),
        )
    except sqlite3.IntegrityError:
        return None
    return db.execute("SELECT * FROM giftcodes WHERE code=?", (code,)).fetchone()


# ============================== THÔNG BÁO LÊN BOX ==============================


def mask_user(name):
    """Che bớt tên khách khi đăng công khai: 'Nguyễn Văn An' -> 'Ng***An'."""
    n = unicodedata.normalize("NFC", (name or "").strip()) or "Khách"
    if len(n) <= 2:
        return n[0] + "***"
    if len(n) <= 5:
        return n[0] + "***" + n[-1]
    return n[:2] + "***" + n[-2:]


async def post_box(bot, text):
    """Đăng thông báo công khai lên box -> True nếu đăng được. Lỗi chỉ ghi log, không ảnh hưởng thao tác của khách."""
    if NOTIFY_CHAT is None:
        return False
    extra = {"message_thread_id": NOTIFY_THREAD_ID} if NOTIFY_THREAD_ID else {}
    targets = [NOTIFY_CHAT]
    if isinstance(NOTIFY_CHAT, int) and NOTIFY_CHAT < 0 and not str(NOTIFY_CHAT).startswith("-100"):
        # Telegram Web chỉ hiện ID rút gọn; supergroup/kênh thật là -100 + số đó
        targets.append(int(f"-100{-NOTIFY_CHAT}"))
    err = None
    for target in targets:  # targets có thể được thêm ID mới trong vòng lặp (nhóm vừa nâng cấp)
        try:
            await bot.send_message(target, text, **extra)
            return True
        except ChatMigrated as e:
            err = e
            if e.new_chat_id not in targets:
                targets.append(e.new_chat_id)
        except TelegramError as e:
            err = e
    log.warning("Đăng thông báo lên box lỗi: %s (kiểm tra NOTIFY_CHAT_ID / FEEDBACK_CHAT_ID và quyền gửi tin của bot)", err)
    return False


def box_task(ctx, text):
    """Đăng lên box chạy nền, không làm chậm thao tác của khách."""
    ctx.application.create_task(post_box(ctx.bot, text))


def deposit_box_text(name, amount, bonus):
    lines = [
        "💰 <b>NẠP TIỀN THÀNH CÔNG</b>",
        LINE,
        f"👤 <b>{esc(mask_user(name))}</b> vừa nạp tiền",
        f"💵 Số tiền nạp: <b>{money(amount)}</b>",
    ]
    if bonus:
        lines.append(f"🎁 Ưu đãi tặng thêm: <b>+{money(bonus)}</b>")
    lines += [f"✅ Thực nhận: <b>{money(amount + bonus)}</b>", f"🕐 {fmt_time(time.time())}"]
    return "\n".join(lines)


def purchase_box_text(name, gname, label):
    """Tin công khai khi khách mua key. KHÔNG chứa key."""
    return (
        f"🛒 <b>MUA KEY THÀNH CÔNG</b>\n{LINE}\n"
        f"👤 <b>{esc(mask_user(name))}</b> vừa mua key\n"
        f"🎮 Game: <b>{esc(gname)}</b> · {label}\n"
        f"🕐 {fmt_time(time.time())}"
    )


# ============================== GIAO DIỆN ==============================


_OWNED = {}  # uid -> (ts, {game_code: [ {key,pkg,days,from_use,link,exp,left,activated,at} ]}) — key CÒN HẠN của khách


async def fetch_owned(uid, force=False):
    """Key còn hạn của khách theo game (hỏi sunviw hạn thật của từng key). None = chưa kiểm tra được."""
    now = time.time()
    c = _OWNED.get(uid)
    if c and not force and now - c[0] < 20:
        return c[1]
    rows = db.execute(
        "SELECT game,pkg_code,days,from_use,key_text,link,done FROM purchases "
        "WHERE user_id=? AND status='done' AND key_text IS NOT NULL AND key_text!='' ORDER BY id DESC LIMIT 200",
        (uid,),
    ).fetchall()
    if not rows:
        _OWNED[uid] = (now, {})
        return {}
    keys = list(dict.fromkeys(r["key_text"] for r in rows))
    try:
        res = await api.call("keystat", {"tg": uid, "keys": keys})
    except Exception as e:  # noqa: BLE001  (ApiDown, sunviw cũ chưa có op keystat...)
        log.warning("Không lấy được hạn key của khách %s: %s", uid, e)
        return c[1] if c else None
    if not isinstance(res, dict) or not res.get("ok"):
        log.warning("keystat lỗi: %s", (res or {}).get("code") if isinstance(res, dict) else res)
        return c[1] if c else None
    st = res.get("keys") or {}
    out = {}
    for r in rows:
        s_ = st.get(r["key_text"]) or {}
        if not s_.get("exists") or s_.get("expired"):
            continue  # key hết hạn / đã bị xóa -> không hiện
        out.setdefault(r["game"], []).append(
            {"key": r["key_text"], "pkg": r["pkg_code"], "days": r["days"], "link": (r["link"] or "").strip(),
             "exp": s_.get("exp"), "left": int(s_.get("left") or 0), "activated": bool(s_.get("activated")), "at": r["done"]}
        )
    _OWNED[uid] = (now, out)
    return out


def left_text(sec):
    d, rem = divmod(int(sec), 86400)
    h, m = divmod(rem // 60, 60)
    if d:
        return f"{d} ngày {h} giờ"
    return f"{h} giờ {m} phút" if h else f"{m} phút"


def owned_screen(owned):
    """Danh sách game khách đang có key còn hạn."""
    if not owned:
        return "🔑 <b>GAME ĐÃ MUA KEY</b>\n" + LINE + "\nBạn chưa có key nào còn hạn.", back_home_kb()
    names = {g["code"]: g["name"] for g in games_all()}
    rows = []
    for code, lst in owned.items():
        rows.append([B(f"🎮 {names.get(code, code)} ({len(lst)})", callback_data=f"own:{code}")])
    text = (
        f"🔑 <b>GAME ĐÃ MUA KEY</b>\n{LINE}\n"
        f"Bạn đang có key còn hạn ở <b>{len(owned)}</b> game.\nChọn game để xem key 👇"
    )
    return text, back_home_kb(rows)


def owned_game_screen(code, lst):
    gm = db.execute("SELECT name FROM games WHERE code=?", (code,)).fetchone()
    gname = gm["name"] if gm else code
    link = game_link(code) or next((x["link"] for x in lst if x["link"]), "")
    parts = [f"🎮 <b>{esc(gname)}</b>\n{LINE}"]
    for x in lst[:8]:
        if x["activated"] and x["exp"]:
            hsd = f"📅 HSD: <b>{fmt_time(x['exp'])}/{datetime.fromtimestamp(x['exp'], VN).year}</b> · còn <b>{left_text(x['left'])}</b>"
        else:
            hsd = f"📅 HSD: <b>{x['days']} ngày</b> — chưa kích hoạt, tính từ lần dùng đầu tiên"
        parts.append(f"⏱ Gói: <b>{pkg_label(x['pkg'])}</b>\n🔑 <code>{esc(x['key'])}</code>\n{hsd}")
    if len(lst) > 8:
        parts.append(f"<i>... và {len(lst) - 8} key khác.</i>")
    if link:
        parts.append(f"🔗 Link game:\n{esc(link)}")
    text = "\n\n".join(parts)
    rows = []
    if link and re.fullmatch(r"https?://\S+", link):
        rows.append([B("🎮 Mở game", url=link)])
    rows.append([B("⬅️ Trở về", callback_data="own")])
    return text, back_home_kb(rows)


def home_screen(u):
    g = db.execute("SELECT COUNT(*) n, SUM(CASE WHEN sell=1 AND online=1 THEN 1 ELSE 0 END) o FROM games WHERE api=1 AND sell=1").fetchone()
    g_on = len(games_online())  # mất liên lạc sunviw -> 0 game online
    keys = db.execute("SELECT COUNT(*) FROM logs WHERE user_id=? AND kind='key'", (u["id"],)).fetchone()[0]
    if u["balance"] <= 0 and keys == 0:
        note = "⚠️ <i>Bạn chưa có số dư. Nạp tiền để bắt đầu mua key.</i>"
    else:
        note = "✅ <i>Chọn chức năng bên dưới để tiếp tục.</i>"
    n_promo = len(active_promos())
    if n_promo:
        note += f"\n🔥 <b>Đang có {n_promo} ưu đãi</b> — tự động áp dụng khi nạp tiền / mua key!"
    adm = is_admin(u["id"])
    role = f"🛡 Vai trò: <b>{role_text(u['id'])}</b>\n" if adm else ""
    text = (
        f"👑 <b>{esc(BOT_NAME)}</b>\n"
        f"✨ <i>Hệ thống bán key &amp; nạp tiền tự động</i>\n"
        f"{LINE}\n"
        f"🙂 Xin chào, <b>{esc(u['name'])}</b>\n"
        f"🆔 ID: <code>{u['id']}</code>\n"
        f"{role}"
        f"💲 Số dư: <b>{money(u['balance'])}</b>\n"
        f"📥 Tổng nạp: <b>{money(u['total_deposit'])}</b>\n"
        f"🔑 Key đã mua: <b>{keys}</b>\n"
        f"🎮 Game online: <b>{g_on}/{g['n'] or 0}</b>\n"
        f"{LINE}\n"
        f"{note}"
    )
    rows = [[B("🛒 Mua Key", callback_data="buy"), B("💰 Nạp Tiền", callback_data="dep")]]
    oc = _OWNED.get(u["id"])
    if oc and oc[1]:  # có ít nhất 1 game còn key; 0/6 -> nút tự ẩn
        rows.append([B("🔑 Game Đã Mua Key", callback_data="own")])
    rows.append([B("💬 Feedback", callback_data="fb"), B("📜 Lịch Sử Số Dư", callback_data="hist")])
    url = cskh_url()
    rows.append([B("🎧 CSKH", url=url)] if url else [B("🎧 CSKH", callback_data="cskh_none")])
    if adm:
        rows.append([B("🛠 Bảng Admin", callback_data="adm")])
    return text, K(rows)


def deposit_screen(u, ctx=None):
    promos = "\n".join("• " + promo_line(p) for p in active_promos(("dep_pct",)))
    promo_txt = f"🔥 <b>Ưu đãi nạp tiền</b>\n{promos}\n\n" if promos else ""
    g = active_gift(ctx, u["id"], "dep_gift", ("dep_pct",))
    gift_txt = (
        f"🎟 Gifcode <code>{esc(g['code'])}</code>: nạp tặng thêm <b>{g['amount']}%</b> — áp dụng cho đơn nạp tiếp theo\n\n"
        if g
        else ""
    )
    text = (
        f"💰 <b>NẠP TIỀN</b>\n{LINE}\n"
        f"💲 Số dư hiện tại: <b>{money(u['balance'])}</b>\n"
        f"📌 Nạp tối thiểu: <b>{money(MIN_DEPOSIT)}</b>\n\n"
        f"{promo_txt}"
        f"{gift_txt}"
        f"Chọn mức nạp nhanh hoặc nhập số tiền khác 👇"
    )
    rows = []
    quick = [a for a in QUICK_AMOUNTS if MIN_DEPOSIT <= a <= MAX_DEPOSIT] or [MIN_DEPOSIT]  # mức nạp tối thiểu đổi được ở panel
    for i in range(0, len(quick), 3):
        rows.append([B(money(a), callback_data=f"dp:{a}") for a in quick[i : i + 3]])
    rows.append([B("✏️ Nhập số khác", callback_data="dcustom")])
    rows.append([B("🗑 Bỏ gifcode", callback_data="gx:dep")] if g else [B("🎁 Nhập Gifcode", callback_data="gift:dep")])
    rows.append([B("🏠 Trang chủ", callback_data="home")])
    return text, K(rows)


def back_home_kb(extra_rows=None):
    return K((extra_rows or []) + [[B("🏠 Trang chủ", callback_data="home")]])


def buy_screen(u):
    games = games_online()
    if not games:
        text = (
            f"🛒 <b>MUA KEY</b>\n{LINE}\n"
            f"🔧 Hiện tất cả game đang bảo trì / offline.\nVui lòng quay lại sau nhé!"
        )
        return text, back_home_kb()
    kp = active_promos(("key_pct", "key_flat"))
    promo_txt = ("🔥 <b>Ưu đãi mua key</b>\n" + "\n".join("• " + promo_line(p) for p in kp) + "\n\n") if kp else ""
    text = (
        f"🛒 <b>MUA KEY</b>\n{LINE}\n"
        f"💲 Số dư: <b>{money(u['balance'])}</b>\n\n"
        f"{promo_txt}"
        f"🟢 Danh sách game đang online:\n\nChọn game bạn muốn mua key 👇"
    )
    btns = [B(f"🟢 {g['name']}", callback_data=f"g:{g['code']}") for g in games]
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    rows.append([B("🏠 Trang chủ", callback_data="home")])
    return text, K(rows)


def game_screen(game):
    pk = db.execute("SELECT * FROM packages WHERE game=? ORDER BY price", (game["code"],)).fetchall()
    lines, rows = [], []
    for p in pk:
        price, promo = key_price(game["code"], p["code"], p["price"])
        shown = f"<s>{money(p['price'])}</s> <b>{money(price)}</b> 🔥{promo_tag(promo)}" if promo else money(price)
        lines.append(f"⏱ <b>{pkg_label(p['code'])}</b> — {shown}")
        tag = f" 🔥{promo_tag(promo)}" if promo else ""
        rows.append([B(f"⏱ {pkg_label(p['code'])} · {money(price)}{tag}", callback_data=f"p:{p['id']}")])
    body = "\n".join(lines) if lines else "<i>Game này chưa có gói nào.</i>"
    text = f"🎮 <b>{esc(game['name'])}</b>  🟢 Online\n{LINE}\n{body}\n\nChọn gói bạn muốn mua 👇"
    rows.append([B("⬅️ Quay lại", callback_data="buy"), B("🏠 Trang chủ", callback_data="home")])
    return text, K(rows)


def confirm_screen(u, p, game, ctx=None):
    price0, promo = key_price(game["code"], p["code"], p["price"])  # giá sau ưu đãi sự kiện
    g = active_gift(ctx, u["id"], "key_gift", ("key_pct", "key_flat"))
    gd = gift_discount(g, price0) if g else 0  # gifcode giảm thêm trên giá đã giảm
    if gd <= 0:
        g = None
    price = price0 - gd
    after = u["balance"] - price
    if promo or g:
        price_txt = f"💵 Giá gốc: <s>{money(p['price'])}</s>\n"
        if promo:
            pct = f" <b>{promo_tag(promo)}</b>" if promo["kind"] == "key_pct" else ""
            price_txt += f"🔥 Ưu đãi{pct}: giảm <b>{money(p['price'] - price0)}</b>\n"
        if g:
            price_txt += f"🎟 Gifcode <code>{esc(g['code'])}</code> <b>{gift_tag(g)}</b>: giảm <b>{money(gd)}</b>\n"
        price_txt += f"💵 Thành tiền: <b>{money(price)}</b>\n"
    else:
        price_txt = f"💵 Giá: <b>{money(price)}</b>\n"
    text = (
        f"🧾 <b>XÁC NHẬN MUA KEY</b>\n{LINE}\n"
        f"🎮 Game: <b>{esc(game['name'])}</b>\n"
        f"⏱ Gói: <b>{pkg_label(p['code'])}</b>\n"
        f"{price_txt}"
        f"💲 Số dư: <b>{money(u['balance'])}</b>\n"
    )
    gift_row = (
        [B("🗑 Bỏ gifcode", callback_data=f"gx:key:{p['id']}")]
        if g
        else [B("🎁 Nhập Gifcode", callback_data=f"gift:p:{p['id']}")]
    )
    if after >= 0:
        text += f"➡️ Sau khi mua: <b>{money(after)}</b>"
        kb = K(
            [
                [B("✅ Xác nhận mua", callback_data=f"pc:{p['id']}:{price}")],
                gift_row,
                [B("⬅️ Quay lại", callback_data=f"g:{game['code']}")],
            ]
        )
    else:
        text += f"\n⚠️ <b>Không đủ số dư</b> — còn thiếu <b>{money(-after)}</b>."
        kb = K(
            [
                [B("💰 Nạp tiền", callback_data="dep")],
                gift_row,
                [B("⬅️ Quay lại", callback_data=f"g:{game['code']}")],
            ]
        )
    return text, kb


HIST_DAYS = (1, 3, 7)
HIST_PER_PAGE = 6


def hist_items(uid, days):
    """Gom giao dịch trong `days` ngày gần nhất: log thành công (nạp / mua key / gifcode) + đơn nạp chờ duyệt / bị từ chối."""
    since = int(time.time()) - days * 86400
    items = []
    for r in db.execute("SELECT * FROM logs WHERE user_id=? AND at>=?", (uid, since)).fetchall():
        items.append((r["at"] or 0, r["id"], "log", r))
    for o in db.execute("SELECT * FROM orders WHERE user_id=? AND status IN ('pending','rejected')", (uid,)).fetchall():
        t = (o["handled_at"] if o["status"] == "rejected" else None) or o["submitted"] or o["created"] or 0
        if t >= since:
            items.append((t, o["id"], "order", o))
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return items


def hist_line(kind, r):
    if kind == "order":
        if r["status"] == "pending":
            t = r["submitted"] or r["created"]
            return f"⏳ <b>{fmt_time(t)}</b> · Nạp tiền <b>đang chờ duyệt</b> · {money(r['amount'])} <i>(đơn #{r['id']})</i>"
        t = r["handled_at"] or r["submitted"] or r["created"]
        return f"❌ <b>{fmt_time(t)}</b> · Nạp tiền <b>thất bại</b> · {money(r['amount'])} <i>(đơn #{r['id']} bị từ chối)</i>"
    t = fmt_time(r["at"])
    if r["kind"] == "deposit":
        bonus = int(r["extra"]) if (r["extra"] or "").isdigit() else 0
        note = f" <i>(gồm {money(bonus)} ưu đãi)</i>" if bonus else ""
        return f"✅ <b>{t}</b> · Nạp tiền <b>thành công</b> <b>+{money(r['amount'])}</b>{note} <i>({esc(r['detail'])})</i>"
    if r["kind"] == "gift_money":
        return f"🎁 <b>{t}</b> · {esc(r['detail'])} <b>+{money(r['amount'])}</b>"
    # key
    if r["amount"] < 0:
        head = f"🔑 <b>{t}</b> · Mua key <b>{esc(r['detail'])}</b> <b>-{money(-r['amount'])}</b>"
    else:
        head = f"🔑 <b>{t}</b> · Nhận key <b>{esc(r['detail'])}</b> <b>0đ</b>"
    return f"{head}\n      <code>{esc(r['extra'])}</code>"


def history_screen(u, days=1, page=0):
    if days not in HIST_DAYS:
        days = 1
    items = hist_items(u["id"], days)
    total_in = sum(r["amount"] for _, _, k, r in items if k == "log" and r["amount"] > 0)
    total_out = sum(-r["amount"] for _, _, k, r in items if k == "log" and r["amount"] < 0)
    pages = max(1, -(-len(items) // HIST_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = items[page * HIST_PER_PAGE : (page + 1) * HIST_PER_PAGE]
    body = "\n".join(hist_line(k, r) for _, _, k, r in chunk) if chunk else f"<i>Không có giao dịch nào trong {days} ngày gần nhất.</i>"
    text = (
        f"📜 <b>LỊCH SỬ BIẾN ĐỘNG SỐ DƯ</b>\n{LINE}\n"
        f"📅 Đang xem: <b>{days} ngày gần nhất</b>\n"
        f"💲 Số dư hiện tại: <b>{money(u['balance'])}</b>\n"
        f"➕ Tổng vào: <b>+{money(total_in)}</b>  ·  ➖ Tổng ra: <b>-{money(total_out)}</b>\n"
        f"{LINE}\n{body}\n{LINE}\n"
        f"<i>Trang {page + 1}/{pages} · {len(items)} giao dịch.</i>"
    )
    rows = [[B(("✅ " if d == days else "") + f"{d} ngày", callback_data=f"hist:{d}:0") for d in HIST_DAYS]]
    nav = []
    if page > 0:
        nav.append(B("◀️ Mới hơn", callback_data=f"hist:{days}:{page - 1}"))
    if page < pages - 1:
        nav.append(B("Cũ hơn ▶️", callback_data=f"hist:{days}:{page + 1}"))
    if nav:
        rows.append(nav)
    return text, back_home_kb(rows)


def promo_screen():
    keys = active_promos(("key_pct", "key_flat"))
    deps = active_promos(("dep_pct",))
    if not keys and not deps:
        body = "<i>Hiện chưa có chương trình ưu đãi nào. Quay lại sau nhé!</i>"
    else:
        parts = []
        if keys:
            parts.append("🛒 <b>GIẢM GIÁ KEY</b>\n" + "\n".join("• " + promo_line(p) for p in keys))
        if deps:
            parts.append("💰 <b>ƯU ĐÃI NẠP TIỀN</b>\n" + "\n".join("• " + promo_line(p) for p in deps))
        body = "\n\n".join(parts)
    text = (
        f"🔥 <b>ƯU ĐÃI</b>\n{LINE}\n{body}\n{LINE}\n"
        f"<i>Ưu đãi tự động áp dụng khi mua key / nạp tiền.\n"
        f"Nhiều ưu đãi trùng nhau sẽ lấy mức có lợi nhất cho bạn.</i>"
    )
    kb = K(
        [
            [B("🛒 Mua Key", callback_data="buy"), B("💰 Nạp Tiền", callback_data="dep")],
            [B("🏠 Trang chủ", callback_data="home")],
        ]
    )
    return text, kb


PROMPTS = {}


def build_prompts():
    """Dựng lại các lời nhắc nhập liệu (có số nạp tối thiểu — đổi được từ panel nên dựng lại mỗi lần cấu hình đổi)."""
    PROMPTS.clear()
    PROMPTS.update(
        {
            "amount": (
                f"✏️ <b>NHẬP SỐ TIỀN</b>\n{LINE}\n"
                f"Gõ số tiền muốn nạp rồi gửi vào đây (tối thiểu <b>{money(MIN_DEPOSIT)}</b>).\n"
                f"Ví dụ: <code>50000</code> hoặc <code>50k</code>"
            ),
            "gift": (
                f"🎁 <b>NHẬP GIFCODE</b>\n{LINE}\n"
                f"Gõ mã gifcode của bạn rồi gửi vào đây.\n"
                f"Gifcode có thể là: <b>tiền</b> / <b>key</b> / <b>tặng thêm % khi nạp</b> / <b>giảm giá key</b>."
            ),
            "fb_photo": (
                f"💬 <b>GỬI FEEDBACK</b>\n{LINE}\n"
                f"📸 Gửi <b>1 ảnh</b> kèm nội dung feedback vào đây (gửi ảnh thường hoặc gửi dạng <b>file</b> .jpg/.png đều được).\n"
                f"✍️ Viết nội dung ở phần <b>chú thích</b> của ảnh (tối đa {FB_MAX} ký tự).\n\n"
                f"<i>Ví dụ: ảnh key dùng được + “Key ngon, hỗ trợ nhiệt tình!”</i>"
            ),
            "fb_text": (
                f"💬 <b>GỬI FEEDBACK</b>\n{LINE}\n"
                f"✅ Đã nhận ảnh của bạn.\n"
                f"✍️ Giờ gõ <b>nội dung feedback</b> rồi gửi vào đây (tối đa {FB_MAX} ký tự)."
            ),
            "bc": (
                f"📢 <b>THÔNG BÁO TOÀN BOT</b>\n{LINE}\n"
                f"Gõ nội dung thông báo rồi gửi vào đây (chữ thuần, tối đa {BC_MAX} ký tự). "
                f"Bot sẽ cho bạn xem trước và xin xác nhận trước khi gửi tới <b>tất cả khách</b>."
            ),
        }
    )


BC_MAX = 3500  # độ dài tối đa 1 thông báo (khớp giới hạn của sunviw)
build_prompts()
PROMPT_BACK = {"amount": "dep", "gift": "dep", "fb_photo": "fb", "fb_text": "home", "bc": "adm"}

# ============================== GỬI / SỬA TIN NHẮN ==============================


async def safe_delete(msg):
    try:
        await msg.delete()
    except TelegramError:
        pass


async def delete_by_id(bot, chat_id, msg_id):
    try:
        await bot.delete_message(chat_id, msg_id)
    except TelegramError:
        pass


async def show(q, ctx, text, kb=None):
    """Thay thế nội dung tin nhắn hiện tại (sửa tại chỗ, không gửi tin mới).
    Riêng tin nhắn ảnh (QR) không sửa thành chữ được -> xóa rồi gửi tin mới."""
    msg = q.message
    if getattr(msg, "photo", None):
        await safe_delete(msg)
        return await ctx.bot.send_message(msg.chat_id, text, reply_markup=kb)
    try:
        return await q.edit_message_text(text, reply_markup=kb)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return msg
        raise


async def edit_or_send(bot, chat_id, msg_id, text, kb=None):
    """Sửa tin nhắn theo id; nếu không sửa được (đã bị xóa...) thì gửi tin mới."""
    if msg_id:
        try:
            return await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=kb)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return None
        except TelegramError:
            pass
    return await bot.send_message(chat_id, text, reply_markup=kb)


async def notify_admins(bot, text, kb=None, perm="deposit"):
    """Gửi cho mọi admin CÓ QUYỀN `perm` (admin chính + admin phụ được cấp quyền đó)."""
    sent = 0
    for aid in admins_with(perm):
        try:
            await bot.send_message(aid, text, reply_markup=kb)
            sent += 1
        except TelegramError as e:
            log.warning("Không gửi được cho admin %s: %s", aid, e)
    return sent


async def notify_admins_order(bot, o):
    """Gửi đơn nạp cho mọi admin có quyền duyệt + nhớ id tin nhắn để xóa khi đơn được xử lý."""
    sent = 0
    for aid in admins_with("deposit"):
        try:
            m = await bot.send_message(aid, order_card(o), reply_markup=order_kb(o["id"]))
            db.execute("INSERT INTO order_msgs(order_id,chat_id,msg_id) VALUES(?,?,?)", (o["id"], aid, m.message_id))
            sent += 1
        except TelegramError as e:
            log.warning("Không gửi được cho admin %s: %s", aid, e)
    return sent


async def clear_order_msgs(bot, oid):
    """Xóa tin nhắn đơn nạp ở TẤT CẢ admin (đơn đã duyệt / từ chối)."""
    rows = db.execute("SELECT chat_id,msg_id FROM order_msgs WHERE order_id=?", (oid,)).fetchall()
    db.execute("DELETE FROM order_msgs WHERE order_id=?", (oid,))
    for r in rows:
        await delete_by_id(bot, r["chat_id"], r["msg_id"])


# ============================== NẠP TIỀN ==============================


def qr_dynamic_url(amount, code):
    return (
        f"https://img.vietqr.io/image/{quote(BANK_CODE)}-{quote(BANK_ACCOUNT)}-compact2.png"
        f"?amount={int(amount)}&addInfo={quote(code)}&accountName={quote(BANK_OWNER)}"
    )


async def fetch_qr_bytes(url):
    """Tải ảnh QR về rồi gửi dạng file (Telegram tự tải URL hay lỗi / cache). Lỗi -> None."""

    def _get():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = r.read()
            if r.status == 200 and str(r.headers.get("Content-Type", "")).startswith("image") and len(data) > 500:
                return data
        return None

    try:
        return await asyncio.to_thread(_get)
    except Exception as e:  # noqa: BLE001
        log.warning("Tải QR động lỗi: %s", e)
        return None


async def open_qr(bot, chat_id, uid, amount, gift=None):
    """Tạo đơn + gửi QR chuyển khoản kèm nút 'Tôi đã chuyển khoản' / 'Hủy'.
    gift = gifcode 'nạp tặng thêm %' khách đang dùng (nếu có). Bonus = ưu đãi sự kiện + gifcode, chốt ngay lúc tạo đơn."""
    promo_b, _ = dep_bonus(amount)
    gift_b, g = 0, None
    with tx() as c:
        expire_gift_orders(c)
        release_waiting(c, uid)  # hủy đơn cũ chưa thanh toán (và trả lượt gifcode của đơn đó)
        if gift:
            g, _bad = gift_lookup(gift, uid, c)
            if g and g["kind"] == "dep_pct":
                gift_b = amount * g["amount"] // 100
                gift_take(c, g, uid)  # giữ lượt; hủy / từ chối đơn sẽ trả lại
            else:
                g = None
        bonus = promo_b + gift_b
        oid = c.execute(
            "INSERT INTO orders(user_id,amount,status,created,bonus,gift) VALUES(?,?,?,?,?,?)",
            (uid, amount, "waiting_pay", int(time.time()), bonus, g["code"] if g else None),
        ).lastrowid
        code = f"NAP{oid}"
        c.execute("UPDATE orders SET code=? WHERE id=?", (code, oid))

    bonus_txt = ""
    if promo_b:
        bonus_txt += f"🎁 Ưu đãi sự kiện: <b>+{money(promo_b)}</b>\n"
    if g:
        bonus_txt += f"🎟 Gifcode <code>{esc(g['code'])}</code> (+{g['amount']}%): <b>+{money(gift_b)}</b>\n"
    elif gift:
        bonus_txt += "⚠️ Gifcode không còn hiệu lực nên không được áp dụng.\n"
    if bonus:
        bonus_txt += f"➡️ Tổng bạn nhận: <b>{money(amount + bonus)}</b>\n"
    if g:
        bonus_txt += f"<i>⏱ Gifcode được giữ {GIFT_HOLD // 60} phút — hãy chuyển khoản &amp; bấm xác nhận trong thời gian này.</i>\n"
    def make_cap(dyn):
        return (
            f"💳 <b>NẠP TIỀN — ĐƠN #{oid}</b>\n{LINE}\n"
            f"🏦 Ngân hàng: <b>{esc(BANK_NAME)}</b>\n"
            f"👤 Chủ TK: <b>{esc(BANK_OWNER)}</b>\n"
            f"🔢 STK: <code>{esc(BANK_ACCOUNT)}</code>\n"
            f"💵 Số tiền: <b>{money(amount)}</b>\n"
            f"{bonus_txt}"
            f"📝 Nội dung CK: <code>{code}</code>\n{LINE}\n"
            + (
                "📲 Quét mã QR — <b>số tiền &amp; nội dung đã tự điền sẵn</b>, chuyển xong bấm <b>Tôi đã chuyển khoản</b>."
                if dyn
                else "📲 Quét mã QR, nhập <b>đúng số tiền</b> &amp; <b>nội dung CK</b> như trên, "
                "chuyển xong bấm <b>Tôi đã chuyển khoản</b>."
            )
        )

    kb = K(
        [
            [B("✅ Tôi đã chuyển khoản", callback_data=f"paid:{oid}")],
            [B("❌ Hủy đơn", callback_data=f"cancel:{oid}")],
        ]
    )

    msg = None
    url = qr_dynamic_url(amount, code)
    img = await fetch_qr_bytes(url)
    try:
        if img:
            msg = await bot.send_photo(chat_id, img, caption=make_cap(True), reply_markup=kb)
        else:
            msg = await bot.send_photo(chat_id, url, caption=make_cap(True), reply_markup=kb)
    except TelegramError as e:
        log.warning("Gửi QR động lỗi, dùng QR tĩnh: %s", e)
    cap = make_cap(False)  # từ đây là QR tĩnh / không QR -> khách phải nhập tay
    if msg is None and os.path.exists(QR_FILE):
        try:
            with open(QR_FILE, "rb") as f:
                msg = await bot.send_photo(chat_id, f, caption=cap, reply_markup=kb)
        except TelegramError as e:
            log.warning("Gửi ảnh QR lỗi: %s", e)
    if msg is None:
        msg = await bot.send_message(chat_id, cap, reply_markup=kb)
    db.execute("UPDATE orders SET msg_id=? WHERE id=?", (msg.message_id, oid))
    return oid


def submit_order(uid, oid):
    with tx() as c:
        n = c.execute(
            "UPDATE orders SET status='pending', submitted=? WHERE id=? AND user_id=? AND status='waiting_pay'",
            (int(time.time()), oid, uid),
        ).rowcount
        if not n:
            return None
        return c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()


def decide_order(oid, approve, admin_id):
    with tx() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o or o["status"] != "pending":
            return None
        now = int(time.time())
        if approve:
            bonus = o["bonus"] or 0  # tiền tặng thêm (ưu đãi nạp): cộng vào số dư nhưng không tính vào "Tổng nạp"
            c.execute("UPDATE orders SET status='approved',handled_by=?,handled_at=? WHERE id=?", (admin_id, now, oid))
            c.execute(
                "UPDATE users SET balance=balance+?, total_deposit=total_deposit+? WHERE id=?",
                (o["amount"] + bonus, o["amount"], o["user_id"]),
            )
            add_log(c, o["user_id"], "deposit", f"Đơn #{oid}", str(bonus) if bonus else "", o["amount"] + bonus)
        else:
            c.execute("UPDATE orders SET status='rejected',handled_by=?,handled_at=? WHERE id=?", (admin_id, now, oid))
            if o["gift"]:
                gift_release(c, o["gift"], o["user_id"])  # đơn bị từ chối -> trả lại lượt gifcode cho khách
        return c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()


def order_card(o):
    u = reload_user(o["user_id"])
    uname = f" (@{esc(u['username'])})" if u and u["username"] else ""
    bonus_txt = (
        f"🎁 Ưu đãi tặng thêm: <b>+{money(o['bonus'])}</b> (khách nhận <b>{money(o['amount'] + o['bonus'])}</b>)\n"
        if o["bonus"]
        else ""
    )
    gift_txt = f"🎟 Gifcode: <code>{esc(o['gift'])}</code>\n" if o["gift"] else ""
    return (
        f"🔔 <b>ĐƠN NẠP TIỀN #{o['id']}</b>\n{LINE}\n"
        f"👤 Khách: <b>{esc(u['name'] if u else '?')}</b>{uname}\n"
        f"🆔 ID: <code>{o['user_id']}</code>\n"
        f"💵 Số tiền: <b>{money(o['amount'])}</b>\n"
        f"{bonus_txt}"
        f"📝 Nội dung CK: <code>{esc(o['code'])}</code>\n"
        f"{gift_txt}"
        f"🕐 Gửi lúc: {fmt_time(o['submitted'] or o['created'])}"
    )


def order_kb(oid):
    return K([[B("✅ Duyệt", callback_data=f"ad:ok:{oid}"), B("❌ Từ chối", callback_data=f"ad:no:{oid}")]])


async def notify_user_order(bot, o):
    u = reload_user(o["user_id"])
    if o["status"] == "approved":
        bonus = o["bonus"] or 0
        bonus_txt = f"🎁 Ưu đãi tặng thêm: <b>+{money(bonus)}</b>\n" if bonus else ""
        text = (
            f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n{LINE}\n"
            f"➕ Số tiền nạp: <b>{money(o['amount'])}</b>\n"
            f"{bonus_txt}"
            f"✅ Thực nhận: <b>+{money(o['amount'] + bonus)}</b>\n"
            f"💲 Số dư mới: <b>{money(u['balance'])}</b>\n\n🎉 Cảm ơn bạn đã nạp tiền!"
        )
        kb = K([[B("🛒 Mua Key", callback_data="buy"), B("🏠 Trang chủ", callback_data="home")]])
    else:
        text = (
            f"❌ <b>ĐƠN NẠP #{o['id']} BỊ TỪ CHỐI</b>\n{LINE}\n"
            f"Admin chưa nhận được tiền hoặc thông tin không khớp.\n"
            f"Nếu bạn đã chuyển khoản, vui lòng liên hệ CSKH."
        )
        url = cskh_url()
        rows = [[B("🎧 CSKH", url=url)]] if url else []
        kb = back_home_kb(rows)
    await edit_or_send(bot, o["user_id"], o["msg_id"], text, kb)


# ============================== MUA KEY / GIFCODE ==============================


# ---------- MUA KEY QUA SUNVIW ----------
# Luồng an toàn tiền:
#   (1) GIỮ TIỀN trong DB: trừ số dư + giữ lượt gifcode + ghi đơn 'pending' kèm mã ref duy nhất (1 giao dịch SQLite)
#   (2) gọi sunviw /buy kèm ref -> sunviw sinh key bằng CHÍNH hệ key của nó (giống key tạo ở panel) + ghi doanh thu Tele
#   (3) OK                      -> lưu key vào đơn + lịch sử, gửi cho khách
#       thất bại CHẮC CHẮN      -> (game OFF, giá đổi, hết gói...) HOÀN TIỀN + trả lượt gifcode
#       mất liên lạc / lỗi tạm  -> đơn giữ 'pending'; job nền thử lại CÙNG ref (sunviw đảm bảo không tạo trùng key) rồi gửi key cho khách
_buying = set()  # đơn đang được xử lý (khách bấm + job nền không chạy trùng)
_busy_users = set()  # mỗi khách chỉ 1 thao tác mua tại 1 thời điểm (chống bấm đúp)
_bg_tasks = set()


def bg(coro):
    """Chạy nền, giữ tham chiếu để không bị thu dọn giữa chừng."""
    t = asyncio.ensure_future(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


def reserve_purchase(uid, pkg_id, expect=None, gift=None, chat_id=None, msg_id=None):
    """expect = giá khách đã thấy ở màn hình xác nhận; nếu giá thực tế khác (ưu đãi / gifcode vừa bắt đầu / hết) -> 'pricechg'.
    gift = gifcode giảm giá key khách đang dùng (nếu có) — chỉ bị trừ lượt khi mua thành công (thất bại -> trả lại)."""
    with tx() as c:
        p = c.execute(
            "SELECT p.*, g.online, g.sell, g.api, g.name AS gname FROM packages p JOIN games g ON g.code=p.game WHERE p.id=?",
            (pkg_id,),
        ).fetchone()
        if not p or not p["api"] or not pkg_days(p["code"]):
            return {"status": "err"}
        if not (api_fresh() and p["online"] and p["sell"]):
            return {"status": "offline"}
        price, _ = key_price(p["game"], p["code"], p["price"])
        gd, g = 0, None
        if gift:
            g, _bad = gift_lookup(gift, uid, c)
            if g and g["kind"] in ("key_pct", "key_flat"):
                gd = gift_discount(g, price)
            if gd <= 0:
                g = None
        price -= gd
        if expect is not None and price != expect:
            return {"status": "pricechg"}
        u = c.execute("SELECT balance FROM users WHERE id=?", (uid,)).fetchone()
        if u["balance"] < price:
            return {"status": "nomoney"}
        c.execute("UPDATE users SET balance=balance-? WHERE id=?", (price, uid))
        if g:
            gift_take(c, g, uid)
        pid = c.execute(
            "INSERT INTO purchases(ref,user_id,game,pkg_code,days,base,paid,gift,src,status,chat_id,msg_id,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("bp-" + secrets.token_hex(14), uid, p["game"], p["code"], pkg_days(p["code"]), p["price"], price,
             g["code"] if g else None, "buy", "pending", chat_id, msg_id, int(time.time())),
        ).lastrowid
        return {"status": "reserved", "pid": pid}


def settle_purchase(pid, res):
    """Chốt đơn theo trả lời của sunviw (res = dict, hoặc None nếu mất liên lạc)."""
    with tx() as c:
        pu = c.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
        if not pu or pu["status"] != "pending":
            return {"status": "skip"}
        now, uid = int(time.time()), pu["user_id"]
        if res and res.get("ok") and res.get("key"):
            key = str(res["key"])
            c.execute(
                "UPDATE purchases SET status='done', key_text=?, link=?, from_use=?, done=?, last_try=? WHERE id=?",
                (key, str(res.get("link") or ""), 1 if res.get("from_use") else 0, now, now, pid),
            )
            gm = c.execute("SELECT name FROM games WHERE code=?", (pu["game"],)).fetchone()
            gname = (gm["name"] if gm else None) or res.get("game_name") or pu["game"]
            pre = "Gifcode · " if pu["src"] == "gift" else ""
            add_log(c, uid, "key", f"{pre}{gname} · {pkg_label(pu['pkg_code'])}", key, -pu["paid"])
            return {"status": "ok"}
        code = (res or {}).get("code")
        if res and code in DEFINITIVE:
            if pu["paid"]:
                c.execute("UPDATE users SET balance=balance+? WHERE id=?", (pu["paid"], uid))  # HOÀN TIỀN
            if pu["gift"]:
                gift_release(c, pu["gift"], uid)  # trả lại lượt gifcode
            c.execute("UPDATE purchases SET status='failed', fail=?, done=? WHERE id=?", (code, now, pid))
            return {"status": "failed", "code": code}
        c.execute("UPDATE purchases SET tries=tries+1, last_try=? WHERE id=?", (now, pid))
        return {"status": "pending"}


async def run_purchase(pid):
    """Gọi sunviw cho đơn `pid` rồi chốt. -> {"status": ok|failed|pending|skip|busy, "pu": row, ...}"""
    if pid in _buying:
        return {"status": "busy"}
    _buying.add(pid)
    try:
        pu = db.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
        if not pu or pu["status"] != "pending":
            return {"status": "skip", "pu": pu}
        body = {"ref": pu["ref"], "game": pu["game"], "days": pu["days"], "base": pu["base"], "paid": pu["paid"],
                "tg": pu["user_id"], "src": pu["src"]}
        try:
            res = await api.call("buy", body)
        except ApiDown as e:
            res = None
            log.warning("Mua key (đơn #%s): chưa liên lạc được sunviw (%s) — sẽ tự thử lại", pid, e)
        out = settle_purchase(pid, res)
        out["pu"] = db.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
        out["res"] = res
        return out
    finally:
        _buying.discard(pid)


def dur_text(days, from_use):
    note = "bắt đầu tính từ <b>lần dùng đầu tiên</b>" if from_use else "tính từ lúc mua"
    return f"⏳ Thời hạn: <b>{days} ngày</b> ({note})"


def purchase_ok_text(pu):
    gm = db.execute("SELECT name FROM games WHERE code=?", (pu["game"],)).fetchone()
    gname = gm["name"] if gm else pu["game"]
    link = (pu["link"] or "").strip() or game_link(pu["game"])
    is_gift = pu["src"] == "gift"
    u = reload_user(pu["user_id"])
    pay = ""
    if not is_gift:
        base = pu["base"]
        saved = f" <i>(đã giảm {money(base - pu['paid'])})</i>" if pu["paid"] < base else ""
        if pu["gift"]:
            saved += f"\n🎟 Gifcode <code>{esc(pu['gift'])}</code> đã áp dụng"
        pay = f"💵 Thanh toán: <b>{money(pu['paid'])}</b>{saved}\n"
    text = (
        ("🎉 <b>NHẬP GIFCODE THÀNH CÔNG</b>" if is_gift else "✅ <b>MUA KEY THÀNH CÔNG</b>")
        + f"\n{LINE}\n"
        f"🎮 Game: <b>{esc(gname)}</b>\n"
        f"⏱ Gói: <b>{pkg_label(pu['pkg_code'])}</b>\n"
        f"{pay}"
        f"🔑 Key của bạn:\n<code>{esc(pu['key_text'])}</code>\n"
        f"<i>👆 Chạm vào key để sao chép.</i>\n"
        f"{dur_text(pu['days'], pu['from_use'])}\n{LINE}\n"
    )
    if link:
        text += f"🔗 Link game:\n{esc(link)}\n{LINE}\n"
    if not is_gift:
        text += f"💲 Số dư còn lại: <b>{money(u['balance'] if u else 0)}</b>"
    return text.rstrip(), link


def purchase_ok_kb(pu, link, back=None):
    rows = []
    if link and re.fullmatch(r"https?://\S+", link):
        rows.append([B("🎮 Mở game", url=link)])
    if pu["src"] == "gift":
        rows.append([B("🎁 Nhập mã khác", callback_data=f"gift:{back or 'dep'}"), B("🏠 Trang chủ", callback_data="home")])
    else:
        rows.append([B("🛒 Mua tiếp", callback_data="buy"), B("🏠 Trang chủ", callback_data="home")])
    return K(rows)


FAIL_MSG = {
    "game_off": "🔧 Game này vừa <b>offline / bảo trì</b> nên chưa tạo được key.",
    "price_changed": "🔄 Giá vừa thay đổi nên chưa tạo được key. Vui lòng xem lại giá rồi mua lại.",
    "no_package": "❌ Gói này hiện không còn bán.",
    "full": "⚠️ Kho key đang quá tải, vui lòng thử lại sau hoặc liên hệ CSKH.",
}


def purchase_fail_text(pu, code):
    why = FAIL_MSG.get(code, "⚠️ Hệ thống chưa tạo được key.")
    back = f"💲 Đã <b>hoàn lại {money(pu['paid'])}</b> vào số dư của bạn.\n" if pu["paid"] else ""
    if pu["gift"]:
        back += "🎟 Gifcode của bạn đã được trả lại lượt dùng.\n"
    return f"❌ <b>CHƯA MUA ĐƯỢC KEY</b>\n{LINE}\n{why}\n{back}"


def purchase_pending_text(pu):
    return (
        f"⏳ <b>ĐANG XỬ LÝ ĐƠN KEY #{pu['id']}</b>\n{LINE}\n"
        f"Hệ thống đang bận nên chưa trả key ngay.\n"
        f"{'💲 Tiền đã được giữ. ' if pu['paid'] else ''}Key sẽ <b>tự gửi vào đây</b> ngay khi sẵn sàng; "
        f"nếu không tạo được, bot sẽ <b>tự hoàn tiền</b>.\n\n<i>Bạn không cần làm gì thêm.</i>"
    )


async def deliver_purchase(bot, out, back=None):
    """Gửi kết quả đơn mua cho khách (sửa tin đang có nếu được). out = kết quả run_purchase."""
    pu, st = out.get("pu"), out.get("status")
    if not pu or st in ("skip", "busy"):
        return
    chat, mid = pu["chat_id"] or pu["user_id"], pu["msg_id"]
    if st == "ok":
        text, link = purchase_ok_text(pu)
        try:
            await edit_or_send(bot, chat, mid, text, purchase_ok_kb(pu, link, back))
        except BadRequest:  # link bị Telegram từ chối làm nút -> gửi lại không nút link
            await edit_or_send(bot, chat, mid, text, purchase_ok_kb(pu, "", back))
        if pu["src"] == "buy":  # đăng lên box: tên che bớt + tên game (KHÔNG có key)
            u = reload_user(pu["user_id"])
            gm = db.execute("SELECT name FROM games WHERE code=?", (pu["game"],)).fetchone()
            bg(post_box(bot, purchase_box_text(u["name"] if u else "", gm["name"] if gm else pu["game"], pkg_label(pu["pkg_code"]))))
    elif st == "failed":
        rows = [[B("🛒 Mua Key", callback_data="buy"), B("🏠 Trang chủ", callback_data="home")]]
        if pu["src"] == "gift":
            rows = [[B("🎁 Nhập mã khác", callback_data=f"gift:{back or 'dep'}"), B("🏠 Trang chủ", callback_data="home")]]
        await edit_or_send(bot, chat, mid, purchase_fail_text(pu, out.get("code")), K(rows))
    else:
        await edit_or_send(bot, chat, mid, purchase_pending_text(pu), back_home_kb())


async def recover_pending(app):
    """Thử lại các đơn 'pending' đã tới hạn (giãn dần 15s → 10 phút). Kẹt > 10 phút thì báo admin chính 1 lần."""
    now = int(time.time())
    for r in db.execute("SELECT id FROM purchases WHERE status='pending' ORDER BY id").fetchall():
        pu = db.execute("SELECT * FROM purchases WHERE id=?", (r["id"],)).fetchone()
        if not pu or pu["id"] in _buying:
            continue
        wait = min(15 * 2 ** min(pu["tries"], 6), 600)
        if now - (pu["last_try"] or pu["created"] or now) < wait:
            continue
        out = await run_purchase(pu["id"])
        if out["status"] in ("ok", "failed"):
            try:
                await deliver_purchase(app.bot, out)
            except TelegramError as e:  # khách chặn bot...: đơn đã chốt (key/hoàn tiền xong), chỉ không báo được
                log.warning("Không báo được kết quả đơn #%s cho khách: %s", pu["id"], e)
        elif out["status"] == "pending" and not pu["alerted"] and now - (pu["created"] or now) > 600:
            db.execute("UPDATE purchases SET alerted=1 WHERE id=?", (pu["id"],))
            for aid in ADMIN_IDS[:1]:
                try:
                    await app.bot.send_message(
                        aid,
                        f"⚠️ Đơn mua key <b>#{pu['id']}</b> của khách <code>{pu['user_id']}</code> ({esc(pu['game'])} · {pu['days']} ngày) "
                        f"đã chờ sunviw hơn 10 phút (đã thử {pu['tries']} lần). Tiền đang được giữ — bot tự thử lại / tự hoàn khi xong. "
                        f"Kiểm tra SUNVIW_URL / SUNVIW_BOT_SECRET / server sunviw.",
                    )
                except TelegramError:
                    pass


async def purchase_recovery_loop(app):
    """Job nền: đơn 'pending' (mất liên lạc sunviw) được thử lại CÙNG ref cho tới khi xong."""
    while True:
        try:
            await asyncio.sleep(15)
            await recover_pending(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Lỗi job thử lại đơn mua key")


def redeem_gift(uid, code):
    code = (code or "").strip().upper()
    with tx() as c:
        expire_gift_orders(c)
        g, bad = gift_lookup(code, uid, c)
        if bad:
            return {"status": bad}
        if g["kind"] in GIFT_DEFERRED:  # nạp tặng thêm % / giảm giá key: chưa trừ lượt, áp dụng ở đơn kế tiếp
            return {"status": "deferred", "g": dict(g)}
        if g["kind"] == "money":
            c.execute("UPDATE users SET balance=balance+? WHERE id=?", (g["amount"], uid))
            add_log(c, uid, "gift_money", f"Gifcode {code}", "", g["amount"])
            res = {"status": "ok", "text": f"💲 <b>+{money(g['amount'])}</b> đã được cộng vào số dư."}
        else:  # gifcode tặng key: key do SUNVIW sinh (tính là key tặng, không vào doanh thu)
            gm = c.execute("SELECT * FROM games WHERE code=? AND api=1", (g["game"],)).fetchone()
            days = pkg_days(g["pkg_code"])
            if not gm or not days:
                return {"status": "nostock"}
            if not (api_fresh() and gm["online"] and gm["sell"]):
                return {"status": "offline"}  # game đang OFF / bảo trì: chưa trừ lượt gifcode, khách nhập lại sau
            pid = c.execute(
                "INSERT INTO purchases(ref,user_id,game,pkg_code,days,base,paid,gift,src,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("gf-" + secrets.token_hex(14), uid, g["game"], g["pkg_code"], days, 0, 0, code, "gift", "pending", int(time.time())),
            ).lastrowid
            res = {"status": "gift_key", "pid": pid}
        gift_take(c, g, uid)
        return res


_gift_fails = {}  # chống dò mã: sai 5 lần / 10 phút -> khóa tạm


def gift_blocked(uid):
    now = time.time()
    fails = [t for t in _gift_fails.get(uid, []) if now - t < 600]
    _gift_fails[uid] = fails
    return len(fails) >= 5


# ============================== XỬ LÝ NÚT BẤM ==============================


def prompt_of(st):
    """Lời nhắc nhập liệu; feedback thì kèm tên game khách đã chọn."""
    t = PROMPTS[st["type"]]
    if st["type"] in ("fb_photo", "fb_text") and st.get("game"):
        gm = db.execute("SELECT name FROM games WHERE code=?", (st["game"],)).fetchone()
        if gm:
            t = t.replace(LINE + "\n", LINE + f"\n🎮 Game: <b>{esc(gm['name'])}</b>\n", 1)
    return t


async def start_prompt(q, ctx, kind, back=None, **extra):
    st = {"type": kind, "chat": q.message.chat_id, "msg": q.message.message_id, "back": back, **extra}
    ctx.user_data["await"] = st
    kb = K([[B("⬅️ Quay lại", callback_data=back or PROMPT_BACK[kind])]])
    await show(q, ctx, prompt_of(st), kb)


def fb_game_screen():
    """Bước chọn game khi gửi feedback (game đang bán; kể cả game đang OFF — khách vẫn góp ý được)."""
    games = db.execute("SELECT * FROM games WHERE api=1 AND sell=1 ORDER BY sort, code").fetchall()
    if not games:
        return (f"💬 <b>GỬI FEEDBACK</b>\n{LINE}\n🔧 Hệ thống đang bảo trì, bạn quay lại gửi feedback sau nhé!", back_home_kb())
    btns = [B(g["name"], callback_data=f"fbg:{g['code']}") for g in games]
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)] + [[B("🏠 Trang chủ", callback_data="home")]]
    return f"💬 <b>GỬI FEEDBACK</b>\n{LINE}\n🎮 Bạn muốn feedback cho <b>game nào</b>? 👇", K(rows)


async def route(q, ctx, data):
    """Trả về chuỗi thông báo (alert) hoặc None."""
    user = get_user(q.from_user)
    uid = user["id"]
    ctx.user_data.pop("await", None)

    if data == "home":
        await fetch_owned(uid)  # cập nhật nút "Game Đã Mua Key" (ẩn nếu không còn key nào còn hạn)
        await show(q, ctx, *home_screen(user))
        return
    if data == "own" or data.startswith("own:"):
        owned = await fetch_owned(uid, force=True)
        if owned is None:
            return "⚠️ Chưa kiểm tra được hạn key lúc này, vui lòng thử lại sau ít phút."
        if data == "own":
            if not owned:  # hết key -> về home (nút tự ẩn)
                await show(q, ctx, *home_screen(user))
                return "Bạn chưa có key nào còn hạn."
            await show(q, ctx, *owned_screen(owned))
            return
        lst = owned.get(data[4:])
        if not lst:
            await show(q, ctx, *(owned_screen(owned) if owned else home_screen(user)))
            return "Game này không còn key còn hạn."
        await show(q, ctx, *owned_game_screen(data[4:], lst))
        return
    if data == "cskh_none":
        return "Chưa cấu hình CSKH. Admin chính điền ở panel sunviw (tab Bot Tele) hoặc CSKH_USERNAME trong config.env"
    if data == "soldout":
        return "Gói này hiện không mua được, vui lòng chọn gói khác."
    if data == "promo":
        await show(q, ctx, *promo_screen())
        return

    # ---------- Nạp tiền ----------
    if data == "adm" or data.startswith(("adm:", "aw:")):  # bảng admin + wizard tạo ưu đãi / gifcode
        if not is_admin(uid):
            return "⛔ Bạn không có quyền."
        return await admin_route(q, ctx, data, uid)
    if data.startswith("gx:"):  # khách bỏ gifcode đang áp dụng
        if data == "gx:dep":
            ctx.user_data.pop("dep_gift", None)
            await show(q, ctx, *deposit_screen(user, ctx))
            return
        ctx.user_data.pop("key_gift", None)
        p = db.execute("SELECT * FROM packages WHERE id=?", (int(data.split(":")[2]),)).fetchone()
        game = p and db.execute("SELECT * FROM games WHERE code=?", (p["game"],)).fetchone()
        await show(q, ctx, *(confirm_screen(user, p, game, ctx) if game else buy_screen(user)))
        return

    if data == "dep":
        await show(q, ctx, *deposit_screen(user, ctx))
        return
    if data.startswith("dp:"):
        amount = int(data[3:])
        if amount < MIN_DEPOSIT or amount > MAX_DEPOSIT:
            return f"Số tiền nạp từ {money(MIN_DEPOSIT)} đến {money(MAX_DEPOSIT)}."
        await safe_delete(q.message)
        await open_qr(ctx.bot, q.message.chat_id, uid, amount, ctx.user_data.pop("dep_gift", None))
        return
    if data == "dcustom":
        await start_prompt(q, ctx, "amount")
        return
    if data.startswith("cancel:"):
        oid = int(data[7:])
        with tx() as c:
            o = c.execute("SELECT * FROM orders WHERE id=? AND user_id=? AND status='waiting_pay'", (oid, uid)).fetchone()
            if o:
                release_waiting(c, uid, oid)  # hủy đơn + trả lại lượt gifcode (nếu có)
        if o and o["gift"]:
            ctx.user_data["dep_gift"] = o["gift"]  # giữ gifcode cho đơn nạp kế tiếp
        await show(q, ctx, *deposit_screen(user, ctx))  # xóa QR, quay lại trang nạp để tạo đơn khác
        return
    if data.startswith("paid:"):
        oid = int(data[5:])
        o = submit_order(uid, oid)
        if not o:
            return "Đơn này đã hết hiệu lực hoặc đã được gửi trước đó."
        chat_id = q.message.chat_id
        await safe_delete(q.message)  # xóa tin nhắn QR
        wait = await ctx.bot.send_message(
            chat_id,
            f"⏳ <b>Vui lòng đợi trong giây lát...</b>\n{LINE}\n"
            f"Đơn nạp <b>#{oid}</b> · <b>{money(o['amount'])}</b> đã được gửi cho admin.\n"
            f"Tiền sẽ được cộng ngay sau khi admin duyệt.",
        )
        db.execute("UPDATE orders SET msg_id=? WHERE id=?", (wait.message_id, oid))
        o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        sent = await notify_admins_order(ctx.bot, o)
        if not sent:
            log.warning("Đơn #%s đang chờ nhưng chưa có admin nhận (chưa cấu hình ADMIN_CHAT_IDS).", oid)
        return

    # ---------- Admin duyệt đơn ----------
    if data.startswith(("ad:ok:", "ad:no:")):
        if not has_perm(uid, "deposit"):
            return "⛔ Bạn không có quyền duyệt đơn nạp tiền."
        approve = data.startswith("ad:ok:")
        o = decide_order(int(data.split(":")[2]), approve, uid)
        if not o:
            await safe_delete(q.message)  # đơn đã có admin khác xử lý -> dọn tin này cho đỡ nhầm
            return "Đơn này đã được xử lý hoặc không còn hiệu lực."
        await clear_order_msgs(ctx.bot, o["id"])  # xóa tin đơn ở TẤT CẢ admin
        await safe_delete(q.message)  # (tin từ /pending không nằm trong bảng order_msgs)
        await notify_user_order(ctx.bot, o)
        if approve:  # đăng lên box: tên che bớt + số tiền thực nhận
            buyer = reload_user(o["user_id"])
            box_task(ctx, deposit_box_text(buyer["name"] if buyer else "", o["amount"], o["bonus"] or 0))
        return

    # ---------- Mua key ----------
    if data == "buy":
        await show(q, ctx, *buy_screen(user))
        return
    if data.startswith("g:"):
        game = db.execute("SELECT * FROM games WHERE code=? AND api=1", (data[2:],)).fetchone()
        if not game or not game["sell"] or not game["online"] or not api_fresh():
            await show(q, ctx, *buy_screen(user))
            return "🔧 Game này vừa offline / bảo trì."
        await show(q, ctx, *game_screen(game))
        return
    if data.startswith("p:"):
        p = db.execute("SELECT * FROM packages WHERE id=?", (int(data[2:]),)).fetchone()
        game = p and db.execute("SELECT * FROM games WHERE code=? AND api=1", (p["game"],)).fetchone()
        if not p or not game or not game["sell"] or not game["online"] or not api_fresh():
            await show(q, ctx, *buy_screen(user))
            return "🔧 Game này vừa offline / bảo trì."
        await show(q, ctx, *confirm_screen(user, p, game, ctx))
        return
    if data.startswith("pc:"):
        parts = data.split(":")  # pc:<id>[:<giá đã xác nhận>]
        pkg_id = int(parts[1])
        if uid in _busy_users:
            return "⏳ Đang xử lý đơn trước của bạn, vui lòng đợi vài giây."
        _busy_users.add(uid)
        try:
            r = reserve_purchase(uid, pkg_id, int(parts[2]) if len(parts) > 2 else None, ctx.user_data.get("key_gift"),
                                 q.message.chat_id, q.message.message_id)
            if r["status"] == "reserved":
                ctx.user_data.pop("key_gift", None)  # gifcode giảm giá (nếu có) đã được giữ cho đơn này; thất bại sẽ tự trả lượt
                await show(q, ctx, f"⏳ <b>Đang tạo key cho bạn...</b>\n{LINE}\nVui lòng đợi vài giây, đừng thoát bot nhé.")
                out = await run_purchase(r["pid"])
                await deliver_purchase(ctx.bot, out)
                if out.get("status") == "failed" and out.get("code") in ("game_off", "price_changed", "no_package"):
                    bg(sync_once(ctx.application))  # danh mục có thể đã đổi -> cập nhật ngay
                return
        finally:
            _busy_users.discard(uid)
        msgs = {
            "pricechg": "🔄 Giá vừa thay đổi (ưu đãi bắt đầu / kết thúc). Vui lòng xem lại rồi xác nhận.",
            "nomoney": "⚠️ Số dư không đủ, vui lòng nạp thêm.",
            "offline": "🔧 Game này vừa offline / bảo trì.",
            "err": "⚠️ Gói không tồn tại.",
        }
        if r["status"] == "offline":
            await show(q, ctx, *buy_screen(reload_user(uid)))
        elif r["status"] == "pricechg":
            p = db.execute("SELECT * FROM packages WHERE id=?", (pkg_id,)).fetchone()
            game = db.execute("SELECT * FROM games WHERE code=?", (p["game"],)).fetchone()
            await show(q, ctx, *confirm_screen(reload_user(uid), p, game, ctx))
        return msgs[r["status"]]

    # ---------- Feedback: khách xác nhận / hủy ----------
    if data.startswith("fbok:"):
        fid = int(data[5:])
        if time.time() - _last_fb.get(uid, 0) < 30:
            return "⏳ Bạn gửi quá nhanh, vui lòng đợi vài giây rồi gửi lại."
        fb = submit_feedback(uid, fid)
        if not fb:
            return "Feedback này đã được gửi hoặc đã hết hiệu lực."
        _last_fb[uid] = time.time()
        chat_id = q.message.chat_id
        await safe_delete(q.message)  # xóa tin xác nhận
        wait = await ctx.bot.send_message(
            chat_id,
            f"⏳ <b>Vui lòng đợi trong giây lát...</b>\n{LINE}\n"
            f"Feedback của bạn đã được gửi cho admin.\nNếu được duyệt, feedback sẽ được đăng lên box.",
        )
        db.execute("UPDATE feedback SET msg_id=? WHERE id=?", (wait.message_id, fid))
        fb = db.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone()
        sent = await notify_admins_photo(ctx.bot, fb["photo_id"], fb_card(fb), fb_kb(fid))
        if not sent:
            log.warning("Feedback #%s đang chờ nhưng chưa có admin nhận (chưa cấu hình ADMIN_CHAT_IDS).", fid)
        return
    if data.startswith("fbno:"):
        db.execute(
            "UPDATE feedback SET status='canceled' WHERE id=? AND user_id=? AND status='draft'", (int(data[5:]), uid)
        )
        await fetch_owned(uid, force=True)
        await show(q, ctx, *home_screen(user))  # xóa tin xác nhận, về trang chủ
        return

    # ---------- Broadcast (admin có quyền broadcast) ----------
    if data.startswith("bc:"):
        if not has_perm(uid, "broadcast"):
            return "⛔ Bạn không có quyền gửi thông báo toàn bot."
        if data == "bc:go":
            ctx.user_data.pop("bc_draft", None)
            await start_prompt(q, ctx, "bc")
            return
        if data == "bc:ok":
            text = ctx.user_data.pop("bc_draft", None)
            if not text:
                await show(q, ctx, *admin_panel(uid))
                return "Nội dung đã hết hạn, hãy soạn lại."
            await show(q, ctx, f"📤 <b>Đang gửi thông báo...</b>\n{LINE}\nBạn có thể làm việc khác, bot sẽ báo khi xong.")
            bg(run_bot_broadcast(ctx.bot, q.message.chat_id, q.message.message_id, text, q.from_user.full_name))
            return

    # ---------- Feedback: admin duyệt / từ chối ----------
    if data.startswith(("fa:ok:", "fa:no:")):
        if not has_perm(uid, "feedback"):
            return "⛔ Bạn không có quyền duyệt feedback."
        approve = data.startswith("fa:ok:")
        fid = int(data.split(":")[2])
        fb = claim_feedback(fid)
        if not fb:
            return "Feedback này đã được xử lý hoặc không còn hiệu lực."
        if approve:
            try:
                err = await post_feedback_to_box(ctx.bot, fb)
            except Exception:
                db.execute("UPDATE feedback SET status='pending' WHERE id=?", (fid,))
                raise
            if err:  # đăng thất bại -> trả về trạng thái chờ để admin thử lại sau khi sửa cấu hình
                db.execute("UPDATE feedback SET status='pending' WHERE id=?", (fid,))
                return err[:190]
        db.execute(
            "UPDATE feedback SET status=?, handled_by=?, handled_at=? WHERE id=?",
            ("approved" if approve else "rejected", uid, int(time.time()), fid),
        )
        stamp = ("✅ <b>ĐÃ DUYỆT &amp; ĐĂNG LÊN BOX</b>" if approve else "❌ <b>ĐÃ TỪ CHỐI</b>") + f" bởi {esc(q.from_user.full_name)} · {fmt_time(time.time())}"
        try:
            await q.edit_message_caption(caption=f"{q.message.caption_html}\n\n{stamp}", reply_markup=None)
        except TelegramError:
            pass
        await notify_user_feedback(ctx.bot, db.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone())
        return

    # ---------- Gifcode / Feedback / Lịch sử ----------
    if data == "gift" or data.startswith("gift:"):  # gift:dep | gift:p:<id> = màn hình sẽ quay lại sau khi nhập mã
        back = data[5:]
        await start_prompt(q, ctx, "gift", back if (back == "dep" or re.fullmatch(r"p:[0-9]+", back)) else "dep")
        return
    if data == "fb":
        await show(q, ctx, *fb_game_screen())
        return
    if data.startswith("fbg:"):
        game = db.execute("SELECT code FROM games WHERE code=? AND api=1 AND sell=1", (data[4:],)).fetchone()
        if not game:
            await show(q, ctx, *fb_game_screen())
            return "Game này không còn trong danh sách."
        await start_prompt(q, ctx, "fb_photo", game=game["code"])
        return
    if data == "hist" or data.startswith("hist:"):  # hist:<số ngày>:<trang>
        p = data.split(":")
        days = int(p[1]) if len(p) > 1 and p[1].isdigit() else 1
        page = int(p[2]) if len(p) > 2 and p[2].isdigit() else 0
        await show(q, ctx, *history_screen(user, days, page))
        return


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.message:
        return
    alert = None
    try:
        alert = await route(q, ctx, q.data or "")
    except Exception:
        log.exception("Lỗi xử lý nút %s", q.data)
        alert = "⚠️ Có lỗi xảy ra, vui lòng thử lại."
    try:
        await q.answer(alert, show_alert=bool(alert))
    except TelegramError:
        pass


# ============================== NHẬP TEXT (số tiền / gifcode / feedback) ==============================

_last_fb = {}


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    st = ctx.user_data.get("await")
    await safe_delete(m)  # giữ khung chat gọn, chỉ có 1 tin nhắn giao diện
    if not st:
        return
    user = get_user(m.from_user)
    uid, chat, mid = user["id"], st["chat"], st["msg"]
    text = (m.text or "").strip()
    if st["type"] == "wiz":  # admin (có quyền) đang nhập chữ trong wizard tạo ưu đãi / gifcode
        w = ctx.user_data.get("wiz")
        if w and w.get("uid") == uid and has_perm(uid, w["kind"]):
            await wiz_text(ctx, st, text)
        return
    back = st.get("back") or PROMPT_BACK[st["type"]]
    kb_back = K([[B("⬅️ Quay lại", callback_data=back)]])

    async def err(msg):
        await edit_or_send(ctx.bot, chat, mid, f"{prompt_of(st)}\n\n{msg}", kb_back)

    if st["type"] == "bc":  # admin (quyền broadcast) soạn thông báo toàn bot -> xem trước -> xác nhận
        if not has_perm(uid, "broadcast"):
            return
        if not text:
            return await err("⚠️ Hãy gõ nội dung thông báo bằng chữ.")
        if len(text) > BC_MAX:
            return await err(f"⚠️ Quá dài ({len(text)}/{BC_MAX} ký tự). Hãy rút gọn.")
        ctx.user_data.pop("await", None)
        ctx.user_data["bc_draft"] = text
        t, kb = bc_confirm(text)
        await edit_or_send(ctx.bot, chat, mid, t, kb)
        return

    if st["type"] == "amount":
        amount = parse_amount(text)
        if amount is None:
            return await err("⚠️ <b>Số tiền không hợp lệ.</b> Hãy nhập lại, ví dụ <code>50000</code>.")
        if amount < MIN_DEPOSIT:
            return await err(f"⚠️ <b>Nạp tối thiểu {money(MIN_DEPOSIT)}.</b> Hãy nhập lại.")
        if amount > MAX_DEPOSIT:
            return await err(f"⚠️ <b>Nạp tối đa {money(MAX_DEPOSIT)}.</b> Hãy nhập lại.")
        ctx.user_data.pop("await", None)
        await delete_by_id(ctx.bot, chat, mid)
        await open_qr(ctx.bot, chat, uid, amount, ctx.user_data.pop("dep_gift", None))

    elif st["type"] == "gift":
        if gift_blocked(uid):
            return await err("⛔ Bạn nhập sai quá nhiều lần. Vui lòng thử lại sau 10 phút.")
        r = redeem_gift(uid, text)
        if r["status"] == "gift_key":  # gifcode tặng key: sunviw sinh key rồi gửi (kèm thời hạn + link game)
            ctx.user_data.pop("await", None)
            db.execute("UPDATE purchases SET chat_id=?, msg_id=? WHERE id=?", (chat, mid, r["pid"]))
            await edit_or_send(ctx.bot, chat, mid, f"⏳ <b>Đang tạo key cho bạn...</b>\n{LINE}\nVui lòng đợi vài giây.", None)
            out = await run_purchase(r["pid"])
            await deliver_purchase(ctx.bot, out, back)
            return
        if r["status"] in ("ok", "deferred"):
            ctx.user_data.pop("await", None)
            if r["status"] == "deferred":  # nạp tặng thêm % / giảm giá key: ghi nhớ, tự áp dụng ở đơn kế tiếp
                g = r["g"]
                ctx.user_data["dep_gift" if g["kind"] == "dep_pct" else "key_gift"] = g["code"]
                body = f"{gift_reward_text(g)}\n{GIFT_HINT[g['kind']]}"
            else:
                body = r["text"]
            await edit_or_send(
                ctx.bot, chat, mid,
                f"🎉 <b>NHẬP GIFCODE THÀNH CÔNG</b>\n{LINE}\n{body}",
                K(
                    [
                        [B("🎁 Nhập mã khác", callback_data=f"gift:{back}"), B("⬅️ Quay lại", callback_data=back)],
                        [B("🏠 Trang chủ", callback_data="home")],
                    ]
                ),
            )
            return
        errs = {
            "invalid": "❌ <b>Gifcode không tồn tại.</b> Kiểm tra lại rồi nhập lại.",
            "used_up": "❌ <b>Gifcode này đã hết lượt sử dụng.</b>",
            "already": "❌ <b>Bạn đã dùng gifcode này rồi.</b>",
            "nostock": "⚠️ <b>Gifcode hợp lệ nhưng gói key này không còn.</b> Vui lòng liên hệ CSKH.",
            "offline": "🔧 <b>Game của gifcode này đang offline / bảo trì.</b> Gifcode chưa bị trừ lượt — bạn nhập lại khi game online nhé.",
        }
        if r["status"] in ("invalid", "used_up"):
            _gift_fails.setdefault(uid, []).append(time.time())
        await err(errs[r["status"]])
    elif st["type"] == "fb_photo":
        await err("⚠️ <b>Cần gửi ẢNH</b> kèm nội dung (viết nội dung ở phần chú thích của ảnh).")

    elif st["type"] == "fb_text":
        if not text:
            return await err("⚠️ Hãy gõ nội dung feedback bằng chữ.")
        await fb_preview(ctx, user, chat, mid, st["photo"], text, st.get("doc", False), st.get("game"))


# ============================== FEEDBACK (ảnh + nội dung -> admin duyệt -> đăng lên box) ==============================


def mask_name(name):
    name = (name or "").strip() or "Khách"
    return name[0] + "***" if FEEDBACK_MASK_NAME else name


def fb_clip(text):
    text = (text or "").strip()
    return text if len(text) <= FB_MAX else text[: FB_MAX - 1] + "…"


async def fb_preview(ctx, user, chat, prompt_mid, photo_id, text, is_doc=False, game=None):
    """Gửi tin XÁC NHẬN (ảnh + nội dung + nút Xác nhận / Hủy), thay thế tin nhắc nhập.
    is_doc=True: khách gửi ảnh dạng FILE (document) -> tải về rồi gửi lại thành ẢNH thường,
    nhờ đó admin và box xem trực tiếp, các bước sau (send_photo) không cần đổi."""
    text = fb_clip(text)
    ctx.user_data.pop("await", None)
    with tx() as c:
        c.execute("UPDATE feedback SET status='canceled' WHERE user_id=? AND status='draft'", (user["id"],))
        fid = c.execute(
            "INSERT INTO feedback(user_id,text,at,photo_id,status,game) VALUES(?,?,?,?,?,?)",
            (user["id"], text, int(time.time()), photo_id, "draft", game),
        ).lastrowid
    gm = db.execute("SELECT name FROM games WHERE code=?", (game,)).fetchone() if game else None
    cap = (
        f"📝 <b>XÁC NHẬN FEEDBACK</b>\n{LINE}\n"
        + (f"🎮 Game: <b>{esc(gm['name'])}</b>\n" if gm else "")
        + f"{esc(text)}\n{LINE}\n"
        f"ℹ️ Feedback sẽ được gửi cho admin duyệt. Nếu được duyệt sẽ <b>đăng công khai lên box</b>.\n"
        f"Bấm <b>Xác nhận gửi</b> để gửi, hoặc <b>Hủy</b> để bỏ."
    )
    kb = K([[B("✅ Xác nhận gửi", callback_data=f"fbok:{fid}"), B("❌ Hủy", callback_data=f"fbno:{fid}")]])
    await delete_by_id(ctx.bot, chat, prompt_mid)
    try:
        media = photo_id
        if is_doc:
            f = await ctx.bot.get_file(photo_id)
            media = bytes(await f.download_as_bytearray())
        msg = await ctx.bot.send_photo(chat, media, caption=cap, reply_markup=kb)
        if is_doc:  # lưu file_id của bản ảnh để admin / box dùng
            db.execute("UPDATE feedback SET photo_id=? WHERE id=?", (msg.photo[-1].file_id, fid))
    except TelegramError as e:
        log.warning("Không gửi được ảnh xác nhận feedback: %s", e)
        db.execute("UPDATE feedback SET status='canceled' WHERE id=?", (fid,))
        await ctx.bot.send_message(chat, "⚠️ Không xử lý được ảnh, vui lòng thử gửi lại.", reply_markup=back_home_kb())
        return
    db.execute("UPDATE feedback SET msg_id=? WHERE id=?", (msg.message_id, fid))


FB_IMG_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
FB_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
FB_IMG_MAX = 10 * 1024 * 1024  # Telegram chỉ cho đăng ảnh (send_photo) tối đa 10MB


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Nhận ảnh feedback: ảnh thường HOẶC ảnh gửi dạng file (vd qr.jpg)."""
    m = update.message
    st = ctx.user_data.get("await")
    doc = m.document
    caption = (m.caption or "").strip()
    await safe_delete(m)  # file_id vẫn dùng được sau khi xóa tin gốc
    if not st or st["type"] not in ("fb_photo", "fb_text"):
        return
    user = get_user(m.from_user)
    chat, mid = st["chat"], st["msg"]
    if doc:
        is_img = (doc.mime_type or "").lower() in FB_IMG_MIMES or (doc.file_name or "").lower().endswith(FB_IMG_EXTS)
        if not is_img or (doc.file_size or 0) > FB_IMG_MAX:
            await edit_or_send(
                ctx.bot, chat, mid,
                f"{prompt_of(st)}\n\n⚠️ <b>File không hợp lệ.</b> Chỉ nhận ảnh JPG / PNG / WEBP dưới 10MB.",
                K([[B("⬅️ Quay lại", callback_data=PROMPT_BACK[st["type"]])]]),
            )
            return
        photo_id = doc.file_id
    else:
        photo_id = m.photo[-1].file_id
    is_doc = bool(doc)
    if caption:
        await fb_preview(ctx, user, chat, mid, photo_id, caption, is_doc, st.get("game"))
    else:  # ảnh không có chú thích -> xin nội dung
        nst = {"type": "fb_text", "chat": chat, "msg": mid, "photo": photo_id, "doc": is_doc, "game": st.get("game")}
        ctx.user_data["await"] = nst
        await edit_or_send(ctx.bot, chat, mid, prompt_of(nst), K([[B("⬅️ Hủy", callback_data="home")]]))


def submit_feedback(uid, fid):
    with tx() as c:
        n = c.execute(
            "UPDATE feedback SET status='pending', submitted=? WHERE id=? AND user_id=? AND status='draft'",
            (int(time.time()), fid, uid),
        ).rowcount
        return c.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone() if n else None


def claim_feedback(fid):
    """Admin bấm Duyệt/Từ chối: chiếm đơn (pending -> approving) để 2 admin không xử lý trùng."""
    with tx() as c:
        n = c.execute("UPDATE feedback SET status='approving' WHERE id=? AND status='pending'", (fid,)).rowcount
        return c.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone() if n else None


def fb_card(fb):
    u = reload_user(fb["user_id"])
    uname = f" (@{esc(u['username'])})" if u and u["username"] else ""
    gm = db.execute("SELECT name FROM games WHERE code=?", (fb["game"],)).fetchone() if fb["game"] else None
    game_txt = f"🎮 Game: <b>{esc(gm['name'])}</b>\n" if gm else ""
    return (
        f"💬 <b>FEEDBACK #{fb['id']}</b>\n{LINE}\n"
        f"👤 <b>{esc(u['name'] if u else '?')}</b>{uname}\n🆔 <code>{fb['user_id']}</code>\n{game_txt}\n"
        f"{esc(fb['text'])}\n{LINE}\n🕐 {fmt_time(fb['submitted'] or fb['at'])}"
    )


def fb_kb(fid):
    return K([[B("✅ Duyệt & đăng box", callback_data=f"fa:ok:{fid}"), B("❌ Từ chối", callback_data=f"fa:no:{fid}")]])


async def notify_admins_photo(bot, photo_id, caption, kb=None, perm="feedback"):
    sent = 0
    for aid in admins_with(perm):
        try:
            await bot.send_photo(aid, photo_id, caption=caption, reply_markup=kb)
            sent += 1
        except TelegramError as e:
            log.warning("Không gửi được cho admin %s: %s", aid, e)
    return sent


async def bot_username(bot):
    """@username của bot (để ghi 'Mua key tại @bot' dưới bài feedback)."""
    name = getattr(bot, "username", None)
    if not name:
        try:
            name = (await bot.get_me()).username
        except TelegramError:
            name = None
    return name


def clip_to(text, room):
    """Cắt text sao cho sau khi thoát HTML vẫn <= room ký tự."""
    if len(esc(text)) <= room:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(esc(text[:mid])) + 1 <= room:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def fb_footer(bot_user, fb):
    """Phần thêm dưới bài feedback đã duyệt: 'Mua key tại @bot' + link game khách đã chọn (link chỉnh ở panel sunviw)."""
    lines = []
    if bot_user:
        lines.append(f"🛒 Mua key tại @{esc(bot_user)}")
    link = game_link(fb["game"]) if fb["game"] else ""
    if link:
        gm = db.execute("SELECT name FROM games WHERE code=?", (fb["game"],)).fetchone()
        label = f"Link game {esc(gm['name'])}" if gm else "Link game"
        lines.append(f"🎮 {label}:\n{esc(link)}")
    return ("\n" + LINE + "\n" + "\n".join(lines)) if lines else ""


async def post_feedback_to_box(bot, fb):
    """Đăng feedback đã duyệt lên box. Định dạng GIỮ NGUYÊN như cũ, thêm phía dưới: 'Mua key tại @bot' + link game.
    Trả về None nếu OK, hoặc chuỗi lỗi."""
    if FEEDBACK_CHAT is None:
        return "Chưa cấu hình FEEDBACK_CHAT_ID (config.env hoặc panel sunviw → Bot Tele) nên chưa đăng được lên box."
    u = reload_user(fb["user_id"])
    head = f"⭐ <b>FEEDBACK KHÁCH HÀNG</b>\n{LINE}\n👤 <b>{esc(mask_name(u['name'] if u else ''))}</b>\n\n"
    footer = fb_footer(await bot_username(bot), fb)
    room = max(80, 1024 - len(head) - len(footer) - 4)  # chú thích ảnh Telegram tối đa 1024 ký tự
    cap = head + esc(clip_to(fb["text"] or "", room)) + footer
    extra = {"message_thread_id": FEEDBACK_THREAD_ID} if FEEDBACK_THREAD_ID else {}
    targets = [FEEDBACK_CHAT]
    if isinstance(FEEDBACK_CHAT, int) and FEEDBACK_CHAT < 0 and not str(FEEDBACK_CHAT).startswith("-100"):
        # Telegram Web (link #-4294961954) chỉ hiện ID rút gọn; supergroup/kênh thật là -100 + số đó
        targets.append(int(f"-100{-FEEDBACK_CHAT}"))
    err = None
    for target in targets:  # targets có thể được thêm ID mới trong vòng lặp (nhóm vừa nâng cấp)
        try:
            await bot.send_photo(target, fb["photo_id"], caption=cap, **extra)
            if target != FEEDBACK_CHAT:
                log.warning("Đăng box được bằng ID %s -> hãy sửa FEEDBACK_CHAT_ID trong config.env thành giá trị này.", target)
            return None
        except ChatMigrated as e:
            err = e
            if e.new_chat_id not in targets:
                targets.append(e.new_chat_id)
        except TelegramError as e:
            err = e
    log.warning("Đăng feedback lên box lỗi: %s", err)
    return f"Không đăng được lên box ({err}). Kiểm tra bot đã vào box & có quyền gửi tin, và FEEDBACK_CHAT_ID đúng (dùng /chatid trong box)."


async def notify_user_feedback(bot, fb):
    if fb["status"] == "approved":
        text = (
            f"✅ <b>FEEDBACK ĐÃ ĐƯỢC DUYỆT</b>\n{LINE}\n"
            f"Feedback của bạn đã được đăng lên box. Cảm ơn bạn! 💖"
        )
    else:
        text = (
            f"❌ <b>FEEDBACK CHƯA ĐƯỢC DUYỆT</b>\n{LINE}\n"
            f"Rất tiếc feedback của bạn chưa phù hợp để đăng. Cảm ơn bạn đã gửi!"
        )
    await edit_or_send(bot, fb["user_id"], fb["msg_id"], text, back_home_kb())


# ============================== ĐỒNG BỘ VỚI SUNVIW + BROADCAST ==============================
# Bot giữ BẢN SAO danh mục (game, gói, giá, link, online) + cấu hình + danh sách admin, cập nhật mỗi vài giây từ sunviw.
# Mất liên lạc quá SYNC_FRESH giây -> bot tự coi như bảo trì (không bán key); khi sunviw trả lời lại là mở bán lại.
SYNC_EVERY = 6
_sync_lock = None
_sync_state = {"fails": 0, "was_ok": None}
_bc_seen = set()
_bc_lock = None
_loops = []


def apply_catalog(r):
    """Ghi danh mục sunviw vào DB bot. Game có BẤT KỲ bàn nào OFF -> online=0 -> biến mất khỏi 'Mua Key' (sunviw đã tính sẵn)."""
    codes = []
    with tx() as c:
        for i, g in enumerate(r.get("games") or []):
            code = str(g.get("code") or "").lower()
            if not re.fullmatch(r"[a-z0-9_]{1,20}", code):
                continue
            codes.append(code)
            link = str(g.get("link") or "").strip()
            c.execute(
                "INSERT INTO games(code,name,online,sort,api,sell,link,offt) VALUES(?,?,?,?,1,?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET name=excluded.name, online=excluded.online, sort=excluded.sort, api=1, "
                "sell=excluded.sell, link=excluded.link, offt=excluded.offt",
                (code, str(g.get("name") or code)[:30], 1 if g.get("online") else 0, i + 1, 1 if g.get("sell", True) else 0,
                 link if re.fullmatch(r"https?://\S{4,300}", link) else "", ", ".join(str(x) for x in (g.get("off_tables") or []))[:200]),
            )
            keep = set()
            for p in g.get("packages") or []:
                try:
                    days, price = int(p["days"]), int(p["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                if days < 1 or price <= 0:
                    continue
                c.execute(
                    "INSERT INTO packages(game,code,price) VALUES(?,?,?) ON CONFLICT(game,code) DO UPDATE SET price=excluded.price",
                    (code, f"{days}d", price),
                )
                keep.add(f"{days}d")
            for row in c.execute("SELECT id, code FROM packages WHERE game=?", (code,)).fetchall():
                if row["code"] not in keep:
                    c.execute("DELETE FROM packages WHERE id=?", (row["id"],))
        # game cũ (kho nội bộ) hoặc game sunviw không còn bán -> ẩn hẳn
        if codes:
            c.execute(f"UPDATE games SET api=0, online=0 WHERE api=1 AND code NOT IN ({','.join('?' * len(codes))})", codes)
        else:
            c.execute("UPDATE games SET api=0, online=0 WHERE api=1")
        c.execute("UPDATE games SET online=0 WHERE api=0")


def apply_admins(a):
    subs = {}
    for s in (a or {}).get("subs") or []:
        try:
            tg = int(s["tg"])
        except (KeyError, TypeError, ValueError):
            continue
        subs[tg] = {"name": str(s.get("name") or "")[:40], "perms": {str(p) for p in (s.get("perms") or [])}}
    SUBS.clear()
    SUBS.update(subs)


def seed_payload():
    return {k: v for k, v in ENV_CFG.items() if v is not None and v != "" and v != []}


async def sync_once(app):
    """Đồng bộ 1 lần với sunviw -> True nếu thành công."""
    global _sync_lock
    if not api.ready:
        SYNC["err"] = api.why
        return False
    if _sync_lock is None:
        _sync_lock = asyncio.Lock()
    async with _sync_lock:
        try:
            if not SYNC["seeded"]:  # lần đầu: gửi cấu hình config.env lên panel (chỉ điền ô CHƯA có, không ghi đè)
                sr = await api.call("seed", {"cfg": seed_payload()})
                SYNC["seeded"] = bool(sr.get("ok"))
            r = await api.call("catalog")
            if not r.get("ok"):
                raise ApiDown(f"catalog lỗi: {r.get('code')}")
        except ApiDown as e:
            SYNC["err"] = str(e)
            _sync_state["fails"] += 2 if "404" in str(e) else 1
            if _sync_state["was_ok"] is not False:
                log.warning("⚠️ Mất liên lạc sunviw (%s) — bot tạm ẩn Mua Key. Kiểm tra SUNVIW_URL / SUNVIW_BOT_SECRET / SUNVIW_BOT_IPS (404 = sai secret, sai IP hoặc lệch giờ).", e)
            _sync_state["was_ok"] = False
            return False
        apply_catalog(r)
        apply_cfg(r.get("cfg"))
        apply_admins(r.get("admins"))
        SYNC["ok_at"], SYNC["err"] = time.time(), ""
        _sync_state["fails"] = 0
        if _sync_state["was_ok"] is False:
            log.info("✅ Đã kết nối lại sunviw — mở bán trở lại.")
        _sync_state["was_ok"] = True
        for it in r.get("bc") or []:
            if it.get("id") and it["id"] not in _bc_seen:
                _bc_seen.add(it["id"])
                bg(panel_broadcast(app, it))
        return True


async def sync_loop(app):
    while True:
        try:
            ok = await sync_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Lỗi đồng bộ sunviw")
            ok = False
        # sai secret/IP thì giãn nhịp ra (sunviw sẽ tạm khoá IP nếu bị gọi sai dồn dập)
        await asyncio.sleep(SYNC_EVERY if ok else min(SYNC_EVERY * 2 ** min(_sync_state["fails"], 5), 120))


# ---------- Broadcast: gửi thông báo cho TẤT CẢ khách ----------
def bc_targets():
    return [r["id"] for r in db.execute("SELECT id FROM users WHERE blocked=0 ORDER BY id")]


def bc_message(text):
    return f"📢 <b>THÔNG BÁO</b>\n{LINE}\n{esc(text)}"


def _retry_secs(e):
    ra = getattr(e, "retry_after", 1)
    return float(ra.total_seconds() if hasattr(ra, "total_seconds") else ra)


async def do_broadcast(bot, text, progress=None):
    """Gửi lần lượt (≈20 tin/giây, tôn trọng giới hạn Telegram). Khách đã chặn bot bị đánh dấu & bỏ qua lần sau. -> (đã gửi, lỗi, tổng)."""
    global _bc_lock
    if _bc_lock is None:
        _bc_lock = asyncio.Lock()
    async with _bc_lock:
        ids = bc_targets()
        msg, kb = bc_message(text), K([[B("🏠 Vào Bot", callback_data="home")]])
        sent = failed = 0
        for i, uid in enumerate(ids):
            for attempt in (1, 2):
                try:
                    await bot.send_message(uid, msg, reply_markup=kb)
                    sent += 1
                except RetryAfter as e:
                    if attempt == 1:
                        await asyncio.sleep(_retry_secs(e) + 1)
                        continue
                    failed += 1
                except Forbidden:
                    failed += 1
                    db.execute("UPDATE users SET blocked=1 WHERE id=?", (uid,))
                except TelegramError:
                    failed += 1
                break
            await asyncio.sleep(0.05)
            if progress and (i + 1) % 40 == 0:
                await progress(i + 1, len(ids))
        return sent, failed, len(ids)


async def report_broadcast(op, body):
    for _ in range(5):  # báo kết quả về sunviw (panel xem được lịch sử); lỗi mạng thì thử lại
        try:
            await api.call(op, body)
            return
        except ApiDown:
            await asyncio.sleep(5)


async def panel_broadcast(app, it):
    """Thông báo admin soạn ở PANEL sunviw: bot nhận (claim 1 lần) -> gửi -> báo kết quả."""
    try:
        r = await api.call("bc_claim", {"id": it["id"]})
    except ApiDown:
        _bc_seen.discard(it["id"])  # lần đồng bộ sau thử lại
        return
    if not r.get("ok"):
        return  # đã có người nhận / bị hủy
    sent, failed, total = await do_broadcast(app.bot, str(r.get("text") or ""))
    await report_broadcast("bc_done", {"id": it["id"], "sent": sent, "failed": failed})
    for aid in ADMIN_IDS[:1]:
        try:
            await app.bot.send_message(aid, f"📢 Thông báo từ panel đã gửi xong: <b>{sent}/{total}</b> khách (lỗi {failed}).")
        except TelegramError:
            pass


async def run_bot_broadcast(bot, chat, mid, text, by_name):
    """Thông báo admin soạn NGAY TRONG BOT (nút 📢 hoặc /broadcast)."""

    async def progress(done, total):
        await edit_or_send(bot, chat, mid, f"📤 <b>Đang gửi...</b> {done}/{total}", None)

    sent, failed, total = await do_broadcast(bot, text, progress)
    await edit_or_send(
        bot, chat, mid,
        f"✅ <b>ĐÃ GỬI THÔNG BÁO</b>\n{LINE}\n👥 Tổng khách: <b>{total}</b>\n📨 Gửi được: <b>{sent}</b>\n"
        f"⚠️ Lỗi / đã chặn bot: <b>{failed}</b>",
        K([[B("🛠 Bảng admin", callback_data="adm")]]),
    )
    await report_broadcast("bc_log", {"text": text, "sent": sent, "failed": failed, "by": by_name})


def bc_confirm(text):
    n = len(bc_targets())
    kb = K(
        [
            [B(f"✅ Gửi cho {n} khách", callback_data="bc:ok")],
            [B("✏️ Soạn lại", callback_data="bc:go"), B("❌ Hủy", callback_data="adm")],
        ]
    )
    return f"📢 <b>XEM TRƯỚC THÔNG BÁO</b>\n{LINE}\n{bc_message(text)}\n{LINE}\nSẽ gửi tới <b>{n}</b> khách. Xác nhận?", kb


async def post_init(app):
    if not api.ready:
        log.error("❌ %s — bot vẫn chạy nhưng KHÔNG bán được key.", api.why)
    else:
        await sync_once(app)
    _loops.append(asyncio.ensure_future(sync_loop(app)))
    _loops.append(asyncio.ensure_future(purchase_recovery_loop(app)))


async def post_shutdown(app):
    for t in _loops:
        t.cancel()


# ============================== BẢNG ADMIN ==============================
# Admin = ID nằm trong ADMIN_CHAT_IDS (config.env). Bấm /start hoặc /admin sẽ thấy nút "🛠 Bảng Admin".
# Admin chính (ID đầu tiên) tạo ưu đãi / gifcode bằng nút bấm (wizard); admin phụ chỉ duyệt đơn, xem kho.

KIND_NAME = {
    "key_pct": "Giảm % giá key",
    "key_flat": "Giảm tiền giá key",
    "dep_pct": "Nạp tặng thêm %",
    "money": "Tặng tiền vào số dư",
    "key": "Tặng 1 key",
}
WIZ_VALUE_PROMPT = {
    "key_pct": "Nhập <b>% giảm giá key</b> (1–99). Ví dụ: <code>20</code>",
    "key_flat": "Nhập <b>số tiền giảm cho mỗi key</b>. Ví dụ: <code>5000</code> hoặc <code>5k</code>",
    "dep_pct": "Nhập <b>% tặng thêm</b> trên giá trị nạp (1–200). Ví dụ: <code>10</code>",
    "money": "Nhập <b>số tiền tặng</b> vào số dư. Ví dụ: <code>50000</code> hoặc <code>50k</code>",
}
WIZ_ORDER = ["k", "value", "game", "pkg", "min", "secs", "uses"]  # thứ tự các lựa chọn (lùi bước thì xóa từ đó trở đi)
WIZ_FIRST_KEY = {
    "pgroup": "k", "ptype": "k", "gtype": "k", "value": "value", "game": "game", "pkg": "pkg",
    "min": "min", "min_c": "min", "dur": "secs", "dur_c": "secs", "uses": "uses", "uses_c": "uses",
}
WIZ_CMD_STEP = {  # nút nào chỉ có tác dụng ở bước nào (chặn bấm nhầm nút của tin cũ)
    "pg": ("pgroup",), "t": ("ptype", "gtype"), "g": ("game",), "k": ("pkg",), "m": ("min",), "mc": ("min",),
    "d": ("dur",), "dc": ("dur",), "u": ("uses",), "uc": ("uses",), "cc": ("confirm",), "ok": ("confirm",),
}


def admin_panel(uid):
    perms = perms_of(uid)
    n_user = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    n_ord = db.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
    n_fb = db.execute("SELECT COUNT(*) FROM feedback WHERE status='pending'").fetchone()[0]
    n_gift = db.execute("SELECT COUNT(*) FROM giftcodes WHERE used_count<max_uses").fetchone()[0]
    n_pur = db.execute("SELECT COUNT(*) FROM purchases WHERE status='pending'").fetchone()[0]
    text = (
        f"🛠 <b>BẢNG ADMIN</b> · <b>{role_text(uid)}</b>\n{LINE}\n"
        f"👥 Khách: <b>{n_user}</b>\n"
        f"⏳ Chờ duyệt: <b>{n_ord}</b> đơn nạp · <b>{n_fb}</b> feedback\n"
        f"🔥 Ưu đãi đang chạy: <b>{len(active_promos())}</b>\n"
        f"🎁 Gifcode còn lượt: <b>{n_gift}</b>\n"
        + (f"⚠️ Đơn key đang chờ sunviw: <b>{n_pur}</b>\n" if n_pur and is_main(uid) else "")
        + f"{LINE}\n"
    )
    if not is_main(uid):
        have = "\n".join("• " + PERM_NAME[p] for p in BOT_PERMS if p in perms) or "• (chưa có quyền nào)"
        text += f"🔐 <b>Quyền của bạn</b>\n{have}\n<i>Cần thêm quyền? Nhờ admin chính tích thêm ở panel — áp dụng sau vài giây.</i>\n{LINE}\n"
    text += "Chọn chức năng 👇"
    rows = []
    if "promo" in perms:
        rows.append([B("🔥 Tạo Ưu Đãi", callback_data="aw:promo"), B("📋 DS Ưu Đãi", callback_data="adm:promos")])
    if "gift" in perms:
        rows.append([B("🎁 Tạo Gifcode", callback_data="aw:gift"), B("🎟 DS Gifcode", callback_data="adm:gifts")])
    row = []
    if perms & {"deposit", "feedback"}:
        row.append(B("⏳ Chờ Duyệt", callback_data="adm:pending"))
    row.append(B("🎮 Trạng Thái Game", callback_data="adm:stock"))
    rows.append(row)
    if "broadcast" in perms:
        rows.append([B("📢 Thông Báo Toàn Bot", callback_data="bc:go")])
    rows.append([B("📖 Lệnh Khác", callback_data="adm:help"), B("🏠 Trang chủ", callback_data="home")])
    return text, K(rows)


def promo_list_screen():
    rows = db.execute("SELECT * FROM promos ORDER BY id DESC LIMIT 6").fetchall()
    now = int(time.time())
    out, btns = [], []
    for p in rows:
        st = "⏰" if (p["ends"] and p["ends"] <= now) else ("🟢" if p["active"] else "⚪")
        out.append(f"{st} <code>#{p['id']}</code> {promo_line(p)}")
        btns.append(
            [
                B(("⏸ Tắt" if p["active"] else "▶️ Bật") + f" #{p['id']}", callback_data=f"adm:pt:{p['id']}"),
                B(f"🗑 Xóa #{p['id']}", callback_data=f"adm:pd:{p['id']}"),
            ]
        )
    body = "\n".join(out) if out else "<i>Chưa có ưu đãi nào.</i>"
    text = f"📋 <b>DANH SÁCH ƯU ĐÃI</b>\n{LINE}\n{body}\n{LINE}\n🟢 đang chạy · ⚪ đã tắt · ⏰ hết hạn"
    btns.append([B("🔥 Tạo Ưu Đãi", callback_data="aw:promo")])
    btns.append([B("⬅️ Bảng admin", callback_data="adm")])
    return text, K(btns)


def gift_list_screen():
    rows = db.execute("SELECT * FROM giftcodes ORDER BY created DESC LIMIT 8").fetchall()
    out = [
        f"🎟 <code>{esc(g['code'])}</code> · {gift_reward_text(g)} · đã nhập <b>{g['used_count']}/{g['max_uses']}</b>"
        for g in rows
    ]
    body = "\n".join(out) if out else "<i>Chưa có gifcode nào.</i>"
    text = f"🎟 <b>DANH SÁCH GIFCODE</b>\n{LINE}\n{body}\n{LINE}\n<i>Hiển thị 8 mã mới nhất. Chạm vào mã để sao chép.</i>"
    dels = [B(f"🗑 {g['code']}", callback_data=f"adm:gd:{g['code']}") for g in rows if len(g["code"]) <= 40]
    btns = [dels[i : i + 2] for i in range(0, len(dels), 2)]
    btns.append([B("🎁 Tạo Gifcode", callback_data="aw:gift")])
    btns.append([B("⬅️ Bảng admin", callback_data="adm")])
    return text, K(btns)


def wiz_new(ctx, kind, uid):
    ctx.user_data["wiz"] = {"kind": kind, "uid": uid, "hist": [], "step": None}


def wiz_go(ctx, step):
    w = ctx.user_data["wiz"]
    if w["step"]:
        w["hist"].append(w["step"])
    w["step"] = step


def wiz_back(ctx):
    """Lùi 1 bước và xóa các lựa chọn từ bước đó trở đi. Hết lịch sử -> step None (về bảng admin)."""
    w = ctx.user_data["wiz"]
    w["step"] = w["hist"].pop() if w["hist"] else None
    key = WIZ_FIRST_KEY.get(w["step"])
    if key:
        for k in WIZ_ORDER[WIZ_ORDER.index(key) :]:
            w.pop(k, None)


def wiz_next(ctx, step, sub=False):
    """Đi tới bước kế. sub=True: đang ở bước nhập chữ con (vd 'dur_c') -> thoát về bước cha trước khi đi tiếp."""
    w = ctx.user_data["wiz"]
    if sub and w["hist"]:
        w["step"] = w["hist"].pop()
    if w["step"] != step:
        wiz_go(ctx, step)


def wiz_lines(w):
    """Các lựa chọn admin đã chọn tới lúc này."""
    k, out = w.get("k"), []
    if k:
        out.append(f"• Loại: <b>{KIND_NAME[k]}</b>")
    if w.get("value") is not None:
        out.append("• Giá trị: <b>" + (f"{w['value']}%" if k in ("key_pct", "dep_pct") else money(w["value"])) + "</b>")
    if "game" in w:
        gm = db.execute("SELECT name FROM games WHERE code=?", (w["game"],)).fetchone() if w["game"] else None
        out.append("• Game: <b>" + (esc(gm["name"]) if gm else (esc(w["game"]) if w["game"] else "tất cả game")) + "</b>")
    if "pkg" in w:
        out.append("• Gói: <b>" + (pkg_label(w["pkg"]) if w["pkg"] else "tất cả gói") + "</b>")
    if w.get("min"):
        out.append(f"• Nạp tối thiểu: <b>{money(w['min'])}</b>")
    if "secs" in w:
        out.append("• Thời hạn: <b>" + (fmt_span(w["secs"]) if w["secs"] else "không giới hạn") + "</b>")
    if w.get("uses"):
        out.append(f"• Số lượng nhập: <b>{w['uses']}</b> lượt")
    return "\n".join(out) + "\n\n" if out else ""


def wiz_render(ctx, chat, mid, uid=None):
    """Vẽ bước hiện tại của wizard -> (text, kb). Bước nhập chữ sẽ đặt ctx.user_data['await']."""
    ctx.user_data.pop("await", None)
    w = ctx.user_data.get("wiz")
    if not w or not w.get("step"):
        return admin_panel(uid or (w or {}).get("uid") or MAIN_ADMIN)
    is_promo = w["kind"] == "promo"
    head = (f"🔥 <b>TẠO ƯU ĐÃI</b>\n{LINE}\n" if is_promo else f"🎁 <b>TẠO GIFCODE</b>\n{LINE}\n") + wiz_lines(w)
    nav = [B("⬅️ Quay lại", callback_data="aw:b"), B("❌ Hủy", callback_data="adm")]
    step = w["step"]

    def ask(text):  # bước nhập chữ
        ctx.user_data["await"] = {"type": "wiz", "chat": chat, "msg": mid}
        return head + text, K([nav])

    if step == "pgroup":
        return head + "Chọn loại ưu đãi muốn tạo 👇", K(
            [
                [B("🛒 Ưu đãi mua key", callback_data="aw:pg:key")],
                [B("💰 Ưu đãi nạp tiền", callback_data="aw:pg:dep")],
                nav,
            ]
        )
    if step == "ptype":
        return head + "🛒 <b>Ưu đãi mua key</b> — chọn cách giảm 👇", K(
            [
                [B("📉 Giảm theo %", callback_data="aw:t:key_pct")],
                [B("💵 Giảm số tiền cố định", callback_data="aw:t:key_flat")],
                nav,
            ]
        )
    if step == "gtype":
        return head + "Chọn loại phần thưởng của gifcode 👇", K(
            [
                [B("💰 Nạp tiền: tặng thêm % giá trị đơn", callback_data="aw:t:dep_pct")],
                [B("🛒 Mua key: giảm theo %", callback_data="aw:t:key_pct")],
                [B("🛒 Mua key: giảm số tiền", callback_data="aw:t:key_flat")],
                [B("💲 Tặng tiền vào số dư", callback_data="aw:t:money")],
                [B("🔑 Tặng 1 key", callback_data="aw:t:key")],
                nav,
            ]
        )
    if step == "value":
        return ask(WIZ_VALUE_PROMPT[w["k"]])
    if step == "game":
        games = games_all()
        btns = [B(g["name"], callback_data=f"aw:g:{g['code']}") for g in games]
        rows = [[B("🌐 Tất cả game", callback_data="aw:g:*")]] if is_promo else []
        rows += [btns[i : i + 2] for i in range(0, len(btns), 2)]
        return head + "Chọn <b>game</b> áp dụng 👇", K(rows + [nav])
    if step == "pkg":
        if w.get("game"):
            codes = [r["code"] for r in db.execute("SELECT code FROM packages WHERE game=? ORDER BY price", (w["game"],))]
        else:
            codes = [r["code"] for r in db.execute(
                "SELECT p.code, MIN(p.price) mp FROM packages p JOIN games g ON g.code=p.game AND g.api=1 GROUP BY p.code ORDER BY mp")]
        btns = [B(pkg_label(c), callback_data=f"aw:k:{c}") for c in codes]
        rows = [[B("📦 Tất cả gói", callback_data="aw:k:*")]] if is_promo else []
        rows += [btns[i : i + 3] for i in range(0, len(btns), 3)]
        return head + "Chọn <b>gói</b> áp dụng 👇", K(rows + [nav])
    if step == "min":
        rows = [
            [B("Không tối thiểu", callback_data="aw:m:0")],
            [B(money(a), callback_data=f"aw:m:{a}") for a in (50000, 100000, 200000)],
            [B(money(500000), callback_data="aw:m:500000"), B("✏️ Nhập số khác", callback_data="aw:mc")],
        ]
        return head + "Mức <b>nạp tối thiểu</b> để được ưu đãi? 👇", K(rows + [nav])
    if step == "min_c":
        return ask("Nhập mức nạp tối thiểu. Ví dụ: <code>50000</code> hoặc <code>50k</code> (<code>0</code> = không tối thiểu)")
    if step == "dur":
        rows = [
            [B("1 ngày", callback_data="aw:d:1d"), B("3 ngày", callback_data="aw:d:3d")],
            [B("7 ngày", callback_data="aw:d:7d"), B("30 ngày", callback_data="aw:d:30d")],
            [B("♾ Không giới hạn", callback_data="aw:d:0"), B("✏️ Nhập khác", callback_data="aw:dc")],
        ]
        return head + "Ưu đãi chạy trong bao lâu? 👇", K(rows + [nav])
    if step == "dur_c":
        return ask("Nhập thời hạn: <code>30m</code> (phút), <code>12h</code> (giờ), <code>2d</code> (ngày) hoặc <code>0</code> = không giới hạn")
    if step == "uses":
        rows = [
            [B(str(n), callback_data=f"aw:u:{n}") for n in (1, 5, 10)],
            [B(str(n), callback_data=f"aw:u:{n}") for n in (50, 100)] + [B("✏️ Nhập số khác", callback_data="aw:uc")],
        ]
        return head + "<b>Số lượng</b>: gifcode cho nhập tối đa bao nhiêu lần? (mỗi khách chỉ nhập 1 lần / mã) 👇", K(rows + [nav])
    if step == "uses_c":
        return ask("Nhập số lượng được nhập (1 – 100000). Ví dụ: <code>30</code>")
    if step == "code_c":
        return ask("Nhập mã gifcode riêng (4–20 ký tự: chữ, số, <code>-</code> hoặc <code>_</code>). Ví dụ: <code>TET2026</code>")
    # confirm
    if is_promo:
        p = {"kind": w["k"], "value": w["value"], "game": w.get("game"), "pkg_code": w.get("pkg"), "min_amount": w.get("min", 0)}
        when = f"<b>{fmt_span(w['secs'])}</b>" if w["secs"] else "<b>không giới hạn thời gian</b>"
        body = (
            f"{'💰' if w['k'] == 'dep_pct' else '🛒'} {promo_desc(p)}\n"
            f"⏳ Áp dụng {when}\n\n<i>Tạo xong bot sẽ tự đăng thông báo lên box.</i>"
        )
        rows = [[B("✅ Tạo ưu đãi", callback_data="aw:ok")]]
    else:
        w.setdefault("code", gen_code())
        gft = {"kind": w["k"], "amount": w.get("value", 0), "game": w.get("game"), "pkg_code": w.get("pkg")}
        body = (
            f"🎟 Mã: <code>{esc(w['code'])}</code>\n"
            f"Phần thưởng: {gift_reward_text(gft)}\n"
            f"🔢 Số lượng nhập: <b>{w.get('uses', 1)}</b> lượt (mỗi khách 1 lần)\n\n"
            f"<i>Khách nhập mã ở mục Nạp Tiền / Mua Key → 🎁 Nhập Gifcode.</i>"
        )
        rows = [[B("✅ Tạo gifcode", callback_data="aw:ok"), B("✏️ Đặt mã riêng", callback_data="aw:cc")]]
    return f"{head}📝 <b>XÁC NHẬN</b>\n{body}", K(rows + [nav])


def wiz_apply(ctx, w, cmd, arg):
    """Áp dụng 1 lựa chọn bằng nút vào wizard. Trả về chuỗi cảnh báo nếu lựa chọn không hợp lệ."""
    is_promo = w["kind"] == "promo"
    if cmd == "pg":
        if arg == "key":
            wiz_go(ctx, "ptype")
        elif arg == "dep":
            w["k"] = "dep_pct"
            wiz_go(ctx, "value")
        else:
            return "Lựa chọn không hợp lệ."
    elif cmd == "t":
        ok = ("key_pct", "key_flat", "dep_pct") if is_promo else ("dep_pct", "key_pct", "key_flat", "money", "key")
        if arg not in ok:
            return "Lựa chọn không hợp lệ."
        w["k"] = arg
        wiz_go(ctx, "game" if arg == "key" else "value")
    elif cmd == "g":
        if arg == "*":
            if not is_promo:
                return "Gifcode tặng key phải chọn 1 game cụ thể."
            w["game"] = None
        elif db.execute("SELECT 1 FROM games WHERE code=? AND api=1", (arg,)).fetchone():
            w["game"] = arg
        else:
            return "Game không tồn tại."
        wiz_go(ctx, "pkg")
    elif cmd == "k":
        if arg == "*":
            if not is_promo:
                return "Gifcode tặng key phải chọn 1 gói cụ thể."
            w["pkg"] = None
        else:
            sql, args = "SELECT 1 FROM packages WHERE code=?", [arg]
            if w.get("game"):
                sql, args = sql + " AND game=?", args + [w["game"]]
            if not db.execute(sql, args).fetchone():
                return "Gói không tồn tại."
            w["pkg"] = arg
        wiz_go(ctx, "dur" if is_promo else "uses")
    elif cmd == "m":
        if not arg.isdigit():
            return "Lựa chọn không hợp lệ."
        w["min"] = int(arg)
        wiz_go(ctx, "dur")
    elif cmd == "mc":
        wiz_go(ctx, "min_c")
    elif cmd == "d":
        secs = parse_duration(arg)
        if secs is None:
            return "Lựa chọn không hợp lệ."
        w["secs"] = secs
        wiz_go(ctx, "confirm")
    elif cmd == "dc":
        wiz_go(ctx, "dur_c")
    elif cmd == "u":
        if not arg.isdigit() or not 1 <= int(arg) <= 100000:
            return "Lựa chọn không hợp lệ."
        w["uses"] = int(arg)
        wiz_go(ctx, "confirm")
    elif cmd == "uc":
        wiz_go(ctx, "uses_c")
    elif cmd == "cc":
        wiz_go(ctx, "code_c")


async def wiz_text(ctx, st, text):
    """Admin gõ chữ ở bước nhập tay của wizard (giá trị / mức nạp tối thiểu / thời hạn / số lượng / mã riêng)."""
    w = ctx.user_data.get("wiz")
    if not w or not w.get("step"):
        return
    chat, mid, step = st["chat"], st["msg"], w["step"]
    err = nxt = None
    sub = step.endswith("_c")  # bước nhập tay con của một bước nút bấm
    if step == "value":
        v = parse_value(w["k"], text)
        if v is None:
            err = "⚠️ <b>Giá trị không hợp lệ.</b> Hãy nhập lại."
        else:
            w["value"] = v
            nxt = ("min" if w["k"] == "dep_pct" else "game") if w["kind"] == "promo" else "uses"
    elif step == "min_c":
        v = parse_amount(text)
        if v is None:
            err = "⚠️ <b>Số tiền không hợp lệ.</b> Ví dụ: <code>50000</code> hoặc <code>50k</code>."
        else:
            w["min"], nxt = v, "dur"
    elif step == "dur_c":
        v = parse_duration(text)
        if v is None:
            err = "⚠️ <b>Thời hạn không hợp lệ.</b> Ví dụ: <code>12h</code>, <code>2d</code>."
        else:
            w["secs"], nxt = v, "confirm"
    elif step == "uses_c":
        if re.fullmatch(r"[0-9]{1,6}", text) and 1 <= int(text) <= 100000:
            w["uses"], nxt = int(text), "confirm"
        else:
            err = "⚠️ <b>Số lượng không hợp lệ.</b> Nhập số từ 1 đến 100000."
    elif step == "code_c":
        code = text.upper()
        if re.fullmatch(r"[A-Z0-9_-]{4,20}", code):
            w["code"], nxt = code, "confirm"
        else:
            err = "⚠️ <b>Mã không hợp lệ.</b> Dùng 4–20 ký tự: chữ, số, <code>-</code> hoặc <code>_</code>."
    else:
        return  # bước chọn bằng nút: bỏ qua chữ gõ vào
    if err:
        t, kb = wiz_render(ctx, chat, mid, w["uid"])
        await edit_or_send(ctx.bot, chat, mid, f"{t}\n\n{err}", kb)
        return
    wiz_next(ctx, nxt, sub=sub)
    t, kb = wiz_render(ctx, chat, mid, w["uid"])
    await edit_or_send(ctx.bot, chat, mid, t, kb)


async def wiz_finish(q, ctx, w, uid):
    """Bấm ✅ Tạo ở bước xác nhận."""
    if w["kind"] == "promo":
        if "value" not in w or "secs" not in w:
            return "Thiếu thông tin, hãy tạo lại."
        p = create_promo(w["k"], w["value"], w.get("game"), w.get("pkg"), w.get("min", 0), w["secs"])
        ctx.user_data.pop("wiz", None)
        posted = await post_box(ctx.bot, promo_box_text(p))  # báo ưu đãi mới lên box (kèm thời gian áp dụng)
        note = (
            "📢 Đã đăng thông báo lên box."
            if posted
            else "⚠️ Chưa đăng được lên box (chưa cấu hình FEEDBACK_CHAT_ID / NOTIFY_CHAT_ID, hoặc bot chưa có quyền gửi tin)."
        )
        text = f"🔥 <b>ĐÃ TẠO ƯU ĐÃI #{p['id']}</b>\n{LINE}\n{promo_line(p)}\n\n{note}"
        kb = K([[B("🔥 Tạo thêm", callback_data="aw:promo"), B("🛠 Bảng admin", callback_data="adm")]])
    else:
        if "uses" not in w or (w["k"] == "key" and not (w.get("game") and w.get("pkg"))) or (w["k"] != "key" and "value" not in w):
            return "Thiếu thông tin, hãy tạo lại."
        g = create_gift(uid, w["k"], w.get("value", 0), w.get("game"), w.get("pkg"), w["uses"], w["code"])
        if not g:
            return "❌ Mã này đã tồn tại. Bấm ✏️ Đặt mã riêng để đổi mã khác."
        ctx.user_data.pop("wiz", None)
        text = (
            f"🎁 <b>ĐÃ TẠO GIFCODE</b>\n{LINE}\n"
            f"🎟 Mã: <code>{esc(g['code'])}</code>\n"
            f"Phần thưởng: {gift_reward_text(g)}\n"
            f"🔢 Số lượng nhập: <b>{g['max_uses']}</b> lượt\n\n"
            f"<i>Chạm vào mã để sao chép rồi gửi cho khách.\nKhách nhập ở mục Nạp Tiền / Mua Key → 🎁 Nhập Gifcode.</i>"
        )
        kb = K([[B("🎁 Tạo thêm", callback_data="aw:gift"), B("🛠 Bảng admin", callback_data="adm")]])
    await show(q, ctx, text, kb)


async def wiz_route(q, ctx, data, uid):
    chat, mid = q.message.chat_id, q.message.message_id
    parts = data.split(":", 2)  # aw:<lệnh>[:<tham số>]
    cmd, arg = parts[1], (parts[2] if len(parts) > 2 else "")
    alert = None
    if cmd in ("promo", "gift"):
        if not has_perm(uid, cmd):
            return "⛔ Bạn chưa được cấp quyền này."
        wiz_new(ctx, cmd, uid)
        wiz_go(ctx, "pgroup" if cmd == "promo" else "gtype")
    else:
        w = ctx.user_data.get("wiz")
        if w and (w.get("uid") != uid or not has_perm(uid, w["kind"])):
            ctx.user_data.pop("wiz", None)
            await show(q, ctx, *admin_panel(uid))
            return "⛔ Bạn chưa được cấp quyền này."
        if not w or not w.get("step"):
            await show(q, ctx, *admin_panel(uid))
            return "Phiên tạo đã hết hạn, hãy mở lại từ Bảng Admin."
        if cmd == "b":
            wiz_back(ctx)
            if not w["step"]:
                ctx.user_data.pop("wiz", None)
        elif w["step"] in WIZ_CMD_STEP.get(cmd, ()):
            if cmd == "ok":
                return await wiz_finish(q, ctx, w, uid)
            alert = wiz_apply(ctx, w, cmd, arg)
        # nút của tin cũ (không đúng bước hiện tại): bỏ qua, chỉ vẽ lại bước hiện tại
    await show(q, ctx, *wiz_render(ctx, chat, mid, uid))
    return alert


async def admin_route(q, ctx, data, uid):
    chat = q.message.chat_id
    if data == "adm":
        ctx.user_data.pop("wiz", None)
        ctx.user_data.pop("bc_draft", None)
        await show(q, ctx, *admin_panel(uid))
        return
    back = K([[B("⬅️ Bảng admin", callback_data="adm")]])
    if data == "adm:pending":
        if not (has_perm(uid, "deposit") or has_perm(uid, "feedback")):
            return "⛔ Bạn chưa được cấp quyền duyệt đơn / feedback."
        await send_pending(ctx.bot, chat, uid)
        return
    if data == "adm:stock":
        await show(q, ctx, stock_text(is_main(uid)), back)
        return
    if data == "adm:help":
        await show(q, ctx, admin_help(uid), back)
        return
    if data.startswith("aw:"):  # wizard: quyền kiểm tra theo loại (promo / gift) trong wiz_route
        return await wiz_route(q, ctx, data, uid)
    if data in ("adm:promos",) or data.startswith(("adm:pt:", "adm:pd:")):
        if not has_perm(uid, "promo"):
            return "⛔ Bạn chưa được cấp quyền tạo / sửa ưu đãi."
        if data.startswith(("adm:pt:", "adm:pd:")):
            pid = int(data.split(":")[2])
            if data.startswith("adm:pd:"):
                db.execute("DELETE FROM promos WHERE id=?", (pid,))
            else:
                db.execute("UPDATE promos SET active=1-active WHERE id=?", (pid,))
        await show(q, ctx, *promo_list_screen())
        return
    if data == "adm:gifts" or data.startswith("adm:gd:"):
        if not has_perm(uid, "gift"):
            return "⛔ Bạn chưa được cấp quyền tạo / xóa gifcode."
        if data.startswith("adm:gd:"):
            code = data[7:]
            db.execute("DELETE FROM giftcodes WHERE code=?", (code,))
            db.execute("DELETE FROM giftcode_uses WHERE code=?", (code,))
        await show(q, ctx, *gift_list_screen())
        return


# ============================== LỆNH ==============================


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("await", None)
    u = get_user(update.effective_user)
    await safe_delete(update.message)
    await fetch_owned(u["id"])
    text, kb = home_screen(u)
    await ctx.bot.send_message(update.effective_chat.id, text, reply_markup=kb)


def admin_help(uid):
    perms = perms_of(uid)
    out = [f"🛠 <b>LỆNH ADMIN</b> · {role_text(uid)}\n{LINE}\n<i>Phần lớn thao tác đã có nút trong /admin. Lệnh gõ tay dành cho thao tác nhanh:</i>\n"]
    out.append("<code>/admin</code>  mở bảng admin (nút bấm)")
    if "gift" in perms:
        out.append(
            "<code>/gifcode dep 10 [lượt] [MÃ]</code>\n   nạp tặng thêm 10% giá trị đơn\n"
            "<code>/gifcode keypct 20 [lượt] [MÃ]</code>\n   giảm 20% giá key\n"
            "<code>/gifcode keyflat 5000 [lượt] [MÃ]</code>\n   giảm 5.000đ giá key\n"
            "<code>/gifcode money 50000 [lượt] [MÃ]</code>\n   tặng tiền vào số dư\n"
            "<code>/gifcode key lc79 30d [lượt] [MÃ]</code>\n   tặng 1 key (game · gói: 1d 3d 7d 30d 60d 90d) — key do sunviw sinh"
        )
    out.append("<code>/game</code> | <code>/stock</code>  xem trạng thái từng game (online / OFF bàn nào) &amp; giá gói")
    if "promo" in perms:
        out.append(
            "<code>/promo</code>  xem danh sách ưu đãi\n"
            "<code>/promo keypct 20 [game=lc79] [goi=30d] [han=2d]</code>\n   giảm 20% giá key\n"
            "<code>/promo keyflat 5000 [game=sun] [goi=7d] [han=2d]</code>\n   giảm 5.000đ mỗi key\n"
            "<code>/promo dep 10 [min=50k] [han=7d]</code>\n   nạp tặng thêm 10% giá trị nạp\n"
            "<code>/promo off 3</code> | <code>/promo on 3</code> | <code>/promo del 3</code>\n   tắt / bật / xóa ưu đãi số 3"
        )
    else:
        out.append("<code>/promo</code>  xem danh sách ưu đãi")
    if perms & {"deposit", "feedback"}:
        out.append("<code>/pending</code>  xem đơn nạp &amp; feedback đang chờ duyệt")
    if "broadcast" in perms:
        out.append("<code>/broadcast Nội dung</code>  gửi thông báo tới TẤT CẢ khách (có xem trước + xác nhận)")
    out.append("<code>/id</code>  xem ID của bạn / ID của chat (gõ trong box để lấy ID box)")
    out.append(
        f"{LINE}\n<i>Game, giá gói, link game, CSKH, box... chỉnh ở <b>panel sunviw → tab Bot Tele</b>. "
        "Quyền của admin phụ do admin chính tích trong chi tiết key admin phụ.</i>"
    )
    return "\n".join(out)


def admin_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not is_admin(update.effective_user.id):
            return
        await fn(update, ctx)

    return wrapper


def perm_only(perm, fn):
    """Lệnh chỉ dành cho admin CÓ QUYỀN `perm` (người không có quyền: im lặng nếu không phải admin, báo rõ nếu là admin thiếu quyền)."""

    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        if not is_admin(uid):
            return
        if not has_perm(uid, perm):
            await update.message.reply_text(f"⛔ Bạn chưa được cấp quyền <b>{PERM_NAME[perm]}</b>. Nhờ admin chính tích thêm ở panel.")
            return
        await fn(update, ctx)

    return wrapper


@admin_only
async def cmd_admin(update, ctx):
    ctx.user_data.pop("wiz", None)
    ctx.user_data.pop("await", None)
    text, kb = admin_panel(update.effective_user.id)
    await update.message.reply_text(text, reply_markup=kb)


async def cmd_gifcode(update, ctx):
    uid = update.effective_user.id
    a = ctx.args
    try:
        kind = GIFT_KINDS[a[0].lower()]
        game = pkg = None
        value = 0
        if kind == "key":
            game, pkg, rest = a[1].lower(), a[2].lower(), a[3:]
            if not db.execute(
                "SELECT 1 FROM packages p JOIN games g ON g.code=p.game AND g.api=1 WHERE p.game=? AND p.code=?", (game, pkg)
            ).fetchone():
                await update.message.reply_text("❌ Game / gói không tồn tại. Xem /game")
                return
        else:
            value = parse_value(kind, a[1])
            if value is None:
                raise ValueError
            rest = a[2:]
        uses = int(rest[0]) if rest else 1
        code = rest[1].upper() if len(rest) > 1 else gen_code()
        if uses < 1 or not re.fullmatch(r"[A-Z0-9_-]{4,20}", code):
            raise ValueError
    except (KeyError, IndexError, ValueError):
        await update.message.reply_text(GIFT_USAGE)
        return
    g = create_gift(uid, kind, value, game, pkg, uses, code)
    if not g:
        await update.message.reply_text("❌ Mã này đã tồn tại, chọn mã khác.")
        return
    await update.message.reply_text(
        f"🎁 <b>ĐÃ TẠO GIFCODE</b>\n{LINE}\nMã: <code>{esc(code)}</code>\n"
        f"Phần thưởng: {gift_reward_text(g)}\nSố lượng nhập: <b>{uses}</b> lượt"
    )


@admin_only
async def cmd_moved(update, ctx):
    await update.message.reply_text(
        "ℹ️ Key, giá gói, game và link game giờ do <b>sunviw</b> quản lý.\n"
        "Admin chính chỉnh ở <b>panel sunviw → tab Bot Tele</b> (giá gốc, link game, bật/tắt bán).\n"
        "Xem trạng thái game: /game"
    )


def stock_text(detail=False):
    rows = games_all()
    out = []
    for g in rows:
        pk = db.execute("SELECT * FROM packages WHERE game=? ORDER BY price", (g["code"],)).fetchall()
        if not g["sell"]:
            st = "⚪ tắt bán (panel)"
        elif g["offt"]:
            st = "🔴 OFF — ẩn khỏi Mua Key"
        elif not pk:
            st = "⚪ chưa có giá gói"
        else:
            st = "🟢 Online"
        out.append(f"\n<b>{esc(g['name'])}</b> (<code>{g['code']}</code>) — {st}")
        if g["offt"]:
            out.append(f"   ⛔ Bàn đang OFF: {esc(g['offt'])}")
        if pk:
            out.append("   " + " · ".join(f"{pkg_label(p['code'])} {money(p['price'])}" for p in pk))
        if detail and g["link"]:
            out.append(f"   🔗 {esc(g['link'])}")
    n_pur = db.execute("SELECT COUNT(*) FROM purchases WHERE status='pending'").fetchone()[0]
    tail = f"\n\n🔌 Kết nối sunviw: " + ("✅ ổn" if api_fresh() else f"❌ mất liên lạc ({esc(SYNC['err'] or 'chưa đồng bộ')})")
    if n_pur:
        tail += f"\n⏳ Đơn key đang chờ: <b>{n_pur}</b>"
    return f"🎮 <b>TRẠNG THÁI GAME</b>\n{LINE}" + ("".join("\n" + x for x in out) if out else "\nChưa lấy được danh mục từ sunviw.") + tail


@admin_only
async def cmd_stock(update, ctx):
    await update.message.reply_text(stock_text(is_main(update.effective_user.id)))


async def send_pending(bot, chat_id, uid):
    rows = db.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id").fetchall() if has_perm(uid, "deposit") else []
    fbs = db.execute("SELECT * FROM feedback WHERE status='pending' ORDER BY id").fetchall() if has_perm(uid, "feedback") else []
    if not rows and not fbs:
        await bot.send_message(chat_id, "✅ Không có đơn nạp / feedback nào đang chờ.")
        return
    for o in rows:
        m = await bot.send_message(chat_id, order_card(o), reply_markup=order_kb(o["id"]))
        db.execute("INSERT INTO order_msgs(order_id,chat_id,msg_id) VALUES(?,?,?)", (o["id"], chat_id, m.message_id))
    for fb in fbs:
        await bot.send_photo(chat_id, fb["photo_id"], caption=fb_card(fb), reply_markup=fb_kb(fb["id"]))


@admin_only
async def cmd_pending(update, ctx):
    uid = update.effective_user.id
    if not (has_perm(uid, "deposit") or has_perm(uid, "feedback")):
        await update.message.reply_text("⛔ Bạn chưa được cấp quyền duyệt đơn nạp / feedback.")
        return
    await send_pending(ctx.bot, update.effective_chat.id, uid)


async def cmd_broadcast(update, ctx):
    uid = update.effective_user.id
    text = (update.message.text or "").partition(" ")[2].strip()
    if not text:
        ctx.user_data.pop("bc_draft", None)
        msg = await update.message.reply_text(PROMPTS["bc"], reply_markup=K([[B("⬅️ Quay lại", callback_data="adm")]]))
        ctx.user_data["await"] = {"type": "bc", "chat": msg.chat_id, "msg": msg.message_id, "back": None}
        return
    if len(text) > BC_MAX:
        await update.message.reply_text(f"⚠️ Quá dài ({len(text)}/{BC_MAX} ký tự). Hãy rút gọn.")
        return
    ctx.user_data["bc_draft"] = text
    t, kb = bc_confirm(text)
    await update.message.reply_text(t, reply_markup=kb)


PROMO_USAGE = (
    "<b>Cú pháp</b> (phần trong [ ] là tùy chọn, bỏ trống = áp dụng tất cả / không hết hạn):\n"
    "<code>/promo keypct 20 [game=lc79] [goi=30d] [han=2d]</code>  giảm 20% giá key\n"
    "<code>/promo keyflat 5000 [game=sun] [goi=7d] [han=2d]</code>  giảm 5.000đ mỗi key\n"
    "<code>/promo dep 10 [min=50k] [han=7d]</code>  nạp tặng thêm 10%\n"
    "<code>/promo off 3</code> | <code>on 3</code> | <code>del 3</code>\n"
    "<i>han = 30m / 12h / 7d.  Nhiều ưu đãi trùng nhau: khách được mức có lợi nhất (không cộng dồn).\n"
    "Tạo xong bot tự đăng thông báo (kèm số ngày áp dụng) lên box.</i>"
)


def parse_promo_args(a):
    """['keypct','20','game=g1','han=2d'] -> (kind, value, game, pkg, min_amount, secs). Sai cú pháp -> ValueError."""
    kind = PROMO_KINDS.get(a[0].lower())
    if kind is None or len(a) < 2:
        raise ValueError
    if kind == "key_flat":
        value = parse_amount(a[1])
        if value is None or value < 1:
            raise ValueError
    else:
        if not a[1].isdigit():
            raise ValueError
        value = int(a[1])
        if not 1 <= value <= (200 if kind == "dep_pct" else 99):
            raise ValueError
    opts = {}
    for t in a[2:]:
        k, _, v = t.partition("=")
        if not v:
            raise ValueError
        opts[k.lower()] = v
    game, pkg = opts.pop("game", None), opts.pop("goi", None)
    game, pkg = (game or "").lower() or None, (pkg or "").lower() or None
    min_amount = parse_amount(opts.pop("min", "0"))
    secs = parse_duration(opts.pop("han", "0"))
    if opts or min_amount is None or secs is None:  # còn tham số lạ / giá trị sai
        raise ValueError
    if kind == "dep_pct" and (game or pkg):
        raise ValueError
    if kind != "dep_pct" and min_amount:
        raise ValueError
    return kind, value, game, pkg, min_amount, secs


@admin_only
async def cmd_promo(update, ctx):
    a = ctx.args
    uid = update.effective_user.id
    reply = update.message.reply_text
    if not a:  # xem danh sách: admin nào cũng xem được
        rows = db.execute("SELECT * FROM promos ORDER BY id DESC LIMIT 20").fetchall()
        now = int(time.time())
        out = []
        for p in rows:
            st = "⏰" if (p["ends"] and p["ends"] <= now) else ("🟢" if p["active"] else "⚪")
            out.append(f"{st} <code>#{p['id']}</code> {promo_line(p)}")
        body = "\n".join(out) if out else "Chưa có ưu đãi nào."
        await reply(f"🔥 <b>DANH SÁCH ƯU ĐÃI</b>\n{LINE}\n{body}\n{LINE}\n🟢 đang chạy · ⚪ đã tắt · ⏰ hết hạn\n\n{PROMO_USAGE}")
        return
    if not has_perm(uid, "promo"):
        await reply("⛔ Bạn chưa được cấp quyền <b>Tạo / sửa ưu đãi</b>. Nhờ admin chính tích thêm ở panel.")
        return
    sub = a[0].lower()
    if sub in ("on", "off", "del"):
        if len(a) != 2 or not a[1].isdigit():
            await reply(f"Cú pháp: <code>/promo {sub} 3</code>")
            return
        pid = int(a[1])
        if sub == "del":
            n = db.execute("DELETE FROM promos WHERE id=?", (pid,)).rowcount
        else:
            n = db.execute("UPDATE promos SET active=? WHERE id=?", (1 if sub == "on" else 0, pid)).rowcount
        label = {"on": "🟢 Đã bật", "off": "⚪ Đã tắt", "del": "🗑 Đã xóa"}[sub]
        await reply(f"{label} ưu đãi #{pid}." if n else "❌ Không có ưu đãi này.")
        return
    try:
        kind, value, game, pkg, min_amount, secs = parse_promo_args(a)
    except ValueError:
        await reply(f"⚠️ Sai cú pháp.\n\n{PROMO_USAGE}")
        return
    if game and not db.execute("SELECT 1 FROM games WHERE code=? AND api=1", (game,)).fetchone():
        await reply("❌ Game không tồn tại. Xem /game")
        return
    if pkg and not db.execute(
        "SELECT 1 FROM packages p JOIN games g ON g.code=p.game AND g.api=1 WHERE p.code=?" + (" AND p.game=?" if game else ""),
        (pkg, game) if game else (pkg,),
    ).fetchone():
        await reply("❌ Gói không tồn tại. Xem /game")
        return
    p = create_promo(kind, value, game, pkg, min_amount, secs)
    pid = p["id"]
    posted = await post_box(ctx.bot, promo_box_text(p))  # báo ưu đãi mới lên box (kèm thời gian áp dụng)
    box_note = (
        "📢 Đã đăng thông báo lên box."
        if posted
        else "⚠️ Chưa đăng được lên box (chưa cấu hình FEEDBACK_CHAT_ID / NOTIFY_CHAT_ID, hoặc bot chưa có quyền gửi tin)."
    )
    await reply(
        f"🔥 <b>ĐÃ TẠO ƯU ĐÃI #{pid}</b>\n{LINE}\n{promo_line(p)}\n\n{box_note}\nTắt: <code>/promo off {pid}</code>"
    )


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Hiện ID của bạn và ID của chat hiện tại. Gõ trong box (nhóm) để lấy FEEDBACK_CHAT_ID."""
    msg, u, c = update.effective_message, update.effective_user, update.effective_chat
    await msg.reply_text(f"🆔 ID của bạn: <code>{u.id}</code>\n💬 ID chat này: <code>{c.id}</code>")


async def on_error(update, ctx):
    log.error("Lỗi không xử lý được", exc_info=ctx.error)


# ============================== KHỞI CHẠY ==============================


def register(app: Application):
    pv = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=pv))
    app.add_handler(CommandHandler("admin", cmd_admin, filters=pv))
    app.add_handler(CommandHandler("gifcode", perm_only("gift", cmd_gifcode), filters=pv))
    app.add_handler(CommandHandler(["addkey", "addgame", "setprice"], cmd_moved, filters=pv))  # đã chuyển sang panel sunviw
    app.add_handler(CommandHandler(["game", "stock"], cmd_stock, filters=pv))
    app.add_handler(CommandHandler("promo", cmd_promo, filters=pv))
    app.add_handler(CommandHandler("pending", cmd_pending, filters=pv))
    app.add_handler(CommandHandler("broadcast", perm_only("broadcast", cmd_broadcast), filters=pv))
    app.add_handler(CommandHandler(["id", "chatid"], cmd_id))  # dùng được cả trong nhóm (sub-admin dùng để lấy Chat ID khi nhập key)
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & pv, on_text))
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & pv, on_photo))  # ảnh thường + ảnh gửi dạng file
    app.add_error_handler(on_error)


def main():
    if not TOKEN:
        raise SystemExit("Chưa có BOT_TOKEN. Mở file config.env và điền token từ @BotFather.")
    app = (
        ApplicationBuilder().token(TOKEN).defaults(Defaults(parse_mode=ParseMode.HTML))
        .post_init(post_init).post_shutdown(post_shutdown).build()
    )
    register(app)
    if not ADMIN_IDS:
        log.warning("Chưa cấu hình ADMIN_CHAT_IDS: đơn nạp sẽ được lưu ở trạng thái chờ, dùng /pending khi đã có admin.")
    log.info("Bot đang chạy...")
    # Python 3.14+ không còn tự tạo event loop -> tự tạo để không bị lỗi
    # "There is no current event loop in thread 'MainThread'".
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
