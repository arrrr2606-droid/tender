FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 TZ=Europe/Moscow
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Бандл сертификатов с НУЦ Минцифры — нужен для ЕИС и torgi.gov.ru
COPY scripts ./scripts
RUN python scripts/make_ca_bundle.py /app/certs/ru-bundle.pem

COPY monitor ./monitor
COPY config.yaml .

CMD ["python", "-m", "monitor.main"]
