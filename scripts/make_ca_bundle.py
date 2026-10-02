"""Собирает бандл доверенных сертификатов: стандартные (certifi) + НУЦ Минцифры.

ЕИС и torgi.gov.ru используют сертификаты Russian Trusted Root CA, которых нет
в стандартных хранилищах. Сертификаты берутся с официального сайта Госуслуг (gu-st.ru).
"""
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi

URLS = [
    "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
    "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt",
    # промежуточный сертификат 2024 года — им подписан, например, torgi.gov.ru,
    # а сайт не присылает его в цепочке сам
    "http://nuc-cdp.digital.gov.ru/cdp/subca_ssl_rsa2024.crt",
]

out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "certs" / "ru-bundle.pem")
bundle = Path(certifi.where()).read_text()
for url in URLS:
    raw = urllib.request.urlopen(url, timeout=30).read()
    pem = raw.decode() if b"BEGIN CERTIFICATE" in raw else ssl.DER_cert_to_PEM_cert(raw)
    if "BEGIN CERTIFICATE" not in pem:
        sys.exit(f"Неожиданный ответ от {url}")
    bundle += "\n" + pem.strip() + "\n"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(bundle)
print(f"Готово: {out}")
