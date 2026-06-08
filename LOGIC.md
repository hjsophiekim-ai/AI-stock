# LOGIC.md — 전체 프로그램 로직 설명서

## 전체 흐름 다이어그램

```
[매일 14:30]
    ↓
collect_daily_data.py  ──→  data/raw/daily_prices.csv
collect_intraday_data.py ──→  data/intraday/YYYYMMDD/
    ↓
make_features.py  ──→  data/processed/features.csv
make_labels.py    ──→  data/processed/labeled_dataset.csv
    ↓
train_model.py    ──→  models/model.joblib
    ↓
[매일 14:40]
    ↓
predict_candidates.py  ──→  reports/predictions/predictions_YYYYMMDD.csv
select_top20.py        ──→  reports/predictions/top20_YYYYMMDD.csv
    ↓
[14:40~15:00]
    ↓
order_manager.py (paper_trade 또는 live_trade)
    ↓
[시간외~다음날 09:30]
    ↓
trade_rules.py (+2% 익절 / -3% 손절 / 09:30 강제청산)
    ↓
결과 → reports/paper_trades/ 또는 reports/backtests/
```

---

## 1. 데이터 수집 로직

### 종목 리스트 수집

```
KOSPI 전 종목 + KOSDAQ 전 종목
→ 아래 조건으로 필터링:
  - 우선주 제외 (종목코드 suffix 기준: 5, 6, 7, 8 등)
  - 스팩 제외 (종목명에 "스팩", "SPAC" 포함)
  - ETF/ETN 제외 (시장구분 코드 기준)
  - 관리종목 제외
  - 거래정지 제외
  - 투자경고·주의환기 제외
```

### 일봉 데이터 수집

```
각 종목별:
  - 최근 2년치 (약 500 거래일) OHLCV 수집
  - 컬럼: date, open, high, low, close, volume, trading_value
  - 결측치 처리: 거래정지일은 NaN 또는 전일 복사
  - 오류 발생 시 logs/data_collect_errors.log 기록 후 다음 종목 계속
  - 성공적으로 수집된 종목만 data/raw/daily_prices.csv에 저장
```

### 분봉 데이터 수집

```
각 종목별 (당일 장중):
  - 1분봉 또는 5분봉 수집
  - 09:00~15:00 구간
  - 오후 2시 40분 기준 데이터까지만 사용
  - data/intraday/YYYYMMDD/{종목코드}.csv 저장
```

---

## 2. 피처 생성 로직

### 일봉 기반 피처 (make_features.py)

| 피처명 | 계산식 | 설명 |
|--------|--------|------|
| ret_1d | (close - close.shift(1)) / close.shift(1) | 1일 수익률 |
| ret_3d | (close - close.shift(3)) / close.shift(3) | 3일 수익률 |
| ret_5d | (close - close.shift(5)) / close.shift(5) | 5일 수익률 |
| ret_10d | (close - close.shift(10)) / close.shift(10) | 10일 수익률 |
| ret_20d | (close - close.shift(20)) / close.shift(20) | 20일 수익률 |
| ma5 | close.rolling(5).mean() | 5일 이동평균 |
| ma20 | close.rolling(20).mean() | 20일 이동평균 |
| ma60 | close.rolling(60).mean() | 60일 이동평균 |
| price_to_ma5 | close / ma5 | 현재가/5일 이평 |
| price_to_ma20 | close / ma20 | 현재가/20일 이평 |
| price_to_ma60 | close / ma60 | 현재가/60일 이평 |
| vol_ma5 | volume.rolling(5).mean() | 거래량 5일 평균 |
| vol_ma20 | volume.rolling(20).mean() | 거래량 20일 평균 |
| vol_ratio | volume / vol_ma20 | 오늘 거래량 / 20일 평균 |
| trading_value | close * volume | 거래대금 |
| tv_ma20 | trading_value.rolling(20).mean() | 거래대금 20일 평균 |
| hl_spread | (high - low) / low | 고저 변동폭 비율 |
| open_close_ret | (close - open) / open | 시가 대비 종가 수익률 |
| is_bullish | (close > open).astype(int) | 양봉 여부 |
| upper_tail | (high - max(open,close)) / (high-low) | 윗꼬리 비율 |
| lower_tail | (min(open,close) - low) / (high-low) | 아랫꼬리 비율 |
| volatility_5d | close.pct_change().rolling(5).std() | 5일 변동성 |
| pos_in_20d_high | close / high.rolling(20).max() | 20일 최고가 대비 위치 |
| pos_in_20d_low | close / low.rolling(20).min() | 20일 최저가 대비 위치 |
| consec_up_5d | 최근 5일 연속 상승 일수 | 연속 상승 |
| consec_dn_5d | 최근 5일 연속 하락 일수 | 연속 하락 |

### 분봉 기반 피처 (당일 14:40 기준)

| 피처명 | 설명 |
|--------|------|
| intraday_ret_1440 | 09:00 기준 14:40까지 수익률 |
| pos_vs_intraday_high | 14:40 현재가 / 당일 고점 |
| intraday_vol_surge | 14:40 거래량 / 전일 평균 거래량 대비 폭증 여부 |
| vwap_position | 현재가 / VWAP (거래량 가중 평균가) |

