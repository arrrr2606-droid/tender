"""Собирает бандл доверенных сертификатов: стандартные (certifi) + НУЦ Минцифры.

ЕИС и torgi.gov.ru используют сертификаты Russian Trusted Root CA, которых нет
в стандартных хранилищах. Копии лежат в certs/ru-ca/ (скачаны с gu-st.ru и
nuc-cdp.digital.gov.ru); если папки нет — сертификаты скачиваются заново.
"""
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "certs" / "ru-ca"
URLS = [
    "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
    "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt",
    # промежуточный сертификат 2024 года — им подписан, например, torgi.gov.ru,
    # а сайт не присылает его в цепочке сам
    "http://nuc-cdp.digital.gov.ru/cdp/subca_ssl_rsa2024.crt",
]


def download() -> list:
    pems = []
    for url in URLS:
        raw = urllib.request.urlopen(url, timeout=30).read()
        pem = raw.decode() if b"BEGIN CERTIFICATE" in raw else ssl.DER_cert_to_PEM_cert(raw)
        if "BEGIN CERTIFICATE" not in pem:
            sys.exit(f"Неожиданный ответ от {url}")
        pems.append(pem)
    return pems


out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "certs" / "ru-bundle.pem")
local = sorted(LOCAL.glob("*.pem"))
pems = [p.read_text() for p in local] if local else download()
bundle = Path(certifi.where()).read_text()
for pem in pems:
    bundle += "\n" + pem.strip() + "\n"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(bundle)
print(f"Готово: {out}")
