#!/bin/zsh
# Собирает архив для Yandex Cloud Functions: dist/tender-function.zip
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
B="$(mktemp -d)"
cd "$ROOT"
[ -f certs/ru-bundle.pem ] || python3 scripts/make_ca_bundle.py
mkdir -p "$B/certs" "$B/cloud"
cp -R monitor "$B/"
cp config.yaml "$B/"
cp certs/ru-bundle.pem "$B/certs/"
cp cloud/index.py "$B/index.py"
[ -f cloud/seed_state.json ] && cp cloud/seed_state.json "$B/cloud/"
printf 'httpx==0.28.1\nPyYAML>=6.0\ncertifi\n' > "$B/requirements.txt"
find "$B" -name __pycache__ -type d -prune -exec rm -rf {} +
mkdir -p dist && rm -f dist/tender-function.zip
(cd "$B" && zip -qr "$ROOT/dist/tender-function.zip" .)
rm -rf "$B"
echo "Готово: $ROOT/dist/tender-function.zip"
