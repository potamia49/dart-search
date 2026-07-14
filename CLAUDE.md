# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 현황

**M1(기반 구축) 스캐폴딩 완료.** `backend/`는 상세개발계획.md §3 트리를 따라 생성되어 있고
(`app/api`, `app/core`, `app/parsers`, `app/models`, `app/exporters`, `tests/fixtures`),
`config.py`/DB 모델 6종/`dart_client.py`/`corp_cache.py`/`meta.py`(quota, validate-key)/
`main.py`가 구현되어 있다. `jobs.py`, `results.py`, `pipeline.py`, `filters.py`,
`parsers/*`, `exporters/excel.py`는 M2/M3에서 채울 골격(TODO 주석)만 있는 상태다.
`frontend/`는 아직 스캐폴딩되지 않았다(M4 범위, 마일스톤 순서 준수).

**M1의 ★스파이크(금융위 API 커버리지 실측)는 아직 실행되지 않았다** — OpenDART API 키와
공공데이터포털 키가 아직 발급되지 않았기 때문. 스크립트는
`backend/app/core/spike_financial_committee_coverage.py`에 준비되어 있으며, 키 발급 후
`cd backend && python -m app.core.spike_financial_committee_coverage`로 실행해 대응 1/2
채택을 결정해야 한다 (이후 M2 착수 전 필수).

작업을 시작하기 전에 반드시 아래 두 문서를 먼저 읽으세요 —
이 저장소의 유일한 진실 소스(source of truth)입니다.

- [PRD.md](PRD.md) — 제품 요구사항: 무엇을, 왜 만드는지, 확보 가능한 데이터 항목, 리스크
- [상세개발계획.md](상세개발계획.md) — 위 PRD를 웹앱으로 구현하기 위한 기술 설계:
  아키텍처, DB 스키마, API 설계, 파이프라인 단계, 마일스톤

코드를 작성하기 시작하면, 실제 구현이 두 문서와 달라지는 지점(설계 변경, 스파이크 결과 등)이
생길 수 있습니다. 그런 경우 이 CLAUDE.md와 상세개발계획.md를 함께 갱신해 다음 세션이
최신 상태를 참고할 수 있게 하세요.

## 제품 개요

지역 / 매출액 범위 / 업종 조건을 입력하면 OpenDART API 기반으로 **외부감사대상 비상장
법인**의 기본정보 + 요약 재무정보(당기·전기)를 자동 수집하는 도구. 세무회계사무소가
신규 거래처를 발굴하기 위한 용도(Phase 2에서 조건에 맞는 회사에 보낼 전단지/진단자료 생성으로
이어짐)이며, **Phase 1(현재 범위)은 데이터 수집기 + 결과 조회 웹앱까지만** 다룬다.
전단지 생성은 별도 착수 예정(Phase 2)이지만, Phase 1에서 수집하는 데이터 필드는 Phase 2 요구사항까지
미리 고려해 설계되어 있다 (재수집 방지) — [상세개발계획.md §10](상세개발계획.md).

## 핵심 아키텍처 (계획)

```
브라우저 (React SPA)
  └─ REST API (폴링 방식 진행률 조회)
      FastAPI 서버 ─── SQLite (corp_cache / corp_profiles / jobs / results)
        └─ 백그라운드 워커: 6단계 수집 파이프라인
            └─ OpenDART API (corpCode / list / company / document)
```

- 백엔드: Python 3.12 + FastAPI, 수집 작업은 `BackgroundTasks` 기반 Job으로 실행
  (수 분~수 시간 소요, 진행률은 프론트가 폴링)
- DB: SQLite (SQLAlchemy) — 단일 파일, 배포 시 PostgreSQL 전환 가능하게 설계
- 프론트: React 18 + Vite + TypeScript (Mantine 또는 shadcn/ui)
- HTTP: `httpx` 비동기 (OpenDART 병렬 호출, 타임아웃/재시도)
- 재무제표 파싱: XML 1순위(`lxml`) → PDF 2순위(`pdfplumber`) → HWP는 실패 기록만
- 계획된 디렉터리 구조와 각 모듈의 책임은 [상세개발계획.md §3](상세개발계획.md)의 트리를 그대로 따를 것

### 왜 이렇게 설계되었는가 — 반드시 알아야 할 구조적 제약

