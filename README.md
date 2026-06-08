# AI stock — Next Morning 2% Target AI Strategy

> **경고: 본 프로그램은 수익을 보장하지 않습니다.**
> 주식 자동매매는 큰 손실을 초래할 수 있습니다.
> 반드시 백테스트 → 모의투자(paper trade) → 소액 실전 순서로 진행하세요.
> `live_trade: false`가 기본값이며, 명시적으로 변경하기 전까지 실제 주문은 절대 실행되지 않습니다.

---

## 프로젝트 개요

**프로젝트명:** AI stock  
**목적:** 국내 주식 전 종목의 최근 3년치 일봉 데이터를 학습하여, 다음 거래일 오전까지 +2% 이상 상승 가능성이 높은 후보 종목(top100)을 예측하는 AI 자동매매 시스템  
**전략명:** Next Morning 2% Target AI Strategy  
**데이터 소스:** pykrx / FinanceDataReader / 한국투자증권 Open API (KIS Developers)  
**후보 기준:** +2% 익절 목표 후보 — 수익 보장이 아닌 모델 기반 후보군입니다.

---

## 3년치 차트 학습 및 Top100 후보 생성

### 1. 패키지 설치

```bash
pip install -r requirements.txt
# 핵심 패키지 별도 확인
pip install pykrx FinanceDataReader lightgbm
```

### 2. 의존성 확인

```bash
python src/check_dependencies.py
python src/check_dependencies.py --install-missing
```

### 3. 전체 파이프라인 실행 (권장)

```bash
# 테스트 실행 (100개 종목, 빠름)
python src/run_ai_prediction_pipeline.py --years 3 --limit 100

# 전체 종목 실행 (시간 오래 걸림)
python src/run_ai_prediction_pipeline.py --years 3 --all

# 예산 배분까지 실행
python src/run_ai_prediction_pipeline.py --years 3 --all --budget 300000
```

### 4. 단계별 수동 실행

```bash
python src/collect_daily_data.py --years 3 --limit 100  # 데이터 수집
python src/make_features.py                             # 피처 생성
python src/make_labels.py                               # 라벨 생성
python src/train_model.py                               # 모델 학습
python src/predict_candidates.py                        # 전체 예측
python src/select_top_candidates.py --top-n 100 --all  # top20/50/100 생성
```

### 5. Top100 후보 파일 확인

```
reports/predictions/top100_YYYYMMDD.csv
reports/predictions/top50_YYYYMMDD.csv
reports/predictions/top20_YYYYMMDD.csv
```

### 6. MOCK 자동매매 실행

```bash
python src/force_auto_trade.py --budget 300000 --mode mock --min-orders 1 --max-orders 100
```

### 7. MOCK 자동매매 실행 시 거래가 0건인 경우

| 증상 | 원인 | 해결책 |
|------|------|--------|
| `force_trade.enabled=false` 출력 | config 미설정 | `config.yaml`에서 `force_trade.enabled: true`로 변경 |
| `SafetyGate init: [PAPER]` 출력 | `--mode mock` 미전달 또는 runtime_mode 미적용 | 반드시 `--mode mock` 인자 사용 |
| `'list' object has no attribute 'items'` | `data/positions.json`이 `[]` 형식 | 자동 보정됨 (재실행 시 해결) |
| 후보 종목 없음 | top100 파일 미생성 | `python src/run_ai_prediction_pipeline.py --years 3` 먼저 실행 |

**정상 실행 명령어:**
```bash
# PAPER 모드 (가상 기록 전용)
python src/force_auto_trade.py --budget 300000 --mode paper --min-orders 1 --max-orders 100

# MOCK 모드 (한국투자증권 모의투자 API 호출)
python src/force_auto_trade.py --budget 300000 --mode mock --min-orders 1 --max-orders 100

# REAL 모드 (실전 — 3중 안전장치 모두 충족 필요)
# config.yaml: live_trade=true, kis.use_mock=false, safety.confirm_live_trade=true
python src/force_auto_trade.py --budget 300000 --mode real --min-orders 1 --max-orders 100
```

**모드 비교:**

