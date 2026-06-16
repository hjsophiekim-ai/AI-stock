# REQUIREMENTS.md — 요구사항 정의서

## 기능 요구사항

### 데이터 수집
- [ ] 한국투자증권 Open API를 이용해 국내주식 데이터를 수집할 수 있어야 한다.
- [ ] 최근 2년치 코스피, 코스닥 전 종목 일봉(OHLCV) 데이터를 수집해야 한다.
- [ ] 당일 1분봉, 3분봉, 5분봉 데이터를 수집하여 오후 2시 40분 기준 단기 흐름을 분석해야 한다.
- [ ] 종목별 데이터 수집 실패 시 프로그램 전체가 중단되지 않고 로그에 기록해야 한다.

### 데이터 필터링
- [x] 관리종목 제외
- [x] 거래정지 종목 제외
- [ ] 투자경고·주의환기 종목 제외
- [x] 우선주 제외 (종목코드 끝자리 기준)
- [x] 스팩(SPAC) 제외
- [x] ETF/ETN 제외
- [x] 최소 거래대금 미달 종목 제외 (config에서 설정)
- [ ] 현재가 1,000원 미만 종목 제외
- [ ] 기관·외국인 순매수 종목 우대 (미구현)

### 피처 생성
- [ ] 일봉 기반 기술적 피처 30개 이상 생성
- [ ] 분봉 기반 당일 흐름 피처 생성 (VWAP, 장중 수익률, 거래량 폭증 등)
- [ ] 미래 데이터 누수(data leakage)를 철저히 방지해야 한다.
- [ ] 종목별 시계열 순서를 반드시 유지해야 한다.

### 라벨 생성
- [ ] `target_2pct_next_morning`: 다음 거래일 오전 9시 30분 전까지 매수가 대비 +2% 도달 여부 (1/0)
- [ ] `target_close_profit`: 다음 거래일 오전 9시 30분 기준 청산 수익률
- [ ] `target_max_drawdown`: 매수 후 다음날 오전 9시 30분 전까지 최대 하락률
- [ ] shift 처리를 정확히 하여 같은 날짜 이후 데이터가 피처에 섞이지 않아야 한다.

### 모델 학습
- [ ] 기본 모델: LightGBMClassifier
- [ ] 대체 모델: RandomForestClassifier (LightGBM 미설치 시)
- [ ] 학습/검증 데이터를 시간 순서로 분리 (랜덤 셔플 금지)
- [ ] Walk-forward validation 구조 지원
- [ ] 핵심 성과지표: `precision_at_top20` (상위 20개 종목 중 실제 +2% 도달 비율)
- [ ] 평가 지표: Precision, Recall, F1, ROC-AUC, Top20 Hit Rate, Top20 Average Return

### 예측 및 종목 선정
- [ ] 전 종목에 대해 상승확률 계산
- [ ] 유동성·가격·위험종목 필터 적용
- [ ] 최종 20개 종목 선정 및 CSV 출력

### 백테스트
- [ ] 과거 기간에 대해 매일 전략을 시뮬레이션
- [ ] 수수료, 세금, 슬리피지 반영
- [ ] 익절/손절/강제청산 규칙 적용
- [ ] 성과지표 계산 및 리포트 생성

### 모의투자 (Paper Trade)
- [ ] 실제 주문 없이 가상 매수/매도 기록
- [ ] live_trade=false일 때 실제 API 주문 호출 금지
- [ ] 누적 수익/손실 트래킹

### 스케줄러
- [ ] 14:30 데이터 업데이트
- [ ] 14:40 후보 선정
- [ ] 14:40~15:00 가상/실제 매수
- [ ] 시간외/장전/장초반 매도 감시
- [ ] 09:30 강제청산

---

## 비기능 요구사항

### 보안
- [ ] 모든 민감정보(API 키, 앱시크릿, 계좌번호)는 반드시 `.env` 파일에서만 불러온다.
- [ ] 코드에 API 키를 직접 하드코딩하지 않는다.
- [ ] `.env` 파일은 `.gitignore`에 포함되어야 한다.

### 안전성 (주문 관련)
- [ ] `live_trade=false`가 기본값이어야 한다.
- [ ] `live_trade=false`이면 어떤 경우에도 실제 주문 API를 호출하지 않는다.
- [ ] 주문 전 반드시 `risk_manager`의 승인을 받아야 한다.
- [ ] 주문 체결 확인 실패 시 추가 주문 금지

### 안정성 (시스템)
- [ ] 오류 발생 시 프로그램 전체가 중단되지 않아야 한다.
- [ ] 어느 파일, 어느 종목, 어느 단계에서 오류가 났는지 로그에 기록해야 한다.
- [ ] API 오류 시 재시도 로직 포함

### 코드 품질
- [ ] 모든 주요 함수에 타입힌트와 docstring 포함
- [ ] 데이터 누수(future data leakage) 방지 로직 테스트 코드 포함
- [ ] pytest로 핵심 로직 테스트 가능

### 재현성
- [ ] 동일한 데이터와 설정으로 항상 동일한 결과가 나와야 한다.
- [ ] 모든 설정은 `config.yaml`에서 관리

---

## 우선순위

| 순위 | 기능 | 설명 |
|------|------|------|
| P0 | 데이터 수집 | 기반 데이터 없으면 아무것도 안 됨 |
| P0 | 피처/라벨 생성 | 학습의 핵심 |
| P0 | 모델 학습/예측 | 전략의 핵심 |
| P0 | 백테스트 | 실전 전환 전 필수 검증 |
| P1 | 모의투자 | 실전 API 연동 검증 |
| P1 | 리스크 매니저 | 안전한 주문 관리 |
| P2 | 스케줄러 | 자동 실행 |
| P2 | 실전 주문 | live_trade=true 이후 |

---

## 현재가 갱신 및 매도 기능 요구사항 (2026-06-11 추가)

### 배경 및 현재 문제

- AI 후보 파이프라인 실행 시 후보 리스트가 전날 종가(close) 기준으로 생성된다.
- 파이프라인 실행 직전 KIS 현재가를 조회하여 후보 리스트와 주문 가격에 반영해야 한다.
- 현재가 갱신 버튼이 앱에서 정상 작동하지 않았고, `refresh_candidate_prices.py`와 앱 경로 연결이 불완전하다.
- 보유종목 화면의 전량 일괄매도 기능이 정확히 작동하지 않았고, 매도 모드(MOCK/REAL/PAPER)가 명확히 분리되지 않았다.

---

### A. 현재가 갱신 전용 모듈 (`src/refresh_candidate_prices.py`)

#### CLI 인터페이스

```
python src\refresh_candidate_prices.py --mode mock --date 20260610 --top 100
python src\refresh_candidate_prices.py --mode real --date 20260610 --top 100
python src\refresh_candidate_prices.py --mode paper --date 20260610 --top 100
python src\refresh_candidate_prices.py --mode mock --input reports\predictions\top100_20260610.csv --limit 30
```

