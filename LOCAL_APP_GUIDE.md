# AI Stock 자동매매 로컬 앱 사용 설명서

> **중요**: 이 시스템은 +2% 익절 목표 전략을 사용하며, **수익을 보장하지 않습니다.**
> 투자 결과에 대한 책임은 사용자 본인에게 있습니다.

---

## 1. 앱 실행 전 준비사항

```bash
cd "C:\Users\FURSYS\Desktop\AI stock"
# 가상환경 활성화 (없으면 전역 Python 사용)
.venv\Scripts\activate

# 패키지 설치
pip install -r requirements.txt

# 앱 실행
streamlit run app\streamlit_app.py
```

또는 배치 파일로 실행:
```
run_app.bat
```

브라우저에서 열리는 주소: **http://localhost:8501**

---

## 2. API 키 입력 방법

1. 브라우저에서 왼쪽 사이드바 → **1. API 설정** 클릭
2. **한국투자증권 Open API** (https://apiportal.koreainvestment.com)에서 앱 등록
3. 모의투자 API 정보 입력:
   - KIS_APP_KEY: 모의투자용 앱키
   - KIS_APP_SECRET: 모의투자용 앱시크릿 (입력 시 가려짐)
   - KIS_MOCK_ACCOUNT_NO: 모의투자 계좌번호
4. **모의투자 키 저장** 버튼 클릭
5. `.env` 파일이 프로젝트 루트에 자동 생성됨

---

## 3. PAPER / MOCK / REAL 모드 차이

| 모드 | 설명 | API 호출 | 실제 자금 |
|------|------|----------|-----------|
| **PAPER** | 가상 거래. 로컬에만 기록 | ❌ | ❌ |
| **MOCK** | 한국투자증권 모의투자 서버 | ✅ | ❌ |
| **REAL** | 실전투자. 실제 자금 사용 | ✅ | ✅ |

**기본값은 PAPER**입니다. 절대 임의로 REAL 모드로 변경하지 마세요.

---

## 4. 실전(REAL) 주문 전환 조건

REAL 모드는 **5가지 조건이 모두 충족**될 때만 가능합니다:

1. `live_trade: true` (config.yaml)
2. `kis.use_mock: false` (config.yaml)
3. `safety.confirm_live_trade: true` (config.yaml)
4. API 설정 화면에서 3가지 동의 체크박스 모두 체크
5. 실전투자 API 키 저장 완료

**API 설정 화면 → REAL 모드 전환 섹션** 에서 진행하세요.

---

## 5. 후보 리스트 생성 방법

1. 사이드바 → **3. AI 후보 리스트** 클릭
2. 데이터 파이프라인 실행 버튼:
   - 데이터 수집 → 피처 생성 → 모델 예측 → Top20 선정 순서로 클릭
3. 또는 `python src/collect_daily_data.py` 등 직접 실행 가능

**후보 파일 위치**: `reports/predictions/top20_YYYYMMDD.csv`

---

## 6. 예산 입력 및 주문 테스트 방법

1. 사이드바 → **4. 예산배분 및 주문** 클릭
2. 총 예산 입력 (기본값: 100,000원)
3. **예산배분 계산** 버튼 클릭
4. 주문 모드 선택: **PAPER** (기본값, 권장)
5. 종목코드/종목명/현재가 입력
6. **PAPER 주문 실행** 버튼 클릭
7. 결과가 화면에 표시됨

---

## 7. 주문이 0건일 때 확인할 것

1. **3. AI 후보 리스트** 화면에서 Top20 파일 존재 여부 확인
2. **거래 0건 원인 분석 실행** 버튼 클릭
3. 또는 `python src/no_trade_analyzer.py` 직접 실행
4. `reports/no_trade_analysis_YYYYMMDD.txt` 파일 확인

주요 원인:
- 예측 파일 없음 → 데이터 파이프라인 실행 필요
- 필터 기준 너무 엄격 → force_trade 모드 고려
- live_trade=false → PAPER 모드에서는 가상 주문만 가능

---

## 8. 현재 수익률 분석 화면

사이드바 → **6. 수익률 분석** 클릭

표시 항목:
- 총 거래 횟수, 승률, 실현손익, 누적수익률
- +2% 익절 / -3% 손절 / 강제청산 횟수
- 일별 손익 차트 (Plotly)
- 누적수익률 차트
- 거래 내역 다운로드

---

## 9. 로그 확인 방법

사이드바 → **8. 로그 및 긴급중단** 클릭

탭에서 확인 가능:
- `trade.log`: 매수/매도 기록
- `api.log`: API 호출 기록
- `error.log`: 오류 기록
- API 연결 보고서
- no_trade 원인 분석

---

## 10. 긴급중단 방법

**방법 1**: 메인 화면 → **긴급중단 ON** 버튼 클릭

**방법 2**: 사이드바 → 8. 로그 및 긴급중단 → **긴급중단 ON** 클릭

**방법 3**: 직접 파일 생성
```bash
echo STOP > data/EMERGENCY_STOP
```

긴급중단 활성화 시 `data/EMERGENCY_STOP` 파일이 생성되며,
모든 신규 매수 주문이 차단됩니다. 매도는 계속 가능합니다.

해제하려면 **긴급중단 해제** 버튼 또는 파일 삭제:
```bash
del data/EMERGENCY_STOP
```

---

## 11. 자주 발생하는 오류와 해결 방법

### streamlit not found
```bash
pip install streamlit
```

### ModuleNotFoundError: No module named 'xxx'
```bash
pip install -r requirements.txt
```

### config.yaml 로드 오류
- 프로젝트 루트에서 streamlit을 실행했는지 확인
- `streamlit run app\streamlit_app.py` (루트에서 실행)

### API 토큰 발급 실패
- `.env` 파일의 KIS_APP_KEY, KIS_APP_SECRET 확인
- 한국투자증권 API 포털에서 앱 상태 확인
- 모의투자 URL: `https://openapivts.koreainvestment.com:29443`

### UnicodeDecodeError (Windows)
- Windows 콘솔에서 한글 로그 출력 시 발생
- `chcp 65001` 명령으로 UTF-8 설정 후 재실행

### 주문가능금액 0원
- 모의투자 계좌에 예수금 입금 필요
- KIS 모의투자 사이트에서 예수금 충전

---

## 12. force_trade 모드 안내

**이 모드는 예산 범위 내 최소 1건 이상 주문 발생을 목표로 합니다.**
**수익을 보장하는 기능이 아닙니다.**

활성화 방법:
```yaml
# config.yaml
force_trade:
  enabled: true
```

4단계 필터 완화:
1. `normal_filters`: 기본 거래대금 + 변동성 필터
2. `relax_trading_value`: 거래대금 기준 완화
3. `relax_volatility`: 변동성 기준 제거
4. `score_only_with_hard_exclusions`: 점수 기준만 사용

**hard exclusion은 어떤 단계에서도 우회되지 않습니다**:
- 거래정지, 관리종목, 투자주의환기
- 우선주, 스팩, ETF/ETN
- 현재가 1,000원 미만

---

*이 문서는 자동 생성되었습니다. 마지막 업데이트: 2026-06-08*
