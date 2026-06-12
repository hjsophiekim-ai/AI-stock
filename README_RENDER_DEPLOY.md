# Render 배포 가이드

## 현재 배포 주소
https://ai-stock-r8yl.onrender.com

---

## 배포 전 체크리스트

### 1. Render Dashboard에서 환경변수 설정

**Settings > Environment Variables** 에서 아래 키를 추가하세요.
값을 이 파일이나 코드에 절대 입력하지 마세요.

| 환경변수 | 설명 | 필수 |
|---|---|---|
| `KIS_MOCK_APP_KEY` | 한국투자증권 모의투자 App Key | MOCK 사용 시 |
| `KIS_MOCK_APP_SECRET` | 한국투자증권 모의투자 App Secret | MOCK 사용 시 |
| `KIS_MOCK_ACCOUNT_NO` | 모의투자 계좌번호 (예: 50123456-01) | MOCK 사용 시 |
| `KIS_REAL_APP_KEY` | 실전투자 App Key | REAL 사용 시 |
| `KIS_REAL_APP_SECRET` | 실전투자 App Secret | REAL 사용 시 |
| `KIS_ACCOUNT_NO` | 실전투자 계좌번호 | REAL 사용 시 |
| `DART_API_KEY` | DART 공시 API 키 | 선택 |

### 2. 자동으로 처리되는 항목

아래 항목들은 앱 시작 시 **자동으로 처리**됩니다.

- `data/`, `models/`, `reports/predictions/`, `logs/` 등 필수 디렉토리 자동 생성
- `.env` 파일 없이 환경변수만으로 동작 (Render는 환경변수로 주입)

---

## 배포 방법

### 첫 배포

1. GitHub 저장소 연결 (Render Dashboard > New Web Service)
2. **Branch**: `new-master` 선택
3. **Build Command**: `pip install -r requirements.txt`
4. **Start Command**: `streamlit run app/streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
5. 위 환경변수 입력 후 **Deploy**

### 재배포 (캐시 포함)

```
Render Dashboard > Manual Deploy > Clear build cache & deploy
```

---

## 배포 후 확인 방법

### 진단 페이지 접속

배포된 앱에서 왼쪽 사이드바 → **0_배포상태_환경진단** 클릭

확인 항목:
- ✅ 현재 배포 커밋 해시가 표시됨
- ✅ 환경변수 OK/MISSING 상태 확인
- ✅ 필수 패키지 import 가능 여부
- ✅ KIS MOCK/REAL 연결 상태

---

## 로컬과 Render의 주요 차이점

| 항목 | 로컬 | Render |
|---|---|---|
| `.env` 파일 | 존재 | 없음 (환경변수로 대체) |
| `data/raw/` | 3년치 데이터 존재 가능 | 없음 (휘발성 디스크) |
| `models/model.joblib` | 학습된 모델 존재 가능 | 없음 |
| `reports/predictions/` | 후보 파일 존재 가능 | 없음 (매 배포 시 초기화) |
| 파이프라인 실행 | 로컬 Python 환경 | 제한된 메모리/시간 |

### Render 디스크 유의사항

Render 무료 플랜은 **휘발성 디스크**를 사용합니다.
배포 시마다 `data/`, `models/`, `reports/` 내용이 초기화됩니다.

- 매 배포 후 **전체 파이프라인**을 다시 실행해야 합니다.
- 데이터 영속성이 필요하면 Render **Persistent Disk** 또는 외부 스토리지(S3 등)를 사용하세요.

---

## 문제 해결

### 파이프라인 실패

1. AI 후보 리스트 페이지 → 실패 단계 확인 (stderr 표시됨)
2. Render Dashboard → Logs 탭에서 전체 로그 확인

### MOCK 매수 실패

1. 예산배분 및 주문 페이지 → MOCK Preflight 섹션 확인
2. ❌ FAIL 항목 해결 후 재시도

### KIS 토큰 실패

1. KIS 연결상태 페이지 → MOCK/REAL 탭
2. **토큰 새로 발급** 버튼 클릭

---

## 배포 커밋 추적

사이드바 하단과 배포상태 진단 페이지에 **현재 배포 커밋 해시**가 표시됩니다.

Render 환경에서는 `RENDER_GIT_COMMIT` 환경변수로 자동 제공됩니다.