#### 지원 인자

| 인자 | 기본값 | 설명 |
|------|--------|------|
| `--mode` | mock | paper / mock / real |
| `--date` | 오늘 | YYYYMMDD |
| `--top` | 100 | 처리 종목 수 |
| `--input` | 자동 탐색 | 후보 CSV 경로 (`reports/predictions/top100_YYYYMMDD.csv`) |
| `--output` | 원본 업데이트 | 저장 경로 |
| `--limit` | — | 갱신 개수 |
| `--sleep` | 0.2 | 요청 간격(초) |
| `--strict` | False | 일부 실패 시 전체 중단 여부 |

#### mode별 동작

- **mode=mock**
  - `SafetyGate(runtime_mode="mock")`, `KISApiClient(runtime_mode="mock")`
  - `base_url` = `https://openapivts.koreainvestment.com:29443`
  - `token_url` = `https://openapivts.koreainvestment.com:29443/oauth2/tokenP`
  - `key_type_used` = `MOCK_APP_KEY`
  - `KIS_MOCK_APP_KEY` / `KIS_MOCK_APP_SECRET` 사용

- **mode=real**
  - `SafetyGate(runtime_mode="real")`, `KISApiClient(runtime_mode="real")`
  - **현재가 조회만 수행 — 주문 API 절대 호출 금지**
  - `base_url` = `https://openapi.koreainvestment.com:9443`
  - `key_type_used` = `REAL_APP_KEY`

- **mode=paper**
  - KIS API 호출 금지
  - `current_price` = 기존값 유지, 없으면 `close` 사용
  - `price_source` = `PAPER_CLOSE`

#### 컬럼 자동 인식 우선순위

- 종목코드: `stock_code` → `종목코드` → `code` → `ticker`
- 종목명: `stock_name` → `종목명` → `name`
- 가격: `current_price` → `close` → `현재가`
- 종목코드는 반드시 문자열 6자리로 `zfill(6)` 처리

#### 현재가 조회 결과 처리

- `kis_api.get_current_price(code)`가 `None`을 반환해도 전체 중단 금지
- `None`이면 `price_error="QUOTE_NONE"` 저장, `current_price`는 기존값 또는 `close` fallback
- `result is not None` 확인 후 필드 접근
- 일부 종목 실패해도 전체 CSV 저장 계속 진행

#### CSV 추가/갱신 컬럼

| 컬럼 | 설명 |
|------|------|
| `current_price` | 갱신된 현재가 |
| `price_updated_at` | 갱신 시각 |
| `price_mode` | mock / real / paper |
| `price_source` | MOCK_API / REAL_API / PAPER_CLOSE / CLOSE_FALLBACK |
| `price_base_url` | 사용된 base URL |
| `price_token_url` | 사용된 token URL |
| `price_key_type_used` | MOCK_APP_KEY / REAL_APP_KEY |
| `price_error` | 오류 사유 (없으면 빈 문자열) |

#### 백업 파일 처리

```python
# path.rename() 방식 금지 — .bak 파일 중복 시 실패
from shutil import copy2
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = path.with_name(f"{path.stem}.bak_{timestamp}{path.suffix}")
copy2(path, backup)
# 백업 실패는 warning만 남기고 갱신 계속 진행
```

#### 리포트 저장

- `reports/refresh_candidate_prices_YYYYMMDD_HHMMSS.json`
- `reports/refresh_candidate_prices_YYYYMMDD_HHMMSS.txt`
- 포함 항목: mode, input_path, output_path, updated_count, failed_count, price_error 목록, base_url, token_url, key_type_used, exception, stdout/stderr

---

### B. AI 후보 파이프라인에 현재가 갱신 단계 통합

#### 수정 대상 파일

- `app/pages/3_AI_후보_리스트.py`
- `src/select_top20.py`
- `src/select_top_candidates.py`
- `src/force_trade_selector.py`
- `app/services/trading_service.py`
- `src/full_system_verification.py`

#### 수정 후 파이프라인 순서

```
데이터수집 → 피처생성 → 라벨생성 → 모델학습 → 예측생성 → Top100 생성
→ 현재가 갱신 (refresh_candidate_prices.py) → 최종 Top100 저장
```

#### 앱 동작 요구사항

- [ ] "전체 파이프라인 실행" 버튼 클릭 시 Top100 생성 후 즉시 `refresh_candidate_prices.py` 실행
- [ ] 갱신 모드는 앱의 "갱신 모드" 선택값 사용 (기본값: MOCK)
- [ ] `current_price`가 채워진 Top100 CSV를 최종 후보 파일로 사용
- [ ] 화면에 `current_price` 우선 표시, 없거나 0이면 `close` fallback 표시

#### 앱 AI 후보 리스트 화면 UI 추가

- [ ] 갱신 모드 선택 라디오/selectbox (PAPER / MOCK / REAL), 기본값 MOCK
- [ ] "현재가 갱신" 버튼: `python src\refresh_candidate_prices.py --mode 선택값 --date 오늘 --top 100` 실행
- [ ] 실패 시 단순 "오류" 표시 금지 — 실행 명령어 / stdout 마지막 2000자 / stderr 마지막 2000자 / exception / 리포트 파일 경로 표시

#### 후보 테이블 표시 우선순위

- 가격: `current_price` 우선 → 없으면 `close`
- `price_updated_at`, `price_source`, `price_error` 함께 표시

---

### C. 예산배분/주문 미리보기 — `current_price` 기준

#### 수정 대상 파일

- `src/budget_allocator.py`
- `src/buy_candidate_list.py`
- `src/strategy_executor.py`
- `app/pages/4_예산배분_및_주문.py`

#### 규칙

- [ ] 주문 미리보기 / 예산배분 / 주문수량 계산은 `current_price` 최우선 사용
- [ ] `current_price`가 없거나 0이면 `close` fallback 사용
- [ ] `order_price`는 `current_price` 기준으로 호가단위 보정
- [ ] 주문 결과에 `used_price_source` 컬럼 저장:
  - `CURRENT_PRICE_REFRESHED`
  - `CURRENT_PRICE_EXISTING`
  - `CLOSE_FALLBACK`

#### 주문 직전 옵션 추가

- [ ] "주문 전 현재가 재갱신" 체크박스 (기본값: True)
- [ ] MOCK 주문 시 현재가 재조회 후 `order_price` 재계산
- [ ] REAL 주문 시 현재가 조회만 하고, 주문은 기존 REAL 안전조건 충족 시에만 실행

---

### D. 매수 체결 후 목표가 재계산 — 실제 체결가/평균단가 기준

#### 수정 대상 파일

- `src/order_manager.py`
- `src/position_manager.py`
- `src/sync_broker_positions.py`

#### 매수 주문 성공 후 처리

