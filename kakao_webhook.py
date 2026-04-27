import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler

import pymysql
from pymysql.cursors import DictCursor
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


def save_message(conn, data: dict) -> int:
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
    with conn.cursor() as cur:
        cur.execute(sql, (
            user_id,
            utterance,
            intent_name,
            block_name,
            json.dumps(data, ensure_ascii=False),
        ))
        return cur.lastrowid


def get_user(conn, user_id: str) -> dict | None:
    with conn.cursor(DictCursor) as cur:
        cur.execute(
            "SELECT user_id, display_name, state, message_count "
            "FROM kakao_users WHERE user_id = %s",
            (user_id,),
        )
        return cur.fetchone()


def create_user(conn, user_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kakao_users (user_id) VALUES (%s)",
            (user_id,),
        )


def set_user_name(conn, user_id: str, name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE kakao_users SET display_name = %s, state = 'registered', "
            "message_count = message_count + 1 WHERE user_id = %s",
            (name, user_id),
        )


def bump_user_activity(conn, user_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE kakao_users SET message_count = message_count + 1 "
            "WHERE user_id = %s",
            (user_id,),
        )


def build_registered_response(display_name: str, message_count: int,
                              utterance: str, saved_id: int | None) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"✅ {display_name}님 메시지 수신 완료",
        "─────────────────",
        f"📝 입력: {utterance or '(빈 메시지)'}",
        f"💬 누적: {message_count}회",
        f"🕐 시간: {now}",
    ]
    if saved_id is not None:
        lines.append(f"💾 저장 ID: {saved_id}")
    return "\n".join(lines)


def handle_user_flow(conn, user_id: str, utterance: str,
                     saved_id: int | None) -> str:
    user = get_user(conn, user_id)

    if user is None:
        create_user(conn, user_id)
        return (
            "안녕하세요! 처음 오신 분이네요. 👋\n"
            "어떻게 불러드릴까요?\n"
            "다음 메시지로 닉네임을 보내주세요."
        )

    if user["state"] == "awaiting_name":
        name = (utterance or "").strip()[:64]
        if not name:
            return "닉네임이 비어 있어요. 다시 한 번 보내주세요!"
        set_user_name(conn, user_id, name)
        return (
            f"반갑습니다, {name}님! 🎉\n"
            f"앞으로 이렇게 불러드릴게요."
        )

    bump_user_activity(conn, user_id)
    return build_registered_response(
        display_name=user["display_name"] or "(이름 없음)",
        message_count=(user["message_count"] or 0) + 1,
        utterance=utterance,
        saved_id=saved_id,
    )


@app.post("/kakao")
async def kakao_webhook(request: Request):
    data = await request.json()

    user_req = data.get("userRequest", {}) or {}
    utterance = user_req.get("utterance", "")
    user_id = (user_req.get("user") or {}).get("id", "")
    logger.info(f"[webhook] received user={user_id} utterance={utterance!r}")
    logger.debug(f"[webhook] payload: {json.dumps(data, ensure_ascii=False)}")

    saved_id = None
    response_text = "메시지를 받았지만 처리 중 오류가 발생했어요."

    try:
        conn = get_conn()
        try:
            try:
                saved_id = save_message(conn, data)
                logger.info(f"[db] 메시지 저장 id={saved_id}")
            except Exception as e:
                logger.error(f"[db] 메시지 저장 실패: {e}")

            response_text = handle_user_flow(conn, user_id, utterance, saved_id)
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"[db] 사용자 처리 실패: {e}")

    return {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": response_text}}]},
    }


@app.get("/users")
async def list_users():
    conn = get_conn()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute(
                "SELECT user_id, display_name, state, message_count, "
                "first_seen_at, last_seen_at "
                "FROM kakao_users ORDER BY last_seen_at DESC"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {"count": len(rows), "users": rows}


@app.get("/health")
async def health():
    try:
        conn = get_conn()
        conn.ping(reconnect=True)
        conn.close()
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "ok", "db": f"error: {e}"}
