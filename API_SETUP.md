# API_SETUP.md — 한국투자증권 Open API 설정 가이드

## 1. 사전 준비사항

### 1.1 한국투자증권 계좌 개설
1. 한국투자증권 앱 또는 영업점에서 계좌 개설
2. 실전투자 계좌: 일반 주식 계좌
3. 모의투자 계좌: KIS Developers에서 별도 신청

### 1.2 KIS Developers 가입 및 앱 등록
1. [KIS Developers](https://apiportal.koreainvestment.com/) 접속
2. 회원가입 및 로그인
3. "앱 등록" → 새 앱 생성
4. APP KEY, APP SECRET 발급 (각각 저장)
5. 모의투자용과 실전투자용 앱 키가 별도 존재

---

## 2. 환경변수 설정

### 2.1 .env 파일 생성

`.env.example`을 복사하여 `.env` 파일을 생성합니다.

```bash
copy .env.example .env
```

### 2.2 .env 파일 내용

```env
# 모의투자 (KIS_USE_MOCK=true일 때 사용)
KIS_APP_KEY=your_mock_app_key_here
KIS_APP_SECRET=your_mock_app_secret_here
KIS_ACCOUNT_NO=50123456       # 모의투자 계좌번호 (숫자만)
KIS_ACCOUNT_PRODUCT_CODE=01   # 계좌상품코드 (대부분 01)
KIS_USE_MOCK=true             # true: 모의투자 / false: 실전투자

# 실전투자 (KIS_USE_MOCK=false일 때 사용)
# KIS_APP_KEY=your_real_app_key_here
# KIS_APP_SECRET=your_real_app_secret_here
# KIS_ACCOUNT_NO=12345678
# KIS_ACCOUNT_PRODUCT_CODE=01
# KIS_USE_MOCK=false
```

**절대 주의:** `.env` 파일은 절대 git에 커밋하지 마세요. `.gitignore`에 포함되어 있습니다.

---

## 3. API 기본 구조

### 3.1 Base URL

| 환경 | URL |
|------|-----|
| 모의투자 | `https://openapivts.koreainvestment.com:29443` |
| 실전투자 | `https://openapi.koreainvestment.com:9443` |

### 3.2 접근토큰 발급

```
POST /oauth2/tokenP

Request Body:
{
  "grant_type": "client_credentials",
  "appkey": "YOUR_APP_KEY",
  "appsecret": "YOUR_APP_SECRET"
}

Response:
{
  "access_token": "eyJ0e...",
  "token_type": "Bearer",
  "expires_in": 86400    // 24시간 유효
}
```

토큰은 `models/kis_token_cache.json`에 캐싱하여 재발급 횟수 최소화.

### 3.3 공통 요청 헤더

```python
headers = {
    "content-type": "application/json",
    "authorization": f"Bearer {access_token}",
    "appkey": app_key,
    "appsecret": app_secret,
    "tr_id": "TR_ID_HERE",  # 각 API마다 다른 거래 ID
    "custtype": "P",         # 개인
}
```

---

## 4. 주요 API 목록

### 4.1 시세 조회 API

| API명 | tr_id (모의) | tr_id (실전) | 설명 |
|-------|-------------|-------------|------|
| 주식현재가시세 | FHKST01010100 | FHKST01010100 | 현재가 조회 |
| 주식일자별시세 | FHKST03010100 | FHKST03010100 | 일봉 OHLCV |
| 주식분봉조회 | FHKST03010200 | FHKST03010200 | 분봉 데이터 |
| 국내주식 종목마스터 | - | - | 종목 리스트 |

### 4.2 잔고 조회 API

| API명 | tr_id (모의) | tr_id (실전) | 설명 |
|-------|-------------|-------------|------|
| 주식잔고조회 | VTTC8434R | TTTC8434R | 잔고 조회 |

### 4.3 주문 API (live_trade=true 시에만 사용)

| API명 | tr_id (모의) | tr_id (실전) | 설명 |
|-------|-------------|-------------|------|
| 주식매수주문 | VTTC0802U | TTTC0802U | 매수 주문 |
| 주식매도주문 | VTTC0801U | TTTC0801U | 매도 주문 |
| 주식정정취소주문 | VTTC0803U | TTTC0803U | 주문 정정/취소 |
| 주식체결조회 | VTTC8001R | TTTC8001R | 체결 내역 |

---

## 5. API 요청 제한 및 에러 처리

### 5.1 요청 제한

| 항목 | 제한 |
|------|------|
| 초당 요청 수 | 20건/초 (과도 시 에러) |
| 일일 요청 수 | 제한 있음 (KIS 공식 문서 확인) |
| 토큰 유효기간 | 24시간 |

**안전한 호출 방법:**
```python
import time
time.sleep(0.1)  # 각 API 호출 사이 0.1초 대기
```

### 5.2 에러 코드

| 에러코드 | 설명 | 처리 방법 |
|----------|------|----------|
| EGW00123 | 토큰 만료 | 토큰 재발급 후 재시도 |
| EGW00201 | 권한 없음 | 앱 키 확인 |
| 40100000 | 인증 실패 | APP KEY/SECRET 확인 |
| 40910000 | 요청 제한 초과 | 잠시 대기 후 재시도 |

### 5.3 재시도 로직

```python
MAX_RETRY = 3
RETRY_DELAY = 1.0  # 초

for attempt in range(MAX_RETRY):
    try:
        response = call_api(...)
        if response.status_code == 200:
            break
    except Exception as e:
        if attempt < MAX_RETRY - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))
        else:
            logging.error(f"API 호출 실패: {e}")
            raise
```

---

## 6. 종목 리스트 수집 방법

한국투자증권 API 외에도 아래 방법을 병행합니다:

```python
# pykrx 또는 FinanceDataReader 사용 (종목 마스터 데이터)
from pykrx import stock
kospi_tickers = stock.get_market_ticker_list(market="KOSPI")
kosdaq_tickers = stock.get_market_ticker_list(market="KOSDAQ")
```

---

## 7. 모의투자 vs 실전투자 전환

```yaml
# config.yaml
kis:
  use_mock: true   # 모의투자
  # use_mock: false  # 실전투자 (충분한 검증 후에만 변경)
```

**전환 체크리스트:**
- [ ] 6개월 이상 백테스트 완료 (수수료·세금 포함)
- [ ] 1개월 이상 모의투자 완료
- [ ] 리스크 정책 이해 및 손실 한도 설정
- [ ] KIS 실전투자 앱 키 별도 발급
- [ ] `.env` 파일에 실전 키로 교체
- [ ] `config.yaml`에서 `live_trade: true` 변경

---

## 8. 주문 API 안전 설계 (3단계 안전 잠금)

실전 주문이 실행되려면 아래 3가지 조건이 **모두** 충족되어야 합니다:

```
config.yaml 조건:
  1. live_trade: true
  2. kis.use_mock: false
  3. safety.confirm_live_trade: true
```

```
주문 요청 흐름:
run_live_trader.py
  → REAL 모드 시 5초 경고 + 콘솔 확인 출력
  → AutoTrader.execute_buy_window()
      → SafetyGate.assert_can_place_real_order()   ← 1차 차단
      → RiskManager.approve_buy_order()             ← 2차 검증
      → OrderManager._execute_buy()
          → SafetyGate 재확인                       ← 3차 차단
          → KISApiClient.place_cash_buy_order()     ← 실제 주문

PAPER 모드: KIS API 호출 코드 경로 자체에 진입하지 않음
MOCK 모드: 모의투자 URL + VTTC TR_ID 사용 (실전 계좌 영향 없음)
REAL 모드: 3단계 모두 통과 시에만 실전 URL + TTTC TR_ID 사용
```

### 8.1 토큰 캐싱 상세

- 토큰 저장 위치: `data/token_cache.json`
- 유효 기간: 24시간
- 만료 60분 전 자동 재발급 (`TOKEN_REFRESH_MARGIN_SEC = 3600`)
- 앱 재시작 시 파일 캐시 우선 사용 → 불필요한 토큰 발급 방지

### 8.2 미구현 API (NotImplementedError)

아래 기능은 공식 문서 확인 전까지 구현하지 않았습니다:

- 시간외 단일가 주문 (장 마감 후 15:40~18:00)
- 장전 시간외 주문 (08:30~08:40)
- 코스피 지수 조회 (TR_ID 미확인)

이 기능이 필요할 경우 [KIS Developers 공식 문서](https://apiportal.koreainvestment.com/apiservice)에서 TR_ID를 확인한 후 `kis_api.py`에 추가하세요.

### 8.3 API 키 보안 원칙

- API 키, 앱시크릿, 계좌번호는 **코드에 직접 작성 금지**
- `.env` 파일에서만 읽음 (`python-dotenv` 사용)
- `.env`는 `.gitignore`에 포함 — git에 절대 올라가지 않음
- 로그에 API 키·시크릿·계좌번호 출력 금지 (`mask_account_no()` 사용)