1. KIS 응답에 체결가가 있으면 `filled_price` 사용
2. 체결가 없으면 `order_price` 임시 사용
3. 이후 `sync_broker_positions.py`에서 KIS 계좌 평균단가(`avg_price`)로 재동기화
4. `target_price = avg_price * 1.02` (호가단위 보정)
5. `stop_loss_price = avg_price * 0.97` (호가단위 보정)

#### positions.json 저장 필드

| 필드 | 설명 |
|------|------|
| `avg_price` | KIS 계좌 평균단가 |
| `entry_price` | 최초 주문가 |
| `filled_price` | 실제 체결가 |
| `current_price` | 현재가 |
| `target_price` | 목표가 (avg_price × 1.02, 호가단위 보정) |
| `stop_loss_price` | 손절가 (avg_price × 0.97, 호가단위 보정) |
| `target_basis` | `BROKER_AVG_PRICE` / `FILLED_PRICE` / `ORDER_PRICE_TEMP` |
| `target_recalculated_at` | 목표가 재계산 시각 |
| `take_profit_rate` | 0.02 |
| `stop_loss_rate` | -0.03 |
| `status` | `OPEN` |
| `is_closed` | false |

#### sync_broker_positions.py 실행 시

- [ ] KIS 계좌 실제 평균단가 기준으로 `target_price` 재계산
- [ ] 기존 `target_price`가 후보 close 기준이면 수정
- [ ] `broker_synced_at` 저장
- [ ] `target_basis="BROKER_AVG_PRICE"` 저장

> **중요**: 목표가는 후보 리스트 가격이 아니라 실제 매수 평균단가 기준이어야 한다.

---

### E. 전량 일괄매도 기능

#### 수정 대상 파일

- `app/pages/5_보유종목_및_매도감시.py`
- `app/services/trading_service.py`
- `src/order_manager.py`
- `src/position_manager.py`
- `src/sync_broker_positions.py`
- `src/manual_sell_diagnosis.py`
- `src/force_sell_monitor.py`

#### 앱 UI 추가 요구사항

- [ ] "매도 주문 모드" 선택 UI (PAPER / MOCK / REAL), 기본값 MOCK
- [ ] REAL 선택 시: 빨간 경고 + "실제 계좌에서 실제 매도 주문이 실행됨을 이해했습니다." 체크박스 필수
- [ ] 체크 없으면 REAL 매도 버튼 비활성화

#### 앱 버튼 추가

| 버튼 | 기능 |
|------|------|
| KIS 계좌 동기화 | 브로커 잔고 새로고침 |
| 현재가 갱신 | 보유종목 현재가 갱신 |
| 자동매도 조건 검사 | 목표가/손절가 조건 확인 |
| 현재가 갱신 후 자동매도 실행 | 갱신 + 자동매도 일괄 |
| 선택 종목 매도 | 선택 종목만 매도 |
| 전량 일괄매도 | 전체 보유종목 일괄 매도 |

#### 전량 일괄매도 동작 규칙

- [ ] 선택한 mode 사용
- [ ] mode=mock: KIS MOCK 계좌 보유종목 조회
- [ ] mode=real: KIS REAL 계좌 보유종목 조회 + REAL 안전조건 + 체크박스 확인
- [ ] mode=paper: 로컬 positions.json 기준 가상 매도
- [ ] KIS 계좌 기준 보유수량 우선 사용
- [ ] 로컬 수량과 KIS 수량 다를 경우 KIS 계좌 수량 기준 매도
- [ ] 보유수량 0인 종목: 매도 안 하고 `rejected_reason="NO_BROKER_POSITION_TO_SELL"` 기록
- [ ] 각 종목별 현재가 조회 후 매도 주문가 산정
- [ ] 매도 주문은 `OrderManager.sell_order` 공통 함수로만 실행
- [ ] 일부 실패해도 다음 종목 매도 계속 진행

#### 매도 주문 공통 함수 시그니처

```python
OrderManager.sell_order(
    stock_code,
    quantity,
    mode,
    price=None,
    order_type="limit",
    reason="MANUAL_SELL_ALL"
)
```

#### 매도 공통 실행 경로

```
selected_mode
→ SafetyGate(runtime_mode=selected_mode)
→ OrderManager(runtime_mode=selected_mode)
→ KISApiClient(runtime_mode=selected_mode)
→ get_kis_credentials(mode=selected_mode)
→ get_access_token(mode=selected_mode)
→ validate_final_order_headers()
→ place_sell_order()
```

#### 금지 사항

- `requests.post` 직접 호출 금지
- `SafetyGate()` runtime_mode 없이 생성 금지
- `KISApiClient()` runtime_mode 없이 생성 금지
- `config.yaml`의 `live_trade`로 사용자 선택 mode 덮어쓰기 금지
- `KIS_APP_KEY` 직접 사용 금지 (`get_kis_credentials` 내부에서만 허용)

---

### F. MOCK/REAL 매도 키 및 URL 검증

#### MOCK 매도 검증 조건

| 항목 | 기대값 |
|------|--------|
| `base_url` | `openapivts` 포함 |
| `token_url` | `openapivts` 포함 |
| `key_type_used` | `MOCK_APP_KEY` |
| `headers["appkey"]` | `os.getenv("KIS_MOCK_APP_KEY")` |
| `tr_id` | `VTTC0801U` |
| `mock_order_called` | True |
| `real_order_called` | False |

#### REAL 매도 검증 조건

| 항목 | 기대값 |
|------|--------|
| `base_url` | `openapi.koreainvestment.com:9443` 포함 |
| `token_url` | `openapi.koreainvestment.com:9443` 포함 |
| `key_type_used` | `REAL_APP_KEY` |
| `headers["appkey"]` | `os.getenv("KIS_REAL_APP_KEY")` |
| `tr_id` | `TTTC0801U` |
| `real_order_called` | True |
| `mock_order_called` | False |

#### 검증 실패 시 동작

- API 호출 안 함
- `success=False`, `order_no=""`
- `rejected_reason`: `MODE_KEY_MISMATCH` 또는 `MODE_URL_MISMATCH`
- `expected_appkey_fingerprint`, `header_appkey_fingerprint` 기록
- `app_key_mode_valid=False`, `mode_consistency_valid=False`

---

### G. 매도 결과 저장

#### 저장 파일

- `reports/sell_orders_YYYYMMDD.csv`
- (필요 시) `reports/orders_YYYYMMDD.csv` append

#### 필수 컬럼

