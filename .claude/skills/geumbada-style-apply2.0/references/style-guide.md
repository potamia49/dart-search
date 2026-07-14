---
brand: 금바다세무회계
slug: geumbada-report
version: "2.0"
generated: 2026-07-13
source_type: internal_design_system
confidence: high
is_official: true

region: korea
industry:
  - accounting
  - tax
  - professional-services

color_tone: cool-professional-document
primary_color_hex: "#1F4E79"
primary_color_name: "Geumbada Report Blue"
accent_color_hex: "#C9A227"
accent_color_name: "Geumbada Gold"

font_category: sans-serif
font_primary: Noto Sans KR
font_korean_supported: true

density: data-dense-document
corner_style: subtle-round
flatness: semi-flat

theme_modes:
  - light

target_media:
  - screen
  - print-A4

css_file: geumbada-report-style.css
signature_keyword: "Noto Sans KR + Navy/Primary-Blue 그라디언트 + KPI 배너·비율 카드·연령분석 막대바를 갖춘 재무 보고서 표준"

sources:
  - geumbada-report-style.css
  - (설계 근거) 결산·채권현황 보고서 시안 대화 — 2026-07-13
---

## 왜 v1(geumbada-style-apply)과 별도 스킬인가

v1(`geumbada-style-apply`)은 질의응답서·세무 검토의견서·공문처럼 **서술형 텍스트가 중심**인 문서를 위해 설계됐다. 답변 블록(`gb-answer-block`), 법령 카드(`gb-law-card`), 결론 박스(`gb-conclusion`) 등이 핵심 컴포넌트다.

이 스킬(v2.0)은 **계정별원장·부속명세서 같은 회계 원장 데이터를 파싱해 만드는 결산·채권현황·경영분석 보고서**를 위해 설계됐다. 이런 문서는 서술형 텍스트보다 다음이 핵심이다:

- 페이지 최상단에서 한눈에 보는 **KPI 배너** (당기순이익·매출·비용·채권잔액 등)
- **당기 vs 전기(동기간) vs 전기(연간) 비교표**가 반복적으로 나열됨(손익·재무상태·특이사항 전부)
- **비율 카드**(영업이익률·회전율 등) — 숫자 하나가 곧 콘텐츠
- **채권 연령분석 막대바** — 구간별 색상이 위험도를 인코딩해야 함

이 요구사항에 맞춰 v1보다 팔레트를 한 톤 낮춰(밝은 액션 블루 `#2563EB` → 차분한 문서 블루 `#1F4E79` + 네이비 `#0A192F`) "숫자를 오래 들여다봐도 피로하지 않은" 문서 톤을 만들었다. 골드는 v1과 동일하게 브랜드 강조 전용으로 유지한다.

이 설계는 2026-07-13 대화에서 실제 거래처 원장 데이터(금바다세무회계 자체 장부, `ref/계정별원장/`)를 파싱해 여러 차례 시안 Artifact로 검증하며 다듬어졌다. 상세 배경은 `수정계획.md`의 "[모듈 D/reports] 거래처용 결산·채권현황 보고서" 절 참조.

---

## ① 컬러 시스템 전체

```css
:root {
  --gr-primary:       #1F4E79;
  --gr-primary-dark:  #163A5A;
  --gr-navy:          #0A192F;
  --gr-gold:          #C9A227;

  --gr-success:       #1E8E5A;
  --gr-warning:       #B4791F;
  --gr-danger:        #C6414E;

  --gr-dark:          #191F28;
  --gr-muted:         #6B7686;
  --gr-border:        #DFE3EA;
  --gr-light:         #F6F7FA;
  --gr-page-bg:       #E7EAF0;
  --gr-white:         #ffffff;

  --gr-gradient: linear-gradient(155deg, #0A192F 0%, #1F4E79 55%, #2C6597 100%);
}
```

| 컬러 | 용도 | 금지 |
|------|------|------|
| `--gr-primary` | 섹션 제목 밑줄, 표 tfoot 강조, 아이콘 배경 | 대량 본문 텍스트 |
| `--gr-primary-dark` | 섹션/서브섹션 제목 텍스트, KPI 배너 외 강조 값 | 배경색 |
| `--gr-navy` | KPI 배너 배경, 앱 풋터 배경, 표 헤더(`th`) 배경, 그라디언트 시작점 | 본문 텍스트 컬러 |
| `--gr-gold` | 헤더 내 브랜드명·문서 라벨의 `.co-name`만 | 흰 배경 위 사용 |
| `--gr-success` | 표의 감소(`td.neg`), 좋은 방향 서브텍스트(`.sub.down`), 연령분석 "1개월 이내" | 경고 의미 |
| `--gr-danger` | 표의 증가(`td.pos`), 나쁜 방향 서브텍스트(`.sub.up`), 연령분석 "6개월~1년", 콜아웃 스트립 | 단순 강조 목적 남용 |
| `--gr-warning` | 연령분석 "3~6개월", 경고 박스 | success/danger 대체 |

