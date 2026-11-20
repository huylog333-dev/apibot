#!/bin/sh
# Khởi động bot trên Railway.
set -e

# shop.db (số dư, đơn nạp, gifcode, ưu đãi...) PHẢI nằm trên Volume,
# nếu không mỗi lần deploy lại Railway sẽ xóa sạch dữ liệu.
# Gắn Volume vào service (Mount path: /data) -> Railway tự đặt RAILWAY_VOLUME_MOUNT_PATH.
if [ -z "$DB_PATH" ] && [ -n "$RAILWAY_VOLUME_MOUNT_PATH" ]; then
  export DB_PATH="$RAILWAY_VOLUME_MOUNT_PATH/shop.db"
fi

if [ -n "$DB_PATH" ]; then
  mkdir -p "$(dirname "$DB_PATH")"
  echo "[start] shop.db: $DB_PATH"
else
  echo "[start] !!! CANH BAO: chua gan Volume / chua dat DB_PATH -> shop.db nam trong container va se MAT khi redeploy. Xem DEPLOY_RAILWAY.md muc 3."
fi

exec python -u bot.py