| 항목 | PAPER | MOCK | REAL |
|------|-------|------|------|
| API 호출 | ✗ | ✓ (모의) | ✓ (실전) |
| live_trade=false여도 실행 | ✓ | ✓ | ✗ |
| kis.use_mock=false 필요 | ✗ | ✗ | ✓ |
| confirm_live_trade=true 필요 | ✗ | ✗ | ✓ |
| 실제 자금 사용 | ✗ | ✗ | ✓ |

> `--mode mock`은 실전 주문이 아니므로 `live_trade=false` 상태에서도 실행됩니다.
> `data/positions.json`이 `[]` 리스트이면 시작 시 자동으로 `{}` 딕셔너리로 보정됩니다.

### 8. 시간외 자동매매 지원

`force_auto_trade.py`는 현재 시각을 판단하여 정규장/시간외단일가/장전시간외 주문을 자동 선택합니다.

**세션별 주문구분 코드 (ORD_DVSN):**

| 세션 | 시간대 (KST) | ORD_DVSN | 상태 |
|------|------------|----------|------|
| 정규장 (REGULAR) | 09:00 ~ 15:20 | `00` (지정가) | 공식 확인됨 |
| 동시호가 (CLOSING_AUCTION) | 15:20 ~ 15:30 | — | 미지원 |
| 장후시간외 (AFTER_CLOSE) | 15:30 ~ 16:00 | `62` (후보) | 공식 문서 재확인 필요 |
| 시간외단일가 (AFTER_HOURS_SINGLE) | 16:00 ~ 18:00 | `61` (후보) | 공식 문서 재확인 필요 |
| 장전시간외 (PRE_MARKET) | 08:30 ~ 09:00 | `60` (후보) | 공식 문서 재확인 필요 |
| 장 마감 (CLOSED) | 그 외 | — | 주문 불가 |

