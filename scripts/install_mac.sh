#!/bin/zsh
# Установка мониторинга на этот Mac (фоновая служба launchd: стартует при входе в систему,
# перезапускается при сбое; площадки проверяются каждые interval_minutes из config.yaml).
# Запуск: zsh scripts/install_mac.sh        Удаление: zsh scripts/install_mac.sh --remove
set -e
APP="$HOME/Library/Application Support/tender-monitor"
PLIST="$HOME/Library/LaunchAgents/ru.tender-monitor.plist"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$1" = "--remove" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Проверка на Mac отключена. Папку с данными можно удалить: $APP"
  exit 0
fi

[ -f "$SRC/.env" ] || { echo "Нет файла .env в $SRC — создайте его по образцу .env.example"; exit 1; }
mkdir -p "$APP/data"
cp "$SRC/.env" "$APP/.env"

if [ -d "$APP/app/.git" ]; then
  git -C "$APP/app" pull -q --ff-only
else
  git clone -q "$(git -C "$SRC" remote get-url origin)" "$APP/app"
fi
cd "$APP/app"
[ -d .venv ] || /usr/bin/python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip >/dev/null 2>&1 || true
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python scripts/make_ca_bundle.py >/dev/null

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ru.tender-monitor</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$APP/app/scripts/mac_run.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$APP/log.txt</string>
  <key>StandardErrorPath</key><string>$APP/log.txt</string>
</dict>
</plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Готово. Мониторинг работает на этом Mac (пока он включён): все площадки, бот отвечает сразу."
echo "Журнал: $APP/log.txt"