1. **OpenDART에는 지역 검색 파라미터가 없다.** 회사 주소는 기업개황(company.json)을
   회사별로 1건씩 조회해야만 알 수 있어, "김해만 검색"해도 후보 전체(연간 약 3~4만 개사)의
   기업개황이 필요하다. 이를 해결하기 위해 두 가지 대응을 계획해 두었다:
   - **대응 1(우선)**: 공공데이터포털 금융위원회_기업기본정보 API로 주소 DB를 일괄
     구축한 뒤 회사명 매칭으로 지역 후보를 먼저 추리고, 추려진 후보만 DART company.json으로
     확정한다. M1 마일스톤에서 소형 외감법인 커버리지를 스파이크로 검증해야 채택 확정.
   - **대응 2(폴백)**: `corp_profiles` 전역 캐시 테이블에 조회한 기업개황을 Job과 무관하게
     영구 저장해, 재검색 시 캐시만으로 즉시 필터링한다 (최초 1회는 일일 한도 20,000건
     제약으로 약 2일 소요).
   - 두 방식 다 인터페이스를 동일하게 유지해 교체 비용을 최소화하도록 설계됨
     ([상세개발계획.md §4-1](상세개발계획.md)).
2. **비상장 외감법인은 재무제표 API(fnlttSinglAcntAll 등)를 지원하지 않는다.** 상장법인/IFRS
   사업보고서 제출대상만 지원되므로, 재무정보는 **감사보고서 원문(document.xml)을
   다운로드해 직접 파싱**해야 한다. 이것이 파이프라인에서 가장 리스크가 큰 구간(M3,
   [상세개발계획.md §4-4](상세개발계획.md))이다.
3. **매출액은 구조적으로 사후 필터일 수밖에 없다.** 원문을 파싱하기 전에는 매출액을 알
   수 없기 때문. 대신 지역·업종 필터를 먼저 통과시켜 다운로드/파싱 대상을 최소화한다.
4. **일일 API 호출 한도 20,000건.** `dart_client.py`가 호출 카운터를 내장해 상한 도달 시
   Job을 `PAUSED_QUOTA`로 자동 전환, 다음 날 재개 가능해야 한다. 각 STEP은 DB에 체크포인트를
   남겨 **중단 후 이어하기(resume)**가 가능해야 함.
5. **파싱은 100% 자동화되지 않는다.** 회사마다 원문 서식이 달라 `parse_status`
   (OK/PARTIAL/FAILED)를 결과마다 남기고, 화면에서 검수 필요 건을 필터링해 재시도할 수
   있게 한다.

### DB 스키마 핵심 테이블

- `corp_cache`: corpCode.xml 전체 고유번호 목록 캐시
- `corp_profiles`: 기업개황 **전역** 캐시 (Job과 무관, 재검색 시 재사용 — 지역 필터 성능의 핵심)
- `jobs`: 검색 조건 + 진행 상태 (`PENDING/RUNNING/PAUSED_QUOTA/DONE/FAILED/CANCELLED`)
- `results`: 회사 1건 = 1행, 기본정보 + 당기(`_cur`)/전기(`_prv`) 재무 13항목 + `parse_status`
- `api_usage`: 일일 호출량 카운터

전체 컬럼 정의는 [상세개발계획.md §5](상세개발계획.md) 참고.

## 개발 시작 시 참고사항

- API 키(OpenDART, 공공데이터포털)는 `.env` + `pydantic-settings`로 관리, 코드 하드코딩 금지.
  프론트에는 절대 노출하지 않고 모든 DART 호출은 백엔드 경유.
- 마일스톤 순서(M1 기반 구축 → M2 파이프라인 → M3 파싱 → M4 프론트 → M5 검수)를 따르는 것을
  권장. 특히 **M1의 금융위 API 커버리지 스파이크**는 이후 지역 필터 구현 방식(대응 1 vs 2)을
  결정하므로 가장 먼저 검증해야 한다.
- `backend/tests/fixtures/`에 샘플 감사보고서 원문(10개사)을 두고 파서 단위 테스트를 작성하는
  구조로 계획되어 있다 — 실제 원문 파일이 없으면 M3 작업을 시작할 수 없으니 먼저 확보할 것.

### 백엔드 실행/테스트 명령 (M1 스캐폴딩 완료 후 실제 확인된 명령)

```
cd backend
python -m venv .venv            # Python 3.11 또는 3.12 권장 (아래 "Python 버전 주의" 참고)
source .venv/Scripts/activate   # Windows Git Bash 기준. PowerShell은 .venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env            # 실제 키 발급 후 값 채워넣기 (커밋 금지)

uvicorn app.main:app --reload   # http://127.0.0.1:8000, 기동 시 SQLite 테이블 자동 생성
pytest tests/ -q                # 파서 fixtures 확보 전에는 placeholder 테스트만 존재
```

**Python 버전 주의**: 이 개발 환경의 기본 `python`이 3.14였는데, 3.14용 `pandas`/`lxml` 등의
사전 빌드 wheel이 아직 없어 `pip install -r requirements.txt`가 Meson/C 빌드 단계에서 실패했다
(Visual Studio 빌드 도구 필요). **Python 3.11 또는 3.12로 가상환경을 만들 것** — 이 저장소는
`py -3.11 -m venv .venv`로 확인 완료. frontend는 아직 없음(M4 착수 시 이 섹션에 `npm run dev`,
`npm run lint` 추가할 것).