| 컬럼 | 설명 |
|------|------|
| `timestamp` | 주문 시각 |
| `side` | SELL |
| `stock_code` | 종목코드 |
| `stock_name` | 종목명 |
| `quantity` | 매도수량 |
| `sell_price` | 매도 주문가 |
| `current_price` | 현재가 |
| `avg_price` | 평균단가 |
| `target_price` | 목표가 |
| `requested_mode` | 요청 모드 |
| `resolved_mode` | 실제 적용 모드 |
| `base_url` | 사용된 base URL |
| `token_url` | 사용된 token URL |
| `key_type_used` | 키 타입 |
| `token_cache_file` | 캐시 파일 경로 |
| `token_source` | 토큰 출처 |
| `expected_appkey_fingerprint` | 기대 appkey 지문 |
| `header_appkey_fingerprint` | 실제 header appkey 지문 |
| `app_key_mode_valid` | 키 모드 일치 여부 |
| `mode_url_valid` | URL 모드 일치 여부 |
| `mode_consistency_valid` | 전체 모드 일관성 |
| `mode_consistency_errors` | 불일치 오류 목록 |
| `api_called` | API 호출 여부 |
| `mock_order_called` | MOCK 주문 호출 여부 |
| `real_order_called` | REAL 주문 호출 여부 |
| `tr_id` | KIS TR_ID |
| `ord_dvsn` | 주문구분 코드 |
| `order_no` | 주문번호 |
| `rt_cd` | KIS 응답 코드 |
| `msg` | KIS 응답 메시지 |
| `raw_msg` | KIS 원본 응답 |
| `response_text` | 응답 전문 |
| `success` | 주문 성공 여부 |
| `rejected_reason` | 거부 사유 |
| `reason` | 매도 이유 |

#### 앱 화면 표시 항목

- 전체 매도 대상 수 / 성공 매도 수 / 실패 매도 수
- 실패 사유 요약 / 주문번호 / mode / key_type_used / app_key_mode_valid

---

### H. 매도 성공 시 positions.json 업데이트

#### 매도 성공 조건

- `order_no`가 있고 `success=True`이며 `rt_cd=0` 또는 KIS 성공 응답

#### 전량 매도 성공 시 필드 업데이트

| 필드 | 값 |
|------|-----|
| `quantity` | 0 |
| `is_closed` | true |
| `status` | `CLOSED` |
| `exit_price` | sell_price |
| `exit_time` | now |
| `exit_reason` | `MANUAL_SELL_ALL` 또는 `TAKE_PROFIT` |
| `sell_order_no` | order_no |

#### 일부 매도 성공 시

- `quantity` 감소
- `last_sell_order_no`, `last_sell_time` 저장
- `status=OPEN` 유지

#### 실패 시

- `status=OPEN` 유지
- `last_sell_attempt_at`, `last_sell_error` 저장

---

### I. 자동매도 — 동일 OrderManager 공통 경로 사용

#### 수정 대상: `src/force_sell_monitor.py`

#### CLI

```
python src\force_sell_monitor.py --mode mock --once --refresh-prices
python src\force_sell_monitor.py --mode mock --watch --interval 10
python src\force_sell_monitor.py --mode paper --once
python src\force_sell_monitor.py --mode real --once
```

#### 자동매도 실행 조건 (모두 충족 시)

- [ ] 매도 판단 직전 현재가 갱신
- [ ] `current_price >= target_price`
- [ ] `auto_take_profit_enabled == true`
- [ ] `manual_only != true`
- [ ] `status == OPEN`
- [ ] `quantity > 0`

#### 자동매도 결과 처리

- 성공 시: positions.json CLOSED 처리 + `sell_orders_YYYYMMDD.csv` 기록
- `OrderManager.sell_order` 공통 함수 사용

---

### J. 수동매도 진단 CLI (`src/manual_sell_diagnosis.py`)

#### CLI

```
python src\manual_sell_diagnosis.py --mode mock --stock-code 055550 --quantity 1 --dry-run
python src\manual_sell_diagnosis.py --mode mock --stock-code 055550 --quantity 1 --execute
python src\manual_sell_diagnosis.py --mode mock --all --dry-run
python src\manual_sell_diagnosis.py --mode mock --all --execute
```

#### 기능 요구사항

- [ ] mode 강제 적용
- [ ] 보유수량 확인
- [ ] 현재가 조회
- [ ] 최종 매도 헤더 검증 (`validate_final_order_headers`)
- [ ] `--dry-run`: 주문 전까지만 검증, 실제 주문 금지
- [ ] `--execute`: 선택 mode 주문 실행
- [ ] `--all`: KIS 계좌 또는 positions.json 기준 전체 보유종목 매도
- [ ] 결과 저장: `reports/manual_sell_diagnosis_YYYYMMDD_HHMMSS.json/txt`

#### MOCK 성공 기준

| 항목 | 기대값 |
|------|--------|
| `base_url` | `openapivts` 포함 |
| `token_url` | `openapivts` 포함 |
| `key_type_used` | `MOCK_APP_KEY` |
| `tr_id` | `VTTC0801U` |
| `app_key_mode_valid` | True |
| `mock_order_called` | True |
| `real_order_called` | False |
| `--execute` 시 | `order_no` 있음, `success=True` |

---

### K. `full_system_verification.py` 보강

#### 추가 검증 항목

- [ ] `refresh_candidate_prices.py` 파일 존재 확인
- [ ] `refresh_candidate_prices.py --mode paper --top 3` 성공
- [ ] `refresh_candidate_prices.py --mode mock --top 3` 성공
- [ ] Top100 CSV에 `current_price` 컬럼 존재 확인
- [ ] `budget_allocator`가 `current_price` 우선 사용하는지 확인
- [ ] `sync_broker_positions.py` 실행 시 `target_basis=BROKER_AVG_PRICE` 저장 확인
- [ ] `manual_sell_diagnosis.py --mode mock --all --dry-run` 성공
- [ ] `force_sell_monitor.py --mode mock --once --refresh-prices` dry-run 또는 안전 검증 성공
- [ ] REAL 주문은 절대 실행하지 않음 확인

---

### L. 수정 완료 후 검증 명령어

```powershell
cd "C:\Users\FURSYS\Desktop\AI stock"

Remove-Item data\mock_token_cache.json -ErrorAction SilentlyContinue
Remove-Item data\token_cache.json -ErrorAction SilentlyContinue
Remove-Item data\real_token_cache.json -ErrorAction SilentlyContinue

python src\mode_diagnosis.py --mode mock
python src\mock_order_diagnosis.py --stock-code 015760 --quantity 1

python src\refresh_candidate_prices.py --mode paper --date 20260610 --top 3
python src\refresh_candidate_prices.py --mode mock --date 20260610 --top 3

python src\sync_broker_positions.py --mode mock --strategy morning_0930

python src\manual_sell_diagnosis.py --mode mock --all --dry-run

python src\force_sell_monitor.py --mode mock --once --refresh-prices

python src\full_system_verification.py
```

#### 성공 기준

