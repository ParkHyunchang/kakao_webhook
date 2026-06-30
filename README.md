# kakao_webhook

카카오 i 오픈빌더 챗봇의 스킬 서버. 카카오톡 채널로 전송된 이미지를 받아 vLLM(Qwen2.5-VL)으로 OCR 처리 후 결과를 반환한다.

## 구성

```
카카오 서버
   ↓ HTTPS
Kubernetes Ingress / NodePort (30350)
   ↓ HTTP (3500)
FastAPI 컨테이너 (kakao-webhook)
   ↓ HTTP
vLLM 서버 (qwen2.5-vl-32b-instruct-awq, 클러스터 내부)
```

- **외부 진입점**: NodePort `30350`
- **내부 컨테이너 포트**: `3500`
- **vLLM 내부 DNS**: `http://qwen2-5-vl-32b-awq.vllm.svc.cluster.local:8000`

## 디렉토리

| 파일 | 설명 |
|---|---|
| `kakao_webhook.py` | FastAPI 본체. 이미지 OCR → vLLM 호출 → 카카오 응답 |
| `Dockerfile` | `python:3.11-slim` 기반 이미지 |
| `requirements.txt` | fastapi, uvicorn, httpx, python-dotenv |
| `.gitlab-ci.yml` | GitLab CI/CD — 빌드 → 레지스트리 푸시 → K8s 배포 |
| `k8s/deployment.yaml` | Kubernetes Namespace / Deployment / Service 정의 |

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/kakao` | 카카오 스킬 웹훅. 이미지 감지 시 OCR, 텍스트만 있으면 에코 응답 |
| POST | `/test-ocr` | `image_url` 을 직접 넘겨 OCR 결과를 동기로 확인 (타임아웃 없음) |
| GET | `/health` | 헬스체크. vLLM 연결 상태 및 로드된 모델 목록 반환 |

## OCR 처리 흐름

```
카카오 웹훅 수신
   ↓
이미지 URL 추출 (params.media → attachments → utterance URL 순서)
   ↓ 이미지 있음
callbackUrl 존재?
  YES → useCallback: true 즉시 반환 + 백그라운드 OCR 후 콜백 전송
  NO  → 4.5초 내 동기 OCR 후 반환 (카카오 5초 제한 대응)
   ↓ 이미지 없음
utterance 텍스트 에코 반환
```

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `VLLM_BASE_URL` | `http://localhost:8000` | vLLM API 서버 주소 |
| `VLLM_MODEL` | `qwen2.5-vl-32b-instruct-awq` | 사용할 모델 ID |
| `VLLM_TIMEOUT` | `55` | vLLM 요청 타임아웃 (초) |
| `OCR_PROMPT` | *(내장 기본값)* | OCR 지시 프롬프트 |
| `LOG_DIR` | `./logs` | 로그 파일 저장 경로 |
| `TZ` | `Asia/Seoul` | 타임존 |

로컬 실행 시 프로젝트 루트의 `.env` 파일로 오버라이드 가능 (`.env.example` 참고).

## 배포 (CI/CD)

`main` 브랜치에 푸시하면 GitLab CI/CD가 자동으로 처리한다.

```
push to main
   ↓
[build] Dockerfile 빌드 → registry.gitlab.ponylink.co.kr/groupcompany/kakao_webhook 푸시
   ↓
[deploy] kubectl set image → rollout status 확인
```

수동 트리거: GitLab 웹 UI → CI/CD → Pipelines → Run pipeline.

### GitLab CI/CD Variables 등록 목록

**Settings → CI/CD → Variables** 에서 등록

| 변수명 | Masked | 설명 |
|---|---|---|
| `KUBECONFIG_BASE64` | **ON** | K8s 클러스터 kubeconfig를 `base64 -w 0 ~/.kube/config` 로 인코딩한 값 |

> `CI_REGISTRY`, `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD`, `CI_REGISTRY_IMAGE`, `CI_COMMIT_SHA` 는 GitLab이 자동 제공하므로 별도 등록 불필요.

## 최초 셋업 (한 번만)

### 1. K8s — GitLab 레지스트리 Pull Secret 생성

```bash
kubectl create secret docker-registry gitlab-registry-secret \
  --docker-server=registry.gitlab.ponylink.co.kr \
  --docker-username=<GitLab 계정> \
  --docker-password=<Personal Access Token> \
  -n kakao-webhook
```

PAT는 GitLab → User Settings → Access Tokens → `read_registry` 권한으로 발급.

### 2. K8s — 매니페스트 최초 적용

```bash
kubectl apply -f k8s/deployment.yaml
```

이후 배포는 CI/CD가 `kubectl set image` 로 자동 처리한다.

## 동작 확인

### 헬스체크

```bash
curl http://<서버IP>:30350/health
# {"status":"ok","vllm":"ok","models":["qwen2.5-vl-32b-instruct-awq"]}
```

### OCR 테스트 (이미지 URL 직접 지정)

```bash
curl -X POST http://<서버IP>:30350/test-ocr \
  -H "Content-Type: application/json" \
  -d '{"image_url": "https://example.com/sample.jpg"}'
```

### 카카오 웹훅 시뮬레이션

```bash
curl -X POST http://<서버IP>:30350/kakao \
  -H "Content-Type: application/json" \
  -d '{
    "userRequest": {
      "user": {"id": "test_user_1"},
      "utterance": "안녕",
      "callbackUrl": null
    }
  }'
```

### 로그 확인

```bash
kubectl logs -f deployment/kakao-webhook -n kakao-webhook
```

## 응답 포맷

OCR 성공:
```
📄 OCR 결과
─────────────────
(vLLM이 읽어낸 이미지 텍스트)
```

OCR 실패:
```
⚠️ 이미지 분석 실패
(오류 메시지 앞 120자)
```

이미지 없는 텍스트 메시지:
```
메시지 수신: (utterance 내용)
```

## 참고

- 카카오 스킬 응답은 **5초 이내** 필수. `callbackUrl` 이 있으면 즉시 `useCallback: true` 반환 후 백그라운드 처리.
- `callbackUrl` 이 없을 때는 `asyncio.wait_for(..., timeout=4.5)` 로 타임아웃 보호.
- vLLM 서버는 K8s 클러스터 내부 DNS(`vllm` 네임스페이스)로 접근하며 외부에 노출되지 않는다.
- 로그는 `RotatingFileHandler` 로 10MB 단위 최대 5개 보관. K8s 환경에서는 stdout으로도 동시 출력.
