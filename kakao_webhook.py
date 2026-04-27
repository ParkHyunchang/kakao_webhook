import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler

import pymysql
from fastapi import FastAPI, Request

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "host.docker.internal"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "hyunchang88"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "kakao_db"),
    "charset": "utf8mb4",
    "autocommit": True,
}

LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("kakao_webhook")
logger.setLevel(logging.INFO)
_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "kakao_webhook.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)
logger.addHandler(_stream_handler)


def get_conn():
    return pymysql.connect(**DB_CONFIG)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        conn = get_conn()
        conn.ping(reconnect=True)
        conn.close()
        logger.info(
            f"[startup] DB 연결 OK -> {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
        )
    except Exception as e:
        logger.error(f"[startup] DB 연결 실패: {e}")
    yield
    logger.info("[shutdown] 종료")


app = FastAPI(lifespan=lifespan)


def save_message(data: dict) -> int:
    user_req = data.get("userRequest", {}) or {}
    intent = data.get("intent", {}) or {}
    user = user_req.get("user", {}) or {}

    user_id = user.get("id")
    utterance = user_req.get("utterance")
    intent_name = intent.get("name")
    block_name = (data.get("action") or {}).get("name") or intent.get("name")

    sql = """
        INSERT INTO kakao_messages
            (user_id, utterance, intent_name, block_name, raw_payload)
        VALUES (%s, %s, %s, %s, %s)
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                user_id,
                utterance,
                intent_name,
                block_name,
                json.dumps(data, ensure_ascii=False),
            ))
            return cur.lastrowid
    finally:
        conn.close()


def build_response_text(data: dict, saved_id: int | None) -> str:
    user_req = data.get("userRequest", {}) or {}
    user = user_req.get("user", {}) or {}
    intent = data.get("intent", {}) or {}

    utterance = user_req.get("utterance") or "(빈 메시지)"
    user_id = user.get("id") or "unknown"
    intent_name = intent.get("name") or "-"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "✅ 메시지 수신 완료",
        "─────────────────",
        f"📝 입력: {utterance}",
        f"👤 사용자: {user_id[:12]}…" if len(user_id) > 12 else f"👤 사용자: {user_id}",
        f"🎯 인텐트: {intent_name}",
        f"🕐 시간: {now}",
    ]
    if saved_id is not None:
        lines.append(f"💾 저장 ID: {saved_id}")
    return "\n".join(lines)


@app.post("/kakao")
async def kakao_webhook(request: Request):
    data = await request.json()

    user_req = data.get("userRequest", {}) or {}
    utterance = user_req.get("utterance", "")
    user_id = (user_req.get("user") or {}).get("id", "")
    logger.info(f"[webhook] received user={user_id} utterance={utterance!r}")
    logger.debug(f"[webhook] payload: {json.dumps(data, ensure_ascii=False)}")

    saved_id = None
    try:
        saved_id = save_message(data)
        logger.info(f"[db] 저장 성공 id={saved_id}")
    except Exception as e:
        logger.error(f"[db] 저장 실패: {e}")

    text = build_response_text(data, saved_id)
    return {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": text}}]},
    }


@app.get("/health")
async def health():
    try:
        conn = get_conn()
        conn.ping(reconnect=True)
        conn.close()
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "ok", "db": f"error: {e}"}
