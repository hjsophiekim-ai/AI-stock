# PROJECT_PLAN.md — 개발 계획

## Phase 1: 기반 구축 (현재)
- [x] 프로젝트 구조 생성
- [x] 문서 작성 (README, REQUIREMENTS, LOGIC, STRATEGY, RISK_POLICY, API_SETUP, BACKTEST_POLICY)
- [x] config.yaml, .env.example, requirements.txt, .gitignore
- [x] src/ 기본 코드 뼈대 생성
- [x] tests/ 테스트 코드 생성

## Phase 2: 데이터 파이프라인
- [ ] KIS API 인증 구현
- [ ] 전 종목 일봉 데이터 수집 완성
- [ ] 분봉 데이터 수집 완성
- [ ] 피처 생성 완성
- [ ] 라벨 생성 완성

## Phase 3: 모델
- [ ] LightGBM 모델 학습
- [ ] Walk-forward validation 구현
- [ ] 성과 평가 (precision_at_top20 중심)

## Phase 4: 백테스트
- [ ] 백테스트 엔진 구현
- [ ] 수수료·세금·슬리피지 반영
- [ ] 성과 리포트 생성

## Phase 5: 모의투자
- [ ] Paper trade 엔진 구현
- [ ] KIS API 연동 테스트 (시세 조회)
- [ ] 1개월 이상 모의투자 운영

## Phase 6: 실전 전환 (검증 완료 후)
- [ ] live_trade=true 전환 조건 충족 확인
- [ ] 소액 실전 테스트
- [ ] 전체 자동화