| 항목 | 기대 결과 |
|------|-----------|
| `refresh_candidate_prices.py` | `current_price` 갱신 성공 |
| AI 후보 CSV | `current_price`, `price_updated_at`, `price_source` 저장 |
| 후보 리스트 화면 | `current_price` 표시 |
| 예산배분 | `current_price` 기준 계산 |
| 매수 후 `target_price` | `avg_price` 기준 계산 |
| `manual_sell_diagnosis --all --dry-run` | 매도 대상 목록 + 예상 주문 표시 |
| MOCK 매도 dry-run | `base_url=openapivts`, `key_type_used=MOCK_APP_KEY` |
| `force_sell_monitor` | 현재가 갱신 후 자동매도 조건 검사 |
| `full_system_verification.py` | 전체 통과 |

---

### M. Git 반영

```bash
git status -sb
git add src app README.md config.yaml
git commit -m "Implement current-price pipeline refresh and bulk sell execution"
git push origin HEAD
```

---

## 요구사항 우선순위 업데이트 (2026-06-11)

| 순위 | 기능 | 설명 |
|------|------|------|
| P0 | 현재가 갱신 모듈 | `refresh_candidate_prices.py` MOCK/REAL/PAPER 완전 지원 |
| P0 | 파이프라인 현재가 통합 | Top100 생성 후 자동 갱신 |
| P0 | 전량 일괄매도 | MOCK/REAL/PAPER 모드 분리 |
| P0 | 매도 키/URL 검증 | 모드 불일치 시 주문 차단 |
| P1 | 예산배분 `current_price` | 주문가 계산 기준 변경 |
| P1 | 목표가 재계산 | avg_price 기준 target_price |
| P1 | 매도 결과 저장 | sell_orders CSV 완전 기록 |
| P1 | 수동매도 진단 CLI | `manual_sell_diagnosis.py` |
| P2 | 자동매도 공통 경로 | `force_sell_monitor` 통합 |
| P2 | 검증 시스템 보강 | `full_system_verification.py` |

---

## 장중 모멘텀 필터 및 Top20 강제 요구사항 (2026-06-15 추가)

### 배경 및 목적

- AI Top100 후보 중 장중 실시간 데이터(OHLCV)를 기반으로 최종 매수 후보 Top20을 선정해야 한다.
- 예산배분 및 주문 화면에서 최대 주문 건수를 20개로 제한한다.
- `buy_top20_YYYYMMDD.csv` 파일을 파이프라인 최종 출력물로 등록한다.

### N. 장중 매수 후보 Top20 필터 (`src/select_intraday_buy_candidates.py`)

#### CLI 인터페이스

```
python src/select_intraday_buy_candidates.py --mode paper --date 20260615 --top-n 20
python src/select_intraday_buy_candidates.py --mode mock
python src/select_intraday_buy_candidates.py --mode real
```

#### 입출력

- 입력: `reports/predictions/top100_YYYYMMDD.csv`
- 출력: `reports/predictions/buy_top20_YYYYMMDD.csv`
- stdout: JSON (success, candidate_count, output_file, filter_pass_count, warnings)

#### 모드별 동작

| 모드 | 동작 |
|------|------|
| paper | KIS API 미사용. CSV 기존 데이터(close) 활용. 필터 미적용(데이터 없음) |
| mock | KIS 모의 API `get_current_price()` → OHLCV 보강 → 필터 적용 |
| real | KIS 실전 API `get_current_price()` (현재가 조회만, 주문 없음) |

#### 장중 필터 항목 (config.yaml `intraday_filter` 섹션)

| 필터 | config 키 | 기본값 |
|------|-----------|--------|
| 거래대금 | `min_trading_value_krw` | 30,000,000,000 (300억) |
| 최소 상승률 | `min_change_rate_pct` | 1.5% |
| 최대 상승률 | `max_change_rate_pct` | 12.0% |
| 고가 근접도 | `min_high_proximity` | 0.97 (현재가/고가 ≥ 97%) |
| 고점 낙폭 | `max_high_drawdown_pct` | -3.0% |
| VWAP 위 | `require_above_vwap` | true |
| 갭상승 후 밀림 제외 | `exclude_gap_and_fade` | true |

#### 최종 점수 공식

```
final_buy_score =
  ai_score_norm       × 0.30
  + trading_value_score × 0.20
  + change_rate_score   × 0.15
  + high_proximity_score × 0.15
  + vwap_score          × 0.10
  + recent_momentum_score × 0.10
```

- 가중치는 `config.yaml > intraday_filter > score_weights` 에서 관리
- paper 모드: 필터 미적용, ai_score 기준 정렬만 수행

#### Fallback 전략 (필터 통과 종목 부족 시)

1. 통과 ≥ top_n: 정상 선정
2. fallback_min ≤ 통과 < top_n: 통과분 전체 사용
3. 통과 < fallback_min: 필터 완화 (거래대금 1/3, 고가근접 0.94, 상승률 0.5%, VWAP 비요구)
4. 완화 후도 부족: 전체 점수 기준 선정 (경고 표시)

#### allocation_version

- 모든 buy_top20 CSV에 `allocation_version = "TOP20_INTRADAY_FILTERED_V1"` 컬럼 저장

---

### O. 파이프라인 통합 (전체/빠른 파이프라인 마지막 단계)

#### `app/services/pipeline_service.py` 수정 내용

- `run_full_pipeline()`: `refresh_candidate_prices` 이후 `select_intraday_buy_candidates` 단계 추가
- `run_fast_candidate_pipeline()`: `select_top_candidates` 이후 `select_intraday_buy_candidates` 단계 추가
- 두 파이프라인 모두: 이 단계 실패 시 파이프라인 전체 실패로 처리하지 않음 (soft fail)

---

### P. 예산배분 및 주문 화면 Top20 강제 (`app/pages/4_예산배분_및_주문.py`)

#### 변경 사항

- [x] `_load_candidates()`: buy_top20 파일 최우선 탐색 (enriched/top100보다 앞)
- [x] 최대 주문 건수 number_input: `max_value=100` → `max_value=20`, `min(loaded_n, 100)` → `min(loaded_n, 20)`
- [x] "현재 리스트 전부 매수" 버튼: 후보 > 20개이면 주문 차단 + 오류 메시지
- [x] run_buy_candidates 호출 직전 `_safe_max_orders = min(int(max_orders), 20)` 강제
- [x] 주문 미리보기에도 동일 cap 적용

#### buy_top20 파일 탐색 우선순위

1. `reports/predictions/buy_top20_YYYYMMDD.csv` (오늘)
2. `reports/enriched_candidates_YYYYMMDD.csv` (오늘)
3. `reports/predictions/top100_YYYYMMDD.csv` (오늘)
4. 최신 buy_top20_*.csv (fallback)
5. 최신 top100_*.csv (fallback)

---

### Q. buy_candidates 함수 Top20 강제 (`src/buy_candidate_list.py`)