**v1과의 컬러 매핑 차이 주의**: v1의 `--gb-primary`(#2563EB)보다 이 스킬의 `--gr-primary`(#1F4E79)가 더 어둡다. 두 스킬의 색상 변수를 섞어 쓰면 톤이 깨지므로, 한 문서에는 반드시 한쪽 접두사(`.gb-*` 또는 `.gr-*`)와 그에 대응하는 CSS 파일만 사용한다.

---

## ② 타이포그래피

v1과 동일한 폰트 스택 사용:

```html
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap" rel="stylesheet">
```

| 요소 | 크기 | 굵기 |
|------|------|------|
| 문서 제목(`.gr-doc-header h1`) | 22px | 700 |
| 섹션 제목(`.gr-section-title`) | 14.5px | 700 |
| 서브섹션 제목(`.gr-subsection-title`) | 12px | 700 |
| 표 본문(`.gr-table`) | 12px | 400 |
| KPI 배너 메인 값(`.gr-kpi-main .val`) | 26px | 700 |
| KPI 배너 보조 값(`.gr-kpi-item .val`) | 16px | 700 |
| 비율 카드 값(`.gr-ratio-card .r-value`) | 22px | 700 |
| 각주(`.gr-note`) | 11px | 400 |
| 고지문(`.gr-disclaimer`) | 10.5px | 400 |

숫자가 세로로 정렬되는 모든 곳(표 금액 열, KPI 값, 비율 값)은 `font-variant-numeric: tabular-nums`가 이미 적용되어 있다 — 별도 클래스 불필요, `.num`/`.val`/`.r-value` 클래스를 쓰면 자동 적용.

---

## ③ 스페이싱 · 모서리 · 그림자

```css
--gr-radius:     8px;    /* 카드, 박스 */
--gr-radius-lg:  12px;   /* KPI 배너 */
--gr-radius-doc: 3px;    /* 문서 카드 외곽(A4 느낌) */

--gr-shadow:        0 8px 40px rgba(10,25,47,0.16), 0 0 0 1px rgba(10,25,47,0.05);
--gr-shadow-header:  0 2px 12px rgba(10,25,47,0.4);
```

- 문서 카드(`.gr-document`) 좌우 여백: 40px / 상하: 30px 36px
- 섹션 간 간격: 40px (`.gr-section { margin-bottom: 40px; }`)
- KPI 배너 하단 여백: 34px

---

## ④ 표(테이블) 설계 원칙 — 이 스킬의 핵심

이 스킬을 쓰는 문서 대부분은 "당기 vs 전기" 비교표가 반복된다. 아래 규칙을 **모든 비교표에 예외 없이 적용**한다(2026-07-13 대화에서 사용자가 명시적으로 확정한 규칙):

1. **컬럼 순서 고정**: `당기 → 전기(동기간) → [선택: 추가 참조 기간] → 증감액 → 증감율`. "당기말/전기말"을 쓰는 재무상태표류도 당기가 항상 먼저.
2. **참조 열 추가 가능**: 계절성이 있는 업종(세무회계업 등 상반기 쏠림)은 "전기(12월)" 같은 전기 연간 누계 열을 전기 동기간과 증감 사이에 추가해 계절성을 드러낼 수 있다. 단, 이 열은 증감액/증감율 계산에 관여하지 않는 순수 참조용이다.
3. **셀 색상은 순수 방향**: `td.pos`=빨강(증가), `td.neg`=초록(감소). 이것은 "좋다/나쁘다"가 아니라 순수 증감 방향이다 — 매출 증가와 비용 증가가 같은 빨강으로 표시되는 것이 맞다(직관적 방향 표시 우선, 해석은 독자가 문맥으로 판단).
4. **합계/소계는 tfoot**: `.gr-table tfoot`이 자동으로 파란 배경 강조 처리된다.
5. **0/신규/부호전환 표기**: 값이 0→0이면 "0.0%", 0→양수면 "신규", 양수→음수(부호 반전)면 "부호전환"으로 표기(무한대나 이상한 퍼센트 방지).
6. **산출 불가 값**: "-"로 표기하고, 필요시 `.gr-note`로 사유를 각주에 남긴다.

---

## ⑤ 채권 연령분석 컴포넌트 상세

`.gr-aging-bar-track`은 flex 자식 요소(`.gr-aging-bar-seg`)의 `width: N%`로 구간 비중을 표현한다. 구간이 5개보다 적거나 많아도(예: "1년 초과"가 0원이라 생략) 구조는 동일하게 유지하되, 범례(`.gr-aging-bar-legend`)에는 0원 구간도 표기해 "그 구간은 없다"는 사실 자체를 보여준다.

색상 시멘틱 클래스(고정 순서, 커스텀 배색 금지):

```
gr-age-fresh   (초록, --gr-success)  1개월 이내
gr-age-recent  (블루, --gr-primary)  1~3개월
gr-age-aging   (골드/warning)        3~6개월
gr-age-overdue (빨강, --gr-danger)   6개월~1년
gr-age-stale   (회색 #9CA3AF)        1년 초과
```

거래처별 상세 매트릭스 표(연령 구간 × 거래처)에서 위험 구간(3~6개월 이상)에 해당하는 셀은 `style="color:var(--gr-warning);font-weight:600;"` 또는 danger로 인라인 강조해 시선을 끈다.

---

## ⑥ 전체 문서 HTML 템플릿

```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>문서 제목 — 금바다세무회계</title>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="geumbada-report-style.css">
</head>
<body>

<div class="gr-page">
  <header class="gr-app-header">
    <div class="gr-brand">
      <img src="geumbada-logo.png" alt="금바다세무회계">
      <div class="gr-brand-text">
        <span class="gr-brand-name">금바다세무회계</span>
        <span class="gr-brand-sub">세금은 정확하게 · 상담은 친절하게</span>
      </div>
    </div>
    <span class="gr-header-badge">경영분석 보고서</span>
  </header>

  <main class="gr-main">
    <div class="gr-document">
      <div class="gr-doc-header">
        <div class="gr-doc-label">
          <img src="geumbada-logo.png" alt="로고">
          <span class="co-name">금바다세무회계</span> 경영분석 보고서
        </div>
        <h1>결산 · 채권현황 보고서</h1>
        <div class="gr-doc-meta">
          <span>📋 거래처 : ○○○</span>
          <span>📅 작성일 : 2026.07.13</span>
          <span>📂 보고기간 : 2025년 결산 ~ 2026.07.13</span>
        </div>
      </div>

      <div class="gr-content">
        <div class="gr-kpi-banner"> ... </div>
        <div class="gr-section"> ... </div>
      </div>

      <div class="gr-disclaimer">
        <p>본 보고서는 회계 기장 데이터를 기초로 작성되었으며, 세무·회계 자문 목적으로는 사용할 수 없습니다.</p>
        <div class="gr-disclaimer-firm">
          <img src="geumbada-logo.png" alt="로고">
          금바다세무회계
        </div>
      </div>
    </div>
  </main>

  <footer class="gr-app-footer">
    <div class="gr-footer-brand">
      <img src="geumbada-logo.png" alt="금바다세무회계"> 금바다세무회계
    </div>
    <div class="gr-footer-contacts">
      <span>📞 055-327-1010</span>
      <span>✉️ potamia49@gmail.com</span>
    </div>
    <span class="gr-footer-copy">© 2026 금바다세무회계</span>
  </footer>
</div>

</body>
</html>
```

---

## ⑦ Word/HWPX 대응 기준 (v1과 동일 값 승계)

| 항목 | HTML 값 | Word/HWPX 적용값 |
|------|---------|-----------------|
| 본문 폰트 | Noto Sans KR 12px | 맑은 고딕 10pt |
| 섹션 제목 | 14.5px / Bold | 맑은 고딕 11pt / 굵게 |
| 문서 제목 | 22px / Bold | 맑은 고딕 17pt / 굵게 |
| 표 헤더 배경 | `#0A192F`(네이비) | RGB(10,25,47) |
| 표 tfoot 배경 | `#EEF3FA` | RGB(238,243,250) |
| 헤더 배경 | 그라디언트 | RGB(31,78,121) 단색 대체 |

---

*금바다세무회계 재무 보고서 스타일 시스템 v2.0 — 내부 전용 · 2026-07-13*
