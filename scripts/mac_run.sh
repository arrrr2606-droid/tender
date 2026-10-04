#!/bin/zsh
# Мониторинг на этом Mac: все площадки + мгновенные ответы бота.
# Запускается launchd при входе в систему и перезапускается, если процесс завершится.
APP="$HOME/Library/Application Support/tender-monitor"
cd "$APP/app" || exit 1
echo "=== запуск $(date '+%d.%m.%Y %H:%M') ==="

# свежий код с GitHub (если интернет есть)
OLD_REQ=$(md5 -q requirements.txt)
git pull -q --ff-only || echo "git pull не удался — работаю на текущей версии"
[ "$OLD_REQ" = "$(md5 -q requirements.txt)" ] || .venv/bin/pip install -q -r requirements.txt
[ -f certs/ru-bundle.pem ] || .venv/bin/python scripts/make_ca_bundle.py

set -a; source "$APP/.env"; set +a
export MONITOR_DATA="$APP/data"
exec .venv/bin/python -m monitor.main