- [x] 기본값 `max_orders=100` → `max_orders=20`
- [x] 함수 진입 즉시 `max_orders = min(int(max_orders), 20)` 강제 적용
- [x] allocations > 20개면 첫 20개만 사용
- [x] `base_result["allocation_version"] = "TOP20_BUDGET_DISTRIBUTION_V1"` 항상 저장

---

### R. 진단 CLI

#### `src/diagnose_intraday_selection.py`

```
python src/diagnose_intraday_selection.py
python src/diagnose_intraday_selection.py --date 20260615
python src/diagnose_intraday_selection.py --show-filtered
```

- buy_top20 파일 유무/내용 확인
- 종목별 score/filter 상태 출력
- `--show-filtered`: Top100 중 필터 제외 종목 표시

#### `src/diagnose_budget_allocation.py`

```
python src/diagnose_budget_allocation.py
python src/diagnose_budget_allocation.py --date 20260615 --budget 300000
python src/diagnose_budget_allocation.py --candidate-file reports/predictions/buy_top20_20260615.csv
```

- 균등 예산배분 시뮬레이션 출력
- Top20 준수 여부 확인
- allocation_version 표시

---

### S. 앱 화면 추가 (`app/pages/3_AI_후보_리스트.py`)

- [x] 파일 상태 메트릭에 "장중 Top20 파일" 추가
- [x] "장중 매수 Top20 필터 실행" 버튼 (모드 선택 포함: paper/mock/real)
- [x] 실행 성공 시 선정 종목 테이블 즉시 표시

---

### T. config.yaml `intraday_filter` 섹션

```yaml
intraday_filter:
  min_trading_value_krw: 30000000000
  min_change_rate_pct: 1.5
  max_change_rate_pct: 12.0
  min_high_proximity: 0.97
  max_high_drawdown_pct: -3.0
  require_above_vwap: true
  exclude_gap_and_fade: true
  fallback_min_candidates: 10
  score_weights:
    ai_score: 0.30
    trading_value_score: 0.20
    change_rate_score: 0.15
    high_proximity_score: 0.15
    vwap_score: 0.10
    recent_momentum_score: 0.10
```

---

### U. 검증 명령어

```powershell
# 장중 필터 (paper 모드)
python src/select_intraday_buy_candidates.py --mode paper

# 장중 선정 진단
python src/diagnose_intraday_selection.py

# 예산배분 진단
python src/diagnose_budget_allocation.py --budget 300000

# buy_candidate_list Top20 강제 확인
python -c "import sys; sys.path.insert(0,'src'); from buy_candidate_list import buy_candidates; import inspect; src=inspect.getsource(buy_candidates); print('max_orders=20' if 'max_orders = min(int(max_orders), 20)' in src else 'FAIL')"
```

---

## 요구사항 우선순위 업데이트 (2026-06-15)

| 순위 | 기능 | 설명 |
|------|------|------|
| P0 | 장중 필터 선정 | `select_intraday_buy_candidates.py` |
| P0 | Top20 강제 | 예산배분/주문 최대 20건 cap |
| P0 | buy_top20 파이프라인 통합 | 전체/빠른 파이프라인 마지막 단계 |
| P1 | 진단 CLI | `diagnose_intraday_selection.py`, `diagnose_budget_allocation.py` |
| P1 | 앱 UI 통합 | 장중 Top20 필터 실행 버튼 |

---

### V. 예산배분·주문 일치 요구사항 (2026-06-15 추가)

#### 원인 분석 (확인된 버그 3종)

**V-1. 예산배분 vs 실제 주문 종목 불일치**
- `run_budget_allocation()`이 held_codes 필터를 적용하지 않아 "예산배분 계산" 결과(20개)와 실제 주문(4개)이 달랐음
- 이미 OPEN 상태로 보유 중인 16개 종목이 실제 주문에서는 제외되었으나 예산배분 계산에서는 포함됨
- `run_buy_candidates()` → `buy_candidates()` → `held_codes` 필터 ↔ `run_budget_allocation()` 불일치

**V-2. buy_top20 vs top20(예측 순위) 파일 불일치**
- 4페이지 "예산배분 및 주문"은 `buy_top20_{date}.csv` (장중 필터 적용 버전)를 우선 로드
- 3페이지 "AI 후보 리스트"는 `top20_{date}.csv` (AI 예측 점수 순위 1-20위)를 표시
- `buy_top20`은 AI 예측 점수 + 장중 거래량/가격 조건으로 재선별 → 종목 구성이 다름
- `top100`이 재생성된 뒤 `buy_top20`이 갱신되지 않으면 구버전 종목이 주문에 포함됨

**V-3. 가격 신선도 (전날 종가)**
- `select_intraday_buy_candidates.py --mode paper`로 생성된 `buy_top20`의 `_data_source=paper_csv`
- paper 모드에서는 KIS API를 호출하지 않으므로 `current_price = close` = 전날 종가
- 장중 주문 시 현재가와 다른 가격으로 주문됨

#### 수정 사항

- [x] `run_budget_allocation()`: `held_codes` 필터 추가 (보유 중인 OPEN 종목 제외)
- [x] `run_budget_allocation()` 반환값에 `held_count`, `held_names`, `available_candidates`, `price_stale`, `price_source` 추가
- [x] 4페이지 파일 정보 표시: 로드된 파일명, 생성 시각, buy_top20 vs top20 설명
- [x] 4페이지 파일 불일치 경고: buy_top20 사용 중 + top20 파일도 존재할 때 경고
- [x] 4페이지 가격 신선도 경고: `_data_source=paper_csv`인 경우 경고 + `현재가 갱신` 체크박스
- [x] 4페이지 예산배분 결과에 held_count 표시 및 제외 종목 목록 표시
- [x] 4페이지 주문 전 "현재 보유 종목 중 후보와 겹치는 항목" 표시
- [x] 3페이지 buy_top20 vs top100 동기화 경고: top100이 더 새 것이면 재생성 안내

#### 올바른 사용 흐름

1. **파이프라인 실행** (AI 후보 리스트 페이지 → 전체 파이프라인): top100 + buy_top20 동시 갱신
2. **장중 필터 실행** (AI 후보 리스트 페이지 → 장중 Top20 필터): top100 → buy_top20 재생성
3. **예산배분 및 주문** 페이지:
   - "예산배분 계산" 버튼: 실제 주문 로직과 동일한 held_codes 필터 적용
   - "주문 미리보기" 버튼: 실제 주문 예정 목록 확인 (held 제외 후 남은 종목)
   - "현재 리스트 전부 매수" 버튼: 미리보기와 동일한 목록으로 주문

#### max_orders 파라미터 설명

`max_orders`는 배분 알고리즘에서 **총 주식 구매 횟수**를 의미합니다 (종목 수가 아님):
- 알고리즘: 각 종목에 1주씩 순서대로 구매를 반복 (`rank_one_share_then_repeat`)
- `max_orders=20`, 후보 4개 → 각 5주 구매 (4×5=20회)
- `max_orders=20`, 후보 20개 → 각 1주 구매 (20×1=20회)
- UI에서 "최대 주문 건수"라 표시되며 기본값은 min(후보수, 20)

