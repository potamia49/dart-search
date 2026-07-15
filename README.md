# dart-search

지역 / 매출액 범위 / 업종 조건을 입력하면 OpenDART API 기반으로 **외부감사대상
비상장 법인**의 기본정보 + 요약 재무정보(당기·전기, 옵션으로 최근 N년 이력)를
자동 수집하는 웹앱. 세무회계사무소가 신규 거래처를 발굴하기 위한 데이터 수집기 +
결과 조회 도구다(Phase 1 범위). 제품 배경/요구사항은 [PRD.md](PRD.md), 기술
설계는 [상세개발계획.md](상세개발계획.md), 현재 구현 현황과 실측 기반 설계
판단은 [CLAUDE.md](CLAUDE.md)를 참고할 것.

## 아키텍처 개요

```
브라우저 (React SPA, frontend/)
  └─ REST API (폴링 방식 진행률 조회)
      FastAPI 서버 (backend/) ─── SQLite (corp_cache / corp_profiles / jobs / results / financial_snapshots)
        └─ 백그라운드 워커: 7단계 수집 파이프라인
            └─ OpenDART API + 공공데이터포털 금융위원회_기업기본정보 API
```

- 백엔드: Python + FastAPI, 수집 작업은 `BackgroundTasks` 기반 Job(`PENDING → RUNNING →
  DONE`, 쿼터 초과 시 `PAUSED_QUOTA`, 실패 시 `FAILED`, 취소 시 `CANCELLED`)
- 프론트: React 18 + Vite + TypeScript + Mantine. DART API 키는 프론트에 전혀 두지
  않으며 모든 DART 호출은 백엔드를 경유한다.
- 재무제표는 원문(감사보고서 XML/PDF)을 직접 다운로드해 파싱한다 — 비상장
  외감법인은 DART 재무제표 API를 지원하지 않기 때문(자세한 배경은 CLAUDE.md
  "왜 이렇게 설계되었는가" 참고).

## 필요한 API 키

| 키 | 발급처 | 용도 |
|---|---|---|
| `DART_API_KEY` | https://opendart.fss.or.kr/ (회원가입 → 인증키 신청/관리) | 공시목록/기업개황/감사보고서 원문 조회 |
| `DATA_GO_KR_API_KEY` | https://www.data.go.kr/ (활용신청 → "금융위원회_기업기본정보조회서비스") | 지역 필터 사전 추림(대응 1) — 회사명으로 주소를 먼저 가볍게 확인해 DART `company.json` 호출량을 절감 |

두 키 모두 발급에 승인 대기 시간이 있을 수 있다(공공데이터포털은 보통 즉시~수 시간).

## 백엔드 실행

```
cd backend
py -3.11 -m venv .venv          # Python 3.11 또는 3.12 권장 — 3.14는 pandas/lxml
                                 # 사전 빌드 wheel이 없어 설치가 실패한다
source .venv/Scripts/activate   # Git Bash 기준. PowerShell은 .venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env            # 위 표의 두 키 값을 채워넣기 (커밋 금지, .gitignore 처리됨)

uvicorn app.main:app --reload   # http://127.0.0.1:8000, 기동 시 SQLite 테이블 자동 생성
pytest tests/ -q                # 단위 테스트
```

## 프론트엔드 실행

백엔드를 먼저 띄운 상태에서(별도 터미널):

```
cd frontend
npm install
npm run dev      # http://localhost:5173 — /api/* 요청은 vite.config.ts의 proxy 설정으로
                  # http://localhost:8000 (backend)으로 전달됨
npm run build     # tsc -b (타입체크) + vite build
npm run lint       # oxlint
```

자세한 화면/컴포넌트 구조는 [frontend/README.md](frontend/README.md) 참고.

## 사용 흐름

1. `/search` 화면에서 지역(시도/시군구) / 매출액 범위 / 업종 / 공시 조회 기간 /
   재무 이력 조회 기간(2·4·6·10년)을 입력해 Job을 생성한다.
2. `/jobs` 화면에서 진행 상태를 확인한다 — `RUNNING`이면 2초마다 자동 폴링하고,
   `PAUSED_QUOTA`(일일 20,000건 한도 도달)는 다음 날 "재개" 버튼으로 이어서
   실행할 수 있다.
3. `/jobs/:id/results` 화면에서 결과 테이블을 조회한다. `parse_status`
   (OK/PARTIAL/FAILED)와 매출액 제외 여부로 필터링할 수 있고, 파싱 실패 건은
   "재시도" 버튼으로 재파싱할 수 있다. 행을 클릭하면 당기·전기 재무 13항목,
   재무 이력(N개년), DART 원문 링크를 확인할 수 있다. 현재 필터를 반영한
   Excel/CSV로 다운로드할 수 있다.

## 현재 진행 상황

M1(기반 구축) → M2(수집 파이프라인) → M3(재무제표 파싱) → M4(프론트엔드) +
STEP7(재무 이력)까지 구현 및 end-to-end 스모크 테스트 완료. M5(검수 및
안정화 — 샘플 수동 검수, 실전 조건 풀 실행, 성능/쿼터 실측)는 진행 중이다.
세부 이력과 실측 기반 설계 판단은 [CLAUDE.md](CLAUDE.md)에 날짜별로 기록되어
있다.