> **REAL 모드 주의:** `ORD_DVSN "61"/"62"/"60"`은 후보 코드입니다.
> [KIS Developers](https://apiportal.koreainvestment.com/)에서 직접 확인 후
> `config.yaml: after_hours.real_order_confirmed: true`로 변경해야 REAL 주문이 허용됩니다.
> 미확인 상태에서 REAL 모드로 시간외 주문 시도 시 `NotImplementedError`로 차단됩니다.

**config.yaml 설정:**
```yaml
trading_hours:
  pre_market_start: "08:30"
  pre_market_end: "09:00"
  regular_start: "09:00"
  regular_end: "15:20"
  closing_auction_start: "15:20"
  closing_auction_end: "15:30"
  after_close_start: "15:30"
  after_close_end: "16:00"
  after_hours_single_start: "16:00"
  after_hours_single_end: "18:00"

after_hours:
  enabled: true
  allow_pre_market: true
  allow_regular: true
  allow_closing_auction: false
  allow_after_close: false
  allow_after_hours_single: true
  real_order_confirmed: false   # true로 변경 시 REAL 시간외 주문 허용
```

**MOCK 자동매매 (16:00~18:00 시간외단일가 세션에서 실행):**
```bash
python src/force_auto_trade.py --budget 300000 --mode mock --min-orders 1 --max-orders 100
# 배너에 "현재 세션 : AFTER_HOURS_SINGLE" 표시
# ORD_DVSN=61 로 KIS 모의투자 API 호출
```

**시간외 주문 문제 해결:**

| 증상 | 원인 | 해결책 |
|------|------|--------|
| `모의투자 장종료 입니다.` | 정규장 코드(`ORD_DVSN=00`)를 시간외에 사용 (Phase 3 이전) | Phase 3 적용 후 자동 해결됨 |
| `모의투자에서 제공하지 않는 주문유형입니다.` | KIS MOCK 서버가 시간외 주문 유형(`ORD_DVSN=61` 등)을 미지원 | 정상 동작 — KIS 모의투자 서버 자체 제약. 실전 API에서는 지원될 수 있음 |
| `세션(AFTER_HOURS_SINGLE) 주문 비활성화` | config `allow_after_hours_single: false` | `config.yaml`에서 `true`로 변경 |
| `REAL 모드 NotImplementedError` | `real_order_confirmed: false` | KIS 공식 문서 확인 후 `true`로 변경 |
| `세션(CLOSED) 장 마감` | 18:00 이후 또는 08:30 이전 | 허용 시간대에 재실행 |

> KIS 모의투자 서버(`openapivts.koreainvestment.com`)는 시간외 주문 유형을 지원하지 않는 경우가 많습니다.
> 코드가 정상적으로 `ORD_DVSN=61`을 전송하고 있다면, MOCK 서버 제약이며 코드 오류가 아닙니다.

### 9. 시스템 전체 검증

```bash
python src/full_system_verification.py
```

최종 판정 기준:
- `READY_FOR_PAPER`: PAPER 매매만 가능
- `READY_FOR_MOCK`: API/MOCK 주문 가능
- `READY_FOR_AI_PREDICTION`: 3년치 데이터, 모델, top100 후보 생성까지 완료
- `READY_FOR_MOCK_AUTOTRADE`: top100 기반 MOCK 자동매매 가능
- `READY_FOR_REAL_REVIEW`: 실전 인증 가능, 실전 주문은 수동 검토 필요

> **주의:** top100 후보는 투자 추천이 아닌 모델 기반 후보군입니다. 실제 수익은 보장되지 않습니다. 실전 주문은 반드시 MOCK 검증 이후 소액으로만 진행하세요.

---

## 핵심 전략 요약

| 단계 | 시간 | 내용 |
|------|------|------|
| 데이터 수집 | 14:30 | 전 종목 일봉 업데이트 + 당일 분봉 수집 |
| AI 분석 | 14:40 | 전 종목 상승확률 계산 |
| 종목 선정 | 14:40 | 필터 적용 후 상위 20개 선정 |
| 매수 | 14:40~15:00 | 지정가 분할 매수 (live_trade=true 시만 실행) |
| 익절 | 시간외~다음날 09:30 | 매수가 +2% 도달 시 즉시 매도 |
| 강제청산 | 다음날 09:30 | 목표 미달성 전량 청산 |

---

## 로컬 앱 화면 실행 방법

브라우저 기반 대시보드를 통해 API 설정·AI 후보 리스트·예산 배분·보유 종목 감시·수익률 분석·로그·긴급중단 기능을 한 화면에서 사용할 수 있습니다.

### 빠른 실행 (Windows)

```batch
REM 방법 1 — 배치 파일 더블클릭
run_app.bat

REM 방법 2 — PowerShell
.\run_app.ps1

REM 방법 3 — 직접 실행
cd "C:\Users\FURSYS\Desktop\AI stock"
.venv\Scripts\activate
streamlit run app\streamlit_app.py
```

브라우저에서 **http://localhost:8501** 으로 접속합니다.

### 화면 구성

| 화면 | 기능 |
|------|------|
| **메인 (홈)** | 거래 모드 배지, 안전 장치 상태, 요약 카드, 긴급중단 버튼 |
| **1. API 설정** | 모의·실전 API 키 입력(마스킹 표시), REAL 모드 3중 동의 전환 |
| **2. API 연결 테스트** | 환경변수 → 토큰 → 잔고 → 현재가 단계별 테스트 |
| **3. AI 후보 리스트** | 데이터 수집→예측→Top20 파이프라인 실행, 후보 테이블·CSV 다운로드 |
| **4. 예산배분 및 주문** | 예산 입력, PAPER/MOCK/REAL 주문 실행 |
| **5. 보유종목 및 매도감시** | 보유 종목 손익, +2% 익절·-3% 손절·강제청산 버튼 |
| **6. 수익률 분석** | 승률·실현손익·누적수익률 차트(Plotly) |
| **7. 백테스트 결과** | 백테스트 요약·개별 거래 산점도 |
| **8. 로그 및 긴급중단** | trade/api/error 로그 탭, 긴급중단 ON/OFF |

### 첫 실행 시 순서

1. `run_app.bat` 실행 → 브라우저에서 **1. API 설정** 열기
2. 모의투자 API 키 입력 후 **모의투자 키 저장** 클릭 (`.env` 자동 생성)
3. **2. API 연결 테스트** → **전체 연결 테스트 실행** 으로 연결 확인
4. **3. AI 후보 리스트** → 파이프라인 순서대로 실행
5. **4. 예산배분 및 주문** → PAPER 모드에서 주문 테스트
6. **8. 로그 및 긴급중단** → 언제든지 긴급중단 가능

> **기본 모드는 PAPER(가상 거래)** 입니다. 실제 자금은 사용되지 않습니다.  
> REAL 모드 전환에는 3중 안전 잠금 + 3가지 동의 체크박스가 필요합니다.

자세한 사용법은 `LOCAL_APP_GUIDE.md` 참고.

---

## 설치 방법

### 1. Python 환경 설정

```bash
cd "C:\Users\FURSYS\Desktop\AI stock"
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 2. 환경변수 설정

`.env.example`을 복사하여 `.env` 파일을 만들고 실제 키를 입력합니다.

```bash
copy .env.example .env
# 이후 .env 파일을 열어 실제 API 키 입력
```

`.env` 파일 예시:
```
KIS_APP_KEY=실제앱키
KIS_APP_SECRET=실제앱시크릿
KIS_ACCOUNT_NO=계좌번호
KIS_ACCOUNT_PRODUCT_CODE=01
KIS_USE_MOCK=true
```

### 3. 한국투자증권 Open API 신청

- [KIS Developers](https://apiportal.koreainvestment.com/) 접속
- 앱 등록 → APP KEY, APP SECRET 발급
- 모의투자 계좌 개설 (실전 전환 전 필수)

자세한 내용은 `API_SETUP.md` 참고

---

## 실행 순서

### Step 1. 데이터 수집

```bash
# 최근 2년치 전 종목 일봉 데이터 수집 (최초 1회 또는 일 1회 업데이트)
python src/collect_daily_data.py

# 당일 분봉 데이터 수집 (장중 또는 장 마감 후)
python src/collect_intraday_data.py
```

### Step 2. 피처 및 라벨 생성

```bash
python src/make_features.py
python src/make_labels.py
```

### Step 3. 모델 학습

```bash
python src/train_model.py
```

학습 결과는 `reports/model_evaluation.txt`에 저장됩니다.

### Step 4. 예측 및 종목 선정

```bash
python src/predict_candidates.py   # 전 종목 상승확률 계산
python src/select_top20.py         # 필터 적용 후 Top 20 선정
```

결과는 `reports/predictions/top20_YYYYMMDD.csv`에 저장됩니다.

### Step 5. 백테스트

```bash
python src/backtest.py --start 2023-01-01 --end 2024-12-31
```

결과는 `reports/backtests/` 폴더에 저장됩니다.

### Step 6. 모의투자 (Paper Trade)

```bash
python src/paper_trade.py
```

실제 주문 없이 전략을 시뮬레이션합니다. 결과는 `reports/paper_trades/`에 저장됩니다.

### Step 7. 자동매매 실행 (Auto Trade)

```bash
# 현재 시간대 자동 감지 후 해당 단계 실행 (권장)
python src/run_live_trader.py --mode once

# 14:40~15:00 매수만 실행
python src/run_live_trader.py --mode buy

# 현재 보유 종목 매도 감시만 실행
python src/run_live_trader.py --mode monitor

# 전량 강제청산 (09:30)
python src/run_live_trader.py --mode force-exit

# 30초 간격 반복 실행
python src/run_live_trader.py --mode loop --loop-interval 30
```

#### 3단계 안전 잠금 (Triple Safety Lock)

실전 매매(REAL 모드)를 실행하려면 `config.yaml`에서 세 가지를 **모두** 설정해야 합니다:

```yaml
live_trade: true                    # 1단계
kis:
  use_mock: false                   # 2단계
safety:
  confirm_live_trade: true          # 3단계
```

| 조건 | 모드 | 실제 자금 |
|------|------|----------|
| `live_trade: false` | PAPER | 없음 (가상) |
| `live_trade: true` + `use_mock: true` | MOCK | 없음 (모의투자 계좌) |
| 세 조건 모두 충족 | REAL | **실제 자금 사용** |

#### 긴급 중단 (Emergency Stop)

프로그램 실행 중 즉시 중단이 필요할 때:

```bash
# 긴급 중단 활성화 (파일 생성)
echo "emergency" > data/EMERGENCY_STOP

# 긴급 중단 해제
del data\EMERGENCY_STOP
```

### Step 8. 테스트 실행

```bash
pytest
```

---

## 데이터 수집 방법

- `collect_daily_data.py`: KOSPI/KOSDAQ 전 종목의 최근 2년치 일봉 데이터를 수집하여 `data/raw/daily_prices.csv`에 저장
- `collect_intraday_data.py`: 당일 1분봉/5분봉 데이터를 수집하여 `data/intraday/` 폴더에 저장
- 오류 발생 종목은 `logs/data_collect_errors.log`에 기록

필터 옵션 (`config.yaml`에서 설정):
- 관리종목, 투자경고·주의환기 종목 제외
- 거래정지 종목 제외
- 우선주 제외
- 스팩(SPAC) 제외
- ETF/ETN 제외
- 최소 거래대금 미달 종목 제외

---

## 학습 방법

- 기본 모델: LightGBM Classifier
- 대체 모델: Random Forest Classifier (LightGBM 설치 실패 시)
- 학습/검증 분리: 시간 순서 기준 (랜덤 셔플 없음)
- 핵심 평가지표: `precision_at_top20` (모델이 상위 20개로 선정한 종목 중 실제 +2% 도달 비율)

---

## 예측 방법

- 매일 오후 2시 40분 이후 최신 데이터 기준으로 전 종목 상승확률 계산
- 거래대금, 가격, 위험종목 필터 적용
- 최종 20개 종목 CSV 출력

---

## 백테스트 방법

```bash
python src/backtest.py --start 2023-01-01 --end 2024-12-31 --top-n 20 --threshold 0.6
```

옵션:
- `--start`: 백테스트 시작일
- `--end`: 백테스트 종료일
- `--top-n`: 매일 매수할 종목 수
- `--threshold`: 상승확률 최소 기준

---

## 모의투자 방법

`config.yaml`에서 `live_trade: false`, `paper_trade: true` 확인 후:

```bash
python src/paper_trade.py
```

모의투자 결과는 `reports/paper_trades/paper_trade_log.csv`에 누적 저장됩니다.

---

## 실전 전환 조건 (권장)

다음 조건을 모두 충족한 후에만 실전 전환을 고려하세요:

- [ ] 최소 6개월 이상 백테스트에서 수수료·세금·슬리피지 차감 후 양의 수익
- [ ] 최소 3개월 이상 모의투자에서 전략 검증
- [ ] 최대낙폭(MDD) 수준 이해 및 수용
- [ ] 전략 실패 시 손실 한도 설정
- [ ] `live_trade: true` 변경 전 충분한 이해

---

## 실제 API 키 입력 후 자동매매 검증 방법

> **force_trade 모드는 수익을 보장하는 기능이 아닙니다. 후보 종목 중 조건을 완화하여 예산 범위 내에서 주문 발생 가능성을 높이는 기능입니다. 손실이 발생할 수 있으며, 반드시 소액으로만 테스트해야 합니다.**

```batch
cd "C:\Users\FURSYS\Desktop\AI stock"
.venv\Scripts\activate

copy .env.example .env
REM .env에 한국투자증권 Open API 키 입력

REM 1. API 연결 테스트
python src/api_connection_test.py --stock-code 005930

REM 2. 주문 검증 테스트 (기본값: PAPER 또는 MOCK)
python src/order_verification_test.py --stock-code 005930 --amount 10000

REM 3. 예측 후보 생성
python src/predict_candidates.py
python src/select_top20.py

REM 4. 거래 보장 후보 선정
python src/force_trade_selector.py

REM 5. 예산 배분 확인
python src/budget_allocator.py --budget 100000 --min-orders 1

REM 6. PAPER 거래 보장 테스트
python src/force_auto_trade.py --budget 100000 --mode paper

REM 7. MOCK 거래 보장 테스트
python src/force_auto_trade.py --budget 100000 --mode mock

REM 8. 거래가 0건이면 원인 분석
python src/no_trade_analyzer.py

REM 9. 매수 후 자동 매도 감시
python src/force_sell_monitor.py --loop --sleep 10

REM 10. 강제청산
python src/force_sell_monitor.py --force-exit
```

### 단계별 설명

| 단계 | 명령어 | 설명 |
|------|--------|------|
| 1 | `api_connection_test.py` | .env 확인 → 토큰 발급 → 잔고조회 → 현재가조회 순서 검증 |
| 2 | `order_verification_test.py` | PAPER/MOCK 주문 가능 여부 단계별 검증 |
| 3 | `predict_candidates.py` + `select_top20.py` | AI 예측 후보 파일 생성 |
| 4 | `force_trade_selector.py` | 필터 완화 단계를 거쳐 최소 1개 이상 후보 선정 |
| 5 | `budget_allocator.py` | 예산을 후보 종목에 배분하여 주문수량 계산 |
| 6-7 | `force_auto_trade.py` | 배분된 예산으로 PAPER/MOCK 주문 실행 |
| 8 | `no_trade_analyzer.py` | 거래 0건 원인을 파일로 분석 |
| 9 | `force_sell_monitor.py --loop` | +2% 익절 / -3% 손절 / 09:30 강제청산 감시 |
| 10 | `force_sell_monitor.py --force-exit` | 보유 종목 전량 즉시 청산 |

---

### 실전 소액 주문 테스트

**기본값에서는 절대 실행되지 않습니다.** 아래 5가지를 모두 `config.yaml`에서 설정한 후에만 실전 주문이 발생합니다.

```yaml
# config.yaml — 실전 소액 주문 테스트 설정
live_trade: true
paper_trade: false

kis:
  use_mock: false

safety:
  confirm_live_trade: true
  allow_real_test_order: true

force_trade:
  enabled: true
  allow_real_test_order: true
  test_order_amount: 10000        # 최대 10,000원 한도
  max_force_trade_budget: 100000  # 총 예산 상한 100,000원
```

실전 소액 주문 명령어:
```batch
REM 주문 후 즉시 취소 (테스트 목적)
python src/order_verification_test.py --stock-code 005930 --amount 10000 --cancel-after-order

REM force_trade 실전 소액 주문 (최소 1건)
python src/force_auto_trade.py --budget 100000 --mode real --min-orders 1
```

**실전 주문 전 확인사항:**
- [ ] .env에 실전 투자 앱키/시크릿/계좌번호 입력
- [ ] 모의투자에서 최소 1개월 검증 완료
- [ ] `test_order_amount: 10000` (소액) 설정 확인
- [ ] 긴급 중단 방법 숙지: `echo emergency > data/EMERGENCY_STOP`

---

### 긴급 중단 방법

```batch
REM 모든 신규 주문 즉시 차단
echo emergency > data\EMERGENCY_STOP

REM 긴급 중단 해제
del data\EMERGENCY_STOP
```

---

## .env API 키 진단 및 자동 보정

`.env` 파일의 키 이름 오류·예시값·누락 여부를 자동으로 진단하고 표준 키 이름으로 보정합니다.  
민감정보(App Key, Secret, 계좌번호)는 마스킹해서만 출력합니다.

```batch
cd "C:\Users\FURSYS\Desktop\AI stock"
python src/env_diagnosis_and_fix.py
```

보정 후 재검증:
```batch
python src/env_diagnosis_and_fix.py --rerun-verification
```

주요 기능:
- `.env` 없으면 기본 템플릿 자동 생성
- `KIS_MOCK_APP_KEY` ↔ `KIS_APP_KEY` 상호 보완 복사
- `KIS_ACCOUNT_APP_SECRET` 같은 비표준 키 이름 자동 탐지·표준명 매핑
- 예시값(`your_app_key_here` 등) 잔존 시 명확히 경고
- 수정 전 `.env.backup_YYYYMMDD_HHMMSS` 자동 백업
- 진단 보고서: `reports/env_diagnosis_YYYYMMDD_HHMMSS.txt / .json`

**READY_FOR_MOCK 조건** (아래 3가지 모두 OK여야 함):
1. `KIS_APP_KEY` 또는 `KIS_MOCK_APP_KEY` — 모의투자 App Key
2. `KIS_APP_SECRET` 또는 `KIS_MOCK_APP_SECRET` — 모의투자 App Secret
3. `KIS_MOCK_ACCOUNT_NO` — 모의투자 계좌번호

App Key가 없으면 [KIS Developers](https://apiportal.koreainvestment.com) 포털에서 모의투자 앱을 등록하여 발급받으세요.

---

## 실제 동작 통합 검증 방법

환경 설정부터 API 연결, PAPER/MOCK 주문, 매도감시, 강제청산 안전장치, Streamlit 앱까지 실제 동작을 단계별로 자동 검증합니다. **REAL 실전 주문은 절대 실행하지 않습니다.**

```batch
cd "C:\Users\FURSYS\Desktop\AI stock"
.venv\Scripts\activate
pip install -r requirements.txt

python src/full_system_verification.py
```

검증 보고서 위치:
```
reports/full_system_verification_YYYYMMDD_HHMMSS.txt
reports/full_system_verification_YYYYMMDD_HHMMSS.json
logs/full_system_verification.log
```

검증 항목 (총 20단계):

| 단계 | 항목 | 설명 |
|------|------|------|
| 1 | 환경 검증 | Python, 패키지, config.yaml, .env, 폴더 |
| 2 | 설정 검증 | 현재 모드(PAPER/MOCK/REAL), 안전장치 상태 |
| 3 | API 키 검증 | .env 키 존재 여부, 마스킹 출력 |
| 4 | API 연결 | MOCK 토큰·현재가·잔고·주문가능금액, REAL 토큰만 |
| 5 | 후보 생성 | predictions/top20/force_trade CSV 파일 생성 |
| 6 | 예산배분 | budget_allocator 실행, 주문수량 계산 |
| 7 | PAPER 주문 | force_auto_trade.py --mode paper |
| 8 | MOCK 주문 | force_auto_trade.py --mode mock (키 있을 때만) |
| 9 | 주문검증 | order_verification_test.py --cancel-after-order |
| 10 | 매도감시 | force_sell_monitor.py 1회 실행 |
| 11 | 강제청산 | force_sell_monitor.py --force-exit 안전장치 확인 |
| 12 | no_trade 분석 | 거래 0건 원인 보고서 생성 |
| 13 | Streamlit 앱 | import, 파일 존재, 구문 검사 |

최종 판정:
- `READY_FOR_PAPER` — PAPER 모드 가능 (API 키 없어도 됨)
- `READY_FOR_MOCK` — MOCK 모드 가능 (API 키 + 토큰 발급 성공)
- `READY_FOR_REAL_REVIEW` — REAL 조건 검토 가능 (REAL 토큰 발급 성공)
- `NOT_READY` — 환경 문제 또는 치명적 오류

앱 실행:
```batch
streamlit run app\streamlit_app.py
```

---

## 거래 보장 모드 (force_trade) 상세 설명

필터 완화 단계:

| 단계 | 이름 | 적용 필터 |
|------|------|----------|
| 1 | `normal_filters` | 기본 필터 전부 적용 |
| 2 | `relax_trading_value` | 거래대금 기준 완화 (relaxed 값 사용) |
| 3 | `relax_volatility` | 변동성 기준 추가 완화 |
| 4 | `score_only_with_hard_exclusions` | 점수 상위 종목만 적용 |

어떤 단계에서도 절대 선정되지 않는 종목 (hard exclusion):
- 거래정지, 관리종목, 투자주의환기종목
- 우선주, 스팩, ETF/ETN
- 현재가 1,000원 미만
- 주문가능수량 0

---

## 투자 위험 고지

- **본 프로그램은 투자 수익을 보장하지 않습니다.**
- 주식 투자는 원금 손실이 발생할 수 있습니다.
- AI 모델의 예측은 과거 데이터 기반이며, 미래 수익을 예측하지 않습니다.
- 자동매매 프로그램의 오작동, API 오류, 시장 급변 등으로 예상치 못한 손실이 발생할 수 있습니다.
- **소액으로 충분히 검증한 후 투자 규모를 결정하세요.**
- 본 코드는 교육·연구 목적으로 제공되며, 실제 투자 손실에 대한 책임은 사용자 본인에게 있습니다.