---

### VI. KIS API 타임아웃 버그 (2026-06-15 추가)

#### 원인 분석

**VI-1. KIS MOCK 서버 무응답 → 현재가 갱신 실패**
- `openapivts.koreainvestment.com:29443` 가 완전히 응답 없음 (Read timed out)
- `refresh_candidate_prices.py --mode mock`으로 100 종목 갱신 시: 종목당 3×10s = 30s, 100종목 = 3000초(50분) → 300s subprocess 타임아웃으로 강제 종료
- "현재가 갱신" 버튼이 KIS MOCK 모드로 고정되어 있어 갱신이 항상 실패했음

**VI-2. "주문 미리보기" / "전부 매수" → 주문가능금액 0 표시**
- `buy_candidates()` → `_get_orderable_cash()` → `KISApiClient.get_orderable_cash()` 호출
- KIS MOCK 서버 무응답: 3 retry × 10s = 30초 blocking
- Streamlit WebSocket이 30초 hang 동안 연결 유지를 못 하면 스피너가 멈추거나 세션 재시작
- 실제로 fallback `int(budget)` 이 반환되지만 UI가 이미 사라진 상태

**VI-3. `run_script()` env 미전달**
- `prediction_service.run_script()`가 `subprocess.run()`에 `env=os.environ.copy()`를 전달하지 않음
- Streamlit 세션 환경변수(KIS_MOCK_APP_KEY 등)가 subprocess에 전파되지 않아 스크립트 실행 실패 가능

#### 수정 사항

- [x] `src/buy_candidate_list.py` `_get_orderable_cash()`: `concurrent.futures.ThreadPoolExecutor` + 8초 timeout
  - 8초 초과 시 에러 메시지 기록 후 `int(budget)` 즉시 반환 (30초 hang 해소)
- [x] `app/services/trading_service.py` `run_budget_allocation()`: 동일한 8초 threading timeout 적용
- [x] `app/services/prediction_service.py` `run_script()` + `run_full_pipeline()`: `env=os.environ.copy()` 추가
- [x] `app/pages/3_AI_후보_리스트.py` "현재가 갱신" 버튼:
  - 기본 모드를 MOCK → **PAPER** 로 변경 (API 호출 없음, 빠름)
  - MOCK 갱신 실패 시 PAPER 모드로 **자동 재시도**
  - top100 갱신 성공 후 **`buy_top20_{date}.csv`도 함께 갱신** (`--input` 파라미터 사용)
  - KIS 타임아웃 감지 시 명확한 안내 메시지 표시

#### 올바른 사용 흐름 (수정 후)

1. **현재가 갱신**: PAPER 모드 선택(기본값) → 빠르게 top100 + buy_top20 가격 갱신
   - MOCK 모드 선택 시 KIS 서버 응답 없으면 자동으로 PAPER fallback
2. **주문 미리보기 / 전부 매수**: `_get_orderable_cash()` 8초 내 응답 없으면 입력 예산으로 대체
   - "KIS 서버 응답 없음 (8초 초과) — 입력 예산으로 주문가능금액 대체" 메시지 표시

---

## 요구사항 우선순위 업데이트 (2026-06-15 V2)

| 순위 | 기능 | 설명 |
|------|------|------|
| P0 | 예산배분 held_codes 일치 | `run_budget_allocation()`에 held_codes 필터 추가 |
| P0 | 가격 신선도 경고 | buy_top20 paper_csv → 전날 종가 경고 + 갱신 옵션 |
| P0 | 파일 불일치 경고 | top100 갱신 후 buy_top20 미갱신 시 경고 |
| P1 | buy_top20 자동 갱신 | top100 변경 시 buy_top20 자동 재생성 |
| P0 | KIS 타임아웃 30초 hang | threading 8초 timeout으로 즉시 fallback |
| P0 | run_script env 미전달 | subprocess에 os.environ.copy() 전달 |
| P0 | 현재가 갱신 buy_top20 누락 | top100 갱신 후 buy_top20도 함께 갱신 |

---

## 거래정지·위험종목 안전 필터 구현 요구사항 (2026-06-16 추가)

### 배경 및 문제

- 파이프라인 실행 후 후보 리스트와 Top20에 거래정지 종목이 등장하는 현상 발생
- 원인: `stock_master.csv`의 `is_halted`, `is_management` 컬럼이 항상 `False`로 하드코딩됨
- `select_top_candidates.py`의 `apply_hard_exclusions()`가 이 컬럼을 확인하지만, 컬럼 자체가 predictions CSV에 포함되지 않아 필터가 동작하지 않음 (dead code)
- `stock_master.csv`는 생성되지만 downstream 파이프라인 어디에도 병합되지 않았음

### W. Hard Exclusion vs Soft Filter 분류

#### Hard Exclusion (절대 완화 불가 — 후보 리스트에서 완전 제거)

| 필터 | 탐지 방법 | 적용 시점 |
|------|-----------|-----------|
| 거래정지 종목 | 최근 7일(≈5거래일) 내 OHLCV 데이터 없음 | `predict_candidates.py` + `select_top_candidates.py` |
| 관리종목 | `stock_master.csv.is_management=True` | 동일 |
| 우선주 | 종목코드 끝자리 5·7·9, 종목명 "우" 포함 | `collect_daily_data.py:filter_tickers()` |
| 스팩(SPAC) | 종목명 "스팩", "기업인수목적" 포함 | 동일 |
| ETF/ETN | 종목명 "ETF", "ETN", "인버스", "레버리지" 포함 | 동일 |
| 리츠 | 종목명 "리츠" 포함 | `select_today_buy_top20.py` paper mode |

#### Soft Filter (조건 완화 가능 — 필터 통과 종목 부족 시 완화)

| 필터 | config 키 | 기본값 |
|------|-----------|--------|
| 최소 거래대금 (20일 평균) | `risk.min_avg_20d_trading_value` | 5,000,000,000원 |
| 최소 현재가 | `risk.min_price` | 1,000원 |
| 최소 거래대금 (장중) | `safe_intraday_filter.hard_min_trading_value` | — |
| VWAP 위 (safe mode) | `safe_intraday_filter.require_above_vwap` | true |

---

### X. 거래정지 종목 탐지 구현 요구사항

#### X-1. `src/collect_daily_data.py` 수정

- [x] `make_stock_master()` 이후 `collect_all_daily_data()` 완료 시 `_update_stock_master_halted()` 호출
- [x] `_update_stock_master_halted(master_path, output_path, tickers_df)` 함수 구현:
  - 수집된 OHLCV에서 최근 7일(≈5 거래일) 내 데이터가 없는 종목 탐지
  - 해당 종목에 `is_halted=True` 설정 후 `stock_master.csv` 덮어쓰기
  - 실패 시 warning만 기록하고 계속 진행 (파이프라인 중단 금지)

