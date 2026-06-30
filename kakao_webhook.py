import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

import httpx
from dotenv import load_dotenv

load_dotenv()
from fastapi import BackgroundTasks, FastAPI, Request

VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8000")
VLLM_MODEL = os.getenv("VLLM_MODEL", "qwen2.5-vl-32b-instruct-awq")
VLLM_TIMEOUT = int(os.getenv("VLLM_TIMEOUT", "55"))
OCR_PROMPT = os.getenv(
    "OCR_PROMPT",
    "이 이미지의 텍스트를 모두 정확하게 읽어주세요. "
    "표나 항목이 있으면 줄바꿈으로 구분해서 보기 좋게 출력해주세요.",
)

LOG_DIR = os.getenv("LOG_DIR", "./logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("kakao_webhook")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_fh = RotatingFileHandler(
    os.path.join(LOG_DIR, "kakao_webhook.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_fh)
logger.addHandler(_sh)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(f"[startup] vLLM URL={VLLM_BASE_URL} model={VLLM_MODEL}")
    yield
    logger.info("[shutdown] 종료")


app = FastAPI(lifespan=lifespan)


def extract_image_url(data: dict) -> str | None:
    user_req = data.get("userRequest", {}) or {}

    # 1순위: 카카오 정석 구조 (오픈빌더 이미지 업로드 블록)
    media = (user_req.get("params") or {}).get("media") or {}
    if media.get("type") == "image" and media.get("url"):
        return media["url"]

    # 2순위: attachments 구조 (구버전/대안 경로)
    for item in (user_req.get("attachments") or {}).get("items", []) or []:
        attachment = (item.get("attachment") or {})
        if attachment.get("type") == "image":
            return (attachment.get("data") or {}).get("url")

    # 3순위: utterance에 이미지 URL이 직접 들어오는 경우
    utterance = user_req.get("utterance", "") or ""
    if utterance.startswith("http") and ("kakaocdn.net" in utterance or "kakao.com" in utterance):
        return utterance

    return None


async def call_vllm_ocr(image_url: str) -> str:
    async with httpx.AsyncClient(timeout=VLLM_TIMEOUT) as client:
        img_resp = await client.get(image_url)
        img_resp.raise_for_status()

        content_type = img_resp.headers.get("content-type", "image/jpeg").split(";")[0]
        img_b64 = base64.b64encode(img_resp.content).decode()

        resp = await client.post(
            f"{VLLM_BASE_URL}/v1/chat/completions",
            json={
                "model": VLLM_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{content_type};base64,{img_b64}"},
                        },
                        {"type": "text", "text": OCR_PROMPT},
                    ],
                }],
                "max_tokens": 2000,
                "temperature": 0.1,
            },
            timeout=VLLM_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def kakao_text_response(text: str) -> dict:
    return {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": text}}]},
    }


async def process_ocr_and_callback(callback_url: str, image_url: str):
    logger.info(f"[ocr] 시작 image={image_url}")
    try:
        ocr_text = await call_vllm_ocr(image_url)
        logger.info(f"[ocr] 결과 image={image_url}\n{ocr_text}")
        body = kakao_text_response(f"📄 OCR 결과\n─────────────────\n{ocr_text}")
    except Exception as e:
        logger.error(f"[ocr] 실패: {e}")
        body = kakao_text_response(f"⚠️ 이미지 분석 실패\n{str(e)[:120]}")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(callback_url, json=body)
        logger.info("[ocr] 콜백 전송 완료")
    except Exception as e:
        logger.error(f"[ocr] 콜백 전송 실패: {e}")


@app.post("/kakao")
async def kakao_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()

    user_req = data.get("userRequest", {}) or {}
    utterance = user_req.get("utterance", "")
    user_id = (user_req.get("user") or {}).get("id", "")
    callback_url = user_req.get("callbackUrl")
    image_url = extract_image_url(data)

    logger.info(f"[webhook] user={user_id} utterance={utterance!r} image={bool(image_url)}")

    if image_url:
        if callback_url:
            background_tasks.add_task(process_ocr_and_callback, callback_url, image_url)
            return {"version": "2.0", "useCallback": True}
        else:
            # callbackUrl 없을 때 동기 처리 (5초 제한 주의)
            try:
                ocr_text = await asyncio.wait_for(call_vllm_ocr(image_url), timeout=4.5)
                logger.info(f"[ocr] 결과 image={image_url}\n{ocr_text}")
                return kakao_text_response(f"📄 OCR 결과\n─────────────────\n{ocr_text}")
            except asyncio.TimeoutError:
                return kakao_text_response("⏱️ 분석 시간이 초과됐어요. 잠시 후 다시 시도해주세요.")
            except Exception as e:
                logger.error(f"[ocr] 동기 처리 실패: {e}")
                return kakao_text_response(f"⚠️ 이미지 분석 실패: {str(e)[:120]}")

    # 이미지 없으면 텍스트 그대로 에코
    return kakao_text_response(f"메시지 수신: {utterance or '(빈 메시지)'}")


@app.post("/test-ocr")
async def test_ocr(request: Request):
    """타임아웃 없이 OCR 결과를 직접 반환하는 테스트용 엔드포인트."""
    body = await request.json()
    image_url = body.get("image_url")
    if not image_url:
        return {"error": "image_url 필드가 필요합니다."}
    try:
        ocr_text = await call_vllm_ocr(image_url)
        return {"result": ocr_text}
    except Exception as e:
        logger.error(f"[test-ocr] 실패: {e}")
        return {"error": str(e)}


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{VLLM_BASE_URL}/v1/models")
            models = resp.json()
        return {"status": "ok", "vllm": "ok", "models": [m["id"] for m in models.get("data", [])]}
    except Exception as e:
        return {"status": "ok", "vllm": f"error: {e}"}
