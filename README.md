# kakao_webhook

카카오 i 오픈빌더 챗봇의 스킬 서버. 카카오톡 채널로 들어온 메시지를 받아 MySQL에 저장하고, 가독성 있는 응답을 돌려준다.

## 구성

```
카카오 서버
   ↓ HTTPS (8443)
TP-Link 공유기 (포트포워딩)
   ↓
Synology NAS (DSM 리버스 프록시, SSL 종단)
   ↓ HTTP (3500)
FastAPI 컨테이너 (kakao_webhook)
   ↓
MySQL 컨테이너 (vue_personal_project-backend-db, kakao_db 스키마)
```

- **외부 진입점**: `https://hyunchang.synology.me:8443/kakao`
- **내부 컨테이너 포트**: `3500`
- **DB**: `kakao_db.kakao_messages`

## 디렉토리

| 파일 | 설명 |
|---|---|
| `kakao_webhook.py` | FastAPI 본체. 웹훅 수신 → DB 저장 → 응답 |
| `Dockerfile` | python:3.11-slim 기반 이미지 |
| `docker-compose.yml` | NAS 배포용. `ghcr.io/parkhyunchang/kakao_webhook:latest` 사용 |
| `requirements.txt` | fastapi, uvicorn, pymysql |
| `init.sql` | `kakao_db` 스키마 + `kakao_messages` 테이블 생성 (root 권한 필요) |
| `.env.example` | NAS에 둘 `.env` 템플릿 |
| `.github/workflows/deploy.yml` | 빌드 → ghcr.io 푸시 → NAS 자동 배포 |

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/kakao` | 카카오 스킬 웹훅. 메시지 저장 + 닉네임 등록 흐름 후 simpleText 응답 |
| GET | `/users` | 등록된 사용자 목록 (관리자 확인용) |
| GET | `/health` | 헬스체크. DB 연결 상태 포함 |

## DB 스키마

```sql
CREATE TABLE kakao_messages (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id     VARCHAR(128),
  utterance   TEXT,
  intent_name VARCHAR(255),
  block_name  VARCHAR(255),
  raw_payload JSON,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id),
  INDEX idx_created (created_at)
);
```

전체 페이로드는 `raw_payload` (JSON) 에 그대로 보관. 자주 쓰는 필드만 별도 컬럼으로 정규화.

```sql
CREATE TABLE kakao_users (
  user_id       VARCHAR(128) PRIMARY KEY,
  display_name  VARCHAR(64),
  state         VARCHAR(32) NOT NULL DEFAULT 'awaiting_name',
  message_count INT NOT NULL DEFAULT 0,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

카카오 webhook이 주는 `userRequest.user.id` 해시값과 사용자가 자가신고한 닉네임을 매핑. `state` 는 `awaiting_name` → `registered` 로 진행.

## 닉네임 등록 흐름

카카오 webhook에는 사용자 식별 정보가 해시 ID 하나뿐이라, 첫 진입자에게 직접 닉네임을 받아 매핑한다.

| 단계 | 사용자 발화 | 봇 응답 |
|---|---|---|
| 1. 신규 진입 | (아무 메시지) | "어떻게 불러드릴까요? 다음 메시지로 닉네임을 보내주세요." |
| 2. 닉네임 등록 | `홍길동` | "반갑습니다, 홍길동님! 🎉" |
| 3. 이후 대화 | (아무 메시지) | `✅ 홍길동님 메시지 수신 완료` 형태로 응답 |

> 카카오싱크 기반 정식 본인 식별이 필요하면 비즈니스 채널 심사 + 사용자 정보 동의 항목 설정이 추가로 필요하다. 현재 구조는 데모/포트폴리오 용도의 자가신고 매핑이다.

## 환경변수

`.env` 파일 (NAS의 `/volume1/docker/kakao-webhook/.env`):

```
DB_PASSWORD=<MySQL hyunchang88 비밀번호>
```

`docker-compose.yml`에 정의된 나머지 환경변수:

| 변수 | 기본값 |
|---|---|
| `DB_HOST` | `host.docker.internal` |
| `DB_PORT` | `3306` |
| `DB_USER` | `hyunchang88` |
| `DB_NAME` | `kakao_db` |
| `TZ` | `Asia/Seoul` |

## 배포 (CI/CD)

`main` 브랜치에 푸시하면 GitHub Actions가 자동으로 처리한다.

1. `build` job — Dockerfile 빌드 → `ghcr.io/parkhyunchang/kakao_webhook:latest` 푸시
2. `deploy` job — NAS에 SSH 접속 → `docker-compose.yml` 복사 → `pull` & `up -d`

수동 트리거: Actions 탭 → "Build and Deploy" → Run workflow.

### 필요한 GitHub Secrets

| 이름 | 용도 |
|---|---|
| `GHCR_PAT` | ghcr.io 푸시/풀용 PAT (`write:packages`) |
| `NAS_HOST` | NAS 공인 IP 또는 도메인 |
| `NAS_USER` | NAS SSH 사용자명 |
| `NAS_SSH_PASSWORD` | NAS SSH 비밀번호 (sudo 비밀번호와 동일해야 함) |

## 최초 셋업 (한 번만)

### 1. NAS — kakao_db 스키마 생성
MySQL root 권한으로 [init.sql](init.sql) 실행.

```bash
sudo docker exec -it vue_personal_project-backend-db mysql -uroot -p < init.sql
```

### 2. NAS — 디렉토리 + .env 생성
```bash
mkdir -p /volume1/docker/kakao-webhook
cd /volume1/docker/kakao-webhook
echo "DB_PASSWORD=실제비밀번호" > .env
chmod 600 .env
```

### 3. NAS — 첫 컨테이너 기동
첫 배포는 GitHub Actions 트리거로 자동 처리. 또는 수동으로:
```bash
cd /volume1/docker/kakao-webhook
sudo docker login ghcr.io -u parkhyunchang
sudo docker-compose pull
sudo docker-compose up -d
```

### 4. 공유기 — 포트포워딩
TP-Link → 가상 서버 → 외부 `8443` → NAS 내부 IP `8443` (TCP)

### 5. DSM — 리버스 프록시
제어판 → 로그인 포털 → 고급 → 역방향 프록시 → 생성

| 항목 | 값 |
|---|---|
| 소스 | `HTTPS` / `hyunchang.synology.me` / `8443` |
| 대상 | `HTTP` / `localhost` / `3500` |

인증서: `hyunchang.synology.me` (Let's Encrypt) 적용.

### 6. 카카오 i 오픈빌더 — 스킬 등록
스킬 URL: `https://hyunchang.synology.me:8443/kakao`

## 동작 확인

### 내부 (NAS 안에서)
```bash
curl -X POST http://localhost:3500/kakao \
  -H "Content-Type: application/json" \
  -d '{"userRequest":{"user":{"id":"test_user_1"},"utterance":"안녕"},"intent":{"name":"인사블록"}}'
```

### 외부
```bash
curl -X POST https://hyunchang.synology.me:8443/kakao \
  -H "Content-Type: application/json" \
  -d '{"userRequest":{"user":{"id":"test_user_1"},"utterance":"안녕"},"intent":{"name":"인사블록"}}'
```

### 헬스체크
```bash
curl http://localhost:3500/health
# {"status":"ok","db":"ok"}
```

### 저장 데이터 확인
```sql
SELECT id, user_id, utterance, intent_name, created_at
FROM kakao_db.kakao_messages
ORDER BY id DESC
LIMIT 10;

SELECT user_id, display_name, state, message_count, last_seen_at
FROM kakao_db.kakao_users
ORDER BY last_seen_at DESC;
```

### 등록 사용자 조회 (HTTP)
```bash
curl https://hyunchang.synology.me:8443/users
```

## 응답 포맷

등록 완료된 사용자 응답:
```
✅ 홍길동님 메시지 수신 완료
─────────────────
📝 입력: 안녕하세요
💬 누적: 12회
🕐 시간: 2026-04-27 11:53:04
💾 저장 ID: 42
```

신규 진입자 응답:
```
안녕하세요! 처음 오신 분이네요. 👋
어떻게 불러드릴까요?
다음 메시지로 닉네임을 보내주세요.
```

## 참고

- 카카오 스킬 응답은 **5초 이내** 필수. DB INSERT 실패해도 응답은 정상 반환 (saved_id만 누락).
- `extra_hosts: host.docker.internal:host-gateway` 로 NAS 호스트 IP 우회 → 같은 호스트의 MySQL 컨테이너(3306)에 접근.
- 컨테이너 로그 실시간: `sudo docker logs -f kakao_webhook`