```python
def _update_stock_master_halted(master_path, output_path, tickers_df):
    """OHLCV 기반 거래정지 탐지: 과거 수집 이력 있으나 최근 7일 내 거래 없는 종목 → is_halted=True
    
    주의: tickers_df 전체 기준 비교 금지 — 거래대금 미달로 수집 생략된 종목을 거래정지로
          오인하는 false positive 방지를 위해 실제 OHLCV 수집 이력이 있는 종목만 대상으로 함.
    """
    ohlcv = pd.read_csv(output_path, parse_dates=["date"], dtype={"stock_code": str})
    latest = ohlcv["date"].max()
    recent_cutoff = latest - pd.Timedelta(days=7)
    ever_collected = set(ohlcv["stock_code"].astype(str).str.zfill(6).unique())
    recent_codes = set(ohlcv[ohlcv["date"] >= recent_cutoff]["stock_code"].str.zfill(6))
    halted_codes = ever_collected - recent_codes  # 과거엔 있었지만 최근 없음 = 거래정지 추정
    master = pd.read_csv(master_path, dtype={"stock_code": str})
    master["stock_code"] = master["stock_code"].str.zfill(6)
    master["is_halted"] = master["stock_code"].isin(halted_codes)
    save_csv(master, master_path)
```

#### X-2. `src/predict_candidates.py` 수정

- [x] `predict_all()` 실행 후 `stock_master.csv` 병합:
  - `is_halted=True` 또는 `is_management=True`인 종목을 예측 결과에서 제거
  - 제거된 종목명과 수량 로그 기록
  - `stock_master.csv` 없을 시 warning만 기록하고 필터 생략 (이전 동작 유지)
  - 출력 predictions CSV에는 안전 컬럼(`is_halted`, `is_management`) 포함하지 않음

```python
# predict_candidates.py main() 내 predict_all() 호출 직후
master_path = cfg["data"]["raw_daily_path"].replace("daily_prices.csv", "stock_master.csv")
if os.path.exists(master_path):
    master = pd.read_csv(master_path, dtype={"stock_code": str})
    result = result.merge(master[["stock_code","is_halted","is_management"]], on="stock_code", how="left")
    for col in ("is_halted", "is_management"):
        result[col] = result[col].fillna(False).astype(bool)
    excluded = result["is_halted"] | result["is_management"]
    result = result[~excluded].drop(columns=["is_halted","is_management"])
```

#### X-3. `src/select_top_candidates.py` (기존 코드 유지)

- `apply_hard_exclusions()`는 현재 `is_halted`/`is_management` 컬럼이 있을 때만 필터 적용
- `predict_candidates.py` 수정 이후에는 이미 제거된 상태로 도달하므로 이중 방어 역할
- 컬럼이 없어도 에러 없이 동작함 (기존 `if "is_halted" in df.columns:` 조건 유지)

---

### Y. 기관·외국인 매수 필터 요구사항 (미구현)

- [ ] pykrx `stock.get_market_trading_value_by_date()` 또는 네이버 증권 기관/외국인 순매수 데이터 활용
- [ ] `collect_daily_data.py`에서 기관/외국인 순매수 컬럼 추가 (`inst_net_buy`, `foreign_net_buy`)
- [ ] `make_features.py`에서 최근 5일 기관/외국인 순매수 합계 피처 추가
- [ ] `select_top_candidates.py`에서 기관/외국인 동반 순매수 종목 가중치 부여
- [ ] config.yaml에 `risk.require_institutional_net_buy: false` 설정 (기본값 false — Soft filter)

---

### Z. 안전 필터 파이프라인 데이터 흐름

```
collect_daily_data.py
  ↓ OHLCV 수집 완료 후
  → _update_stock_master_halted()
  → data/raw/stock_master.csv (is_halted, is_management 정확히 설정)

predict_candidates.py
  ↓ predict_all() 호출 후
  → stock_master.csv 병합
  → is_halted=True / is_management=True 종목 제거
  → reports/predictions/predictions_YYYYMMDD.csv (위험종목 없음)

select_top_candidates.py
  ↓ predictions CSV 읽기
  → apply_hard_exclusions() (이중 방어 — is_halted 컬럼 없어도 안전)
  → 이름 기반 Hard exclusion (우선주, 스팩, ETF)
  → reports/predictions/top100_YYYYMMDD.csv (위험종목 없음)

select_intraday_buy_candidates.py (paper mode)
  ↓ top100 읽기
  → 이름·코드 기반 Hard exclusion (리츠, 우선주 재확인)
  → reports/predictions/intraday_candidates_YYYYMMDD.csv

select_today_buy_top20.py (paper mode)
  ↓ intraday_candidates 읽기
  → 이름·코드 기반 Hard exclusion (최후 방어선)
  → reports/predictions/buy_top20_YYYYMMDD.csv (주문 가능 최종 목록)
```

**핵심 원칙**: 위험종목 필터는 생성 시점에 제거. 주문 시점에 재검증 금지 (중복 검증 금지).

---

### 안전 필터 구현 현황

| 필터 | 구현 파일 | 상태 |
|------|-----------|------|
| 우선주 제외 | `collect_daily_data.py:filter_tickers()` | ✅ 구현됨 |
| 스팩 제외 | 동일 | ✅ 구현됨 |
| ETF/ETN 제외 | 동일 | ✅ 구현됨 |
| 최소 거래대금 | 동일 + `make_features.py` | ✅ 구현됨 |
| 거래정지 탐지 | `collect_daily_data.py:_update_stock_master_halted()` | ✅ 2026-06-16 구현 |
| 관리종목 제외 | `stock_master.csv` 기반 (탐지 방법 미완성) | ⚠️ 부분 구현 |
| 거래정지→예측 필터링 | `predict_candidates.py` stock_master 병합 | ✅ 2026-06-16 구현 |
| 투자경고 제외 | 미구현 | ❌ |
| 현재가 1,000원 미만 | 미구현 | ❌ |
| 기관·외국인 매수 | 미구현 | ❌ |

---

## 요구사항 우선순위 업데이트 (2026-06-16)

| 순위 | 기능 | 설명 |
|------|------|------|
| P0 | 거래정지 탐지 구현 | `_update_stock_master_halted()` — OHLCV 기반 자동 탐지 |
| P0 | 예측 단계 거래정지 필터 | `predict_candidates.py` stock_master 병합 후 제거 |
| P1 | 관리종목 탐지 | pykrx 또는 KRX 데이터 연동 |
| P1 | 투자경고 종목 제외 | KRX 투자주의 데이터 연동 |
| P2 | 기관·외국인 필터 | 순매수 피처 추가 |
| P2 | 현재가 1,000원 미만 제외 | predict_candidates.py 또는 select_top_candidates.py |
