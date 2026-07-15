# frontend — dart-search 웹 UI (M4)

React 18 + Vite + TypeScript + Mantine 기반 SPA. 백엔드(`backend/`, FastAPI)가
`http://localhost:8000`에서 실행 중이어야 정상 동작한다 — DART API 키는 프론트에
전혀 두지 않으며, 모든 DART 호출은 백엔드를 경유한다(CLAUDE.md 원칙).

## 실행 명령

```
cd frontend
npm install
npm run dev      # http://localhost:5173, /api/* 요청은 vite.config.ts의 proxy 설정으로
                  # http://localhost:8000 (backend) 으로 전달됨
npm run build     # tsc -b (타입체크) + vite build
npm run lint       # oxlint
npm run preview    # build 결과물 로컬 프리뷰
```

백엔드를 먼저 띄워야 한다 (별도 터미널):

```
cd backend
source .venv/Scripts/activate   # PowerShell은 .venv\Scripts\Activate.ps1
uvicorn app.main:app --reload   # http://127.0.0.1:8000
```

## 구조

```
frontend/src/
├─ api/            # 백엔드 REST 클라이언트 (jobs.ts / meta.ts / results.ts / client.ts)
├─ types/          # 백엔드 응답/요청 타입 (backend/app/api/*.py 스키마와 일치)
├─ components/      # RegionSelect, IndustryTreeSelect, JobStatusBadge, ColumnToggle,
│                    # ResultDetailDrawer 등 재사용 컴포넌트
├─ pages/
│  ├─ SearchPage.tsx    # 검색 조건 입력 → POST /api/jobs
│  ├─ JobsPage.tsx      # 작업 목록/진행률 (RUNNING 상태가 있으면 2초 폴링)
│  └─ ResultPage.tsx    # 결과 테이블/필터 탭/상세 패널/Excel·CSV 다운로드
├─ util/            # 날짜 변환, 컬럼 정의, Job 조건 요약 등 순수 함수
├─ App.tsx           # 상단 네비게이션 + react-router 라우트 (/search, /jobs, /jobs/:id/results)
└─ main.tsx          # MantineProvider, Notifications, BrowserRouter 부트스트랩
```

## 참고

- 상세개발계획.md §6(API 설계)/§7(화면 설계)을 그대로 구현했다.
- `POST /api/jobs` 요청 시 매출액은 억원 단위 입력값을 `min_krw`/`max_krw`(원 단위,
  1억원 = 100,000,000원)로 환산해서 보낸다.
- `GET /api/meta/regions`, `/api/meta/industries`, `GET /api/jobs/{id}/export`는
  dart-backend 에이전트가 M4와 병행 구현했다 — 이 프론트는 계약(contract)만 보고
  개발했고, 실제 백엔드 구현과 end-to-end로 스모크 테스트(Playwright, 임시)까지
  확인했다.
- "예상 대상 규모 미리보기" 버튼(§7-1)은 대응하는 백엔드 API가 아직 없어 이번
  스코프에서 제외했다 — 버튼 자체를 만들지 않았다.