---

## 3. 라벨 생성 로직

### target_2pct_next_morning

```python
# 개념 코드 (실제 구현은 make_labels.py 참고)
# 매수 기준가: 당일 오후 2시 40분 이후 종가 또는 보수적으로 종가 사용
entry_price = close  # 또는 intraday_close_1440

# 다음 거래일 고가 기준으로 +2% 도달 여부 확인
# 분봉 데이터 있으면: 다음날 09:30 이전 분봉 고가 기준
# 분봉 데이터 없으면: 다음날 고가 기준 (보수적)
target_price = entry_price * 1.02

if next_day_high >= target_price:
    target_2pct_next_morning = 1
else:
    target_2pct_next_morning = 0
```

### 데이터 누수 방지

```
[날짜 t의 피처] = t일까지의 정보만 사용
[날짜 t의 라벨] = t+1일 데이터로 계산

예시:
- features[date=2024-01-05] = close[2024-01-05]까지의 계산값
- target[date=2024-01-05] = 2024-01-08 (다음 거래일)의 고가로 계산

주의:
- shift(-1)로 내일 데이터를 오늘 피처 행에 붙이는 방식을 쓰면 누수 위험!
- 반드시 (피처 행 = 날짜 t) + (라벨 = t+1일 계산) 구조를 명확히 유지
```

---

## 4. 모델 학습 로직

### 학습/검증 분리

```
전체 데이터: 2년치 일봉
→ 앞 80%: 학습 (train)
→ 뒤 20%: 검증 (validation)
→ 랜덤 셔플 없음 — 반드시 시간 순서 유지

예시:
  train: 2022-01-01 ~ 2023-09-30
  valid: 2023-10-01 ~ 2024-12-31
```

### Walk-Forward Validation (선택적)

```
window_size = 252  # 1년치
step_size = 63     # 분기마다 재학습

fold 1: train=[0:252], valid=[252:315]
fold 2: train=[0:315], valid=[315:378]
...
→ 각 fold 결과 평균
```

### 핵심 성과지표

```
precision_at_top20 = (모델이 상위 20개로 선정한 종목 중 실제 +2% 도달 종목 수) / 20
→ 이 값이 높을수록 좋은 모델

top20_average_return = 상위 20개 종목의 실제 다음날 오전 9시 30분 청산 기준 평균수익률
```

---

## 5. 종목 선정 로직

### 1단계: 모델 예측

```
predict_candidates.py:
  → 전 종목에 대해 target_2pct_next_morning 확률 계산
  → 확률 내림차순 정렬
  → 상위 50개 후보 선정
```

### 2단계: 필터 적용

```
select_top20.py:
  필터 체크리스트:
  ✓ 최근 20일 평균 거래대금 50억 원 이상
  ✓ 당일 거래대금 30억 원 이상
  ✓ 현재가 1,000원 이상
  ✓ 관리종목 아님
  ✓ 투자경고·주의환기 아님
  ✓ 거래정지 아님
  ✓ 우선주 아님
  ✓ 스팩 아님
  ✓ ETF/ETN 아님
  ✓ 당일 상한가 근접 아님 (전일 대비 +25% 이상)
  ✓ 호가 공백 과도하지 않음
```

### 3단계: 종합 점수 계산

```
score = (
  prediction_prob * 0.4 +
  vol_ratio_score * 0.2 +         # 거래량 폭증
  intraday_momentum * 0.2 +       # 장중 수급
  proximity_to_high * 0.2         # 최고가 근접도
)
→ 상위 20개 최종 선정
```

---

## 6. 매수 로직

```
시간: 14:40~15:00
방식: 지정가 또는 최우선매수호가
비중: 동일 비중 또는 score 가중 비중
한 종목당 최대 5% (총 투자금의)

live_trade=false → paper_trade 기록만
live_trade=true  → risk_manager 승인 후 실제 주문
```

---

## 7. 매도 로직

```
우선순위:
1. 매수가 +2% 도달 시 → 즉시 매도
2. 매수가 -3% 도달 시 → 손절
3. 다음날 09:30 → 강제청산 (미체결이면 재시도)

매도 가능 시간대:
- 15:30~16:00 시간외 단일가
- 08:00~09:00 장전 시간외
- 09:00~09:30 다음날 정규장 초반
- 09:30 이후: 원칙적으로 전량 청산
```

---

## 8. 결과 저장 로직

```
실행 후 생성되는 파일:
  reports/predictions/predictions_YYYYMMDD.csv    # 전 종목 예측 확률
  reports/predictions/top20_YYYYMMDD.csv          # 최종 선정 20개
  reports/paper_trades/paper_trade_log.csv        # 모의투자 누적 로그
  reports/backtests/backtest_summary.txt          # 백테스트 요약
  reports/backtests/backtest_trades.csv           # 백테스트 거래 내역
  reports/model_evaluation.txt                    # 모델 평가 결과
  logs/data_collect_errors.log                    # 데이터 수집 오류
  logs/trade_errors.log                           # 주문/체결 오류
```
