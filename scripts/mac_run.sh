#!/bin/zsh
# Одна проверка ЕИС и torgi.gov.ru с этого Mac. Запускается launchd каждые 30 минут.
# Слова, регионы и пауза берутся из общего состояния на GitHub (ветка state),
# команды бота обрабатывает GitHub — здесь они не читаются.
APP="$HOME/Library/Application Support/tender-monitor"
cd "$APP/app" || exit 1
echo "=== $(date '+%d.%m.%Y %H:%M') ==="

# свежий код с GitHub (если интернет есть)
OLD_REQ=$(md5 -q requirements.txt)
git pull -q --ff-only || echo "git pull не удался — работаю на текущей версии"
[ "$OLD_REQ" = "$(md5 -q requirements.txt)" ] || .venv/bin/pip install -q -r requirements.txt
[ -f certs/ru-bundle.pem ] || .venv/bin/python scripts/make_ca_bundle.py

set -a; source "$APP/.env"; set +a
export MONITOR_DATA="$APP/data"
REPO=$(git remote get-url origin | sed -E 's#https://github.com/##; s#\.git$##')
exec .venv/bin/python -m monitor.main --once --only eis,torgi --no-commands \
    --remote-state "https://raw.githubusercontent.com/$REPO/state/state.json"
