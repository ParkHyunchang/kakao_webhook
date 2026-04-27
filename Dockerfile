FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kakao_webhook.py .

EXPOSE 3500

CMD ["uvicorn", "kakao_webhook:app", "--host", "0.0.0.0", "--port", "3500"]
