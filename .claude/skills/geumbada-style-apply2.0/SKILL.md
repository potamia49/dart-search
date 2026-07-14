---
name: geumbada-style-apply2.0
description: >
  금바다세무회계 재무·경영분석 보고서 전용 스타일(geumbada-report-style.css + .gr-* 클래스)을
  HTML 문서에 적용하거나 처음부터 생성하는 스킬. KPI 배너·비율 카드·당기/전기 비교표·
  채권 연령분석 막대바처럼 숫자 밀도가 높은 데이터 보고서에 특화.
  일반 질의응답서·검토의견서·공문에는 geumbada-style-apply(v1)를 사용할 것.
---

# 금바다세무회계 재무 보고서 스타일 적용 스킬 (v2.0)

## v1과의 차이 — 언제 이 스킬을 쓰나

| 구분 | geumbada-style-apply (v1) | geumbada-style-apply2.0 (이 스킬) |
|------|---------------------------|-----------------------------------|
| 용도 | 질의응답서·세무 검토의견서·공문·일반 경영분석 서술형 보고서 | **결산·채권현황 보고서, 경영분석 리포트 등 숫자·표 중심 재무 보고서** |
| 핵심 컴포넌트 | Q/A 답변 블록, 법령 카드, 결론 박스 | **KPI 배너, 비율 카드, 당기/전기/증감/증감율 비교표, 채권 연령분석 막대바** |
| 팔레트 | Geumbada Blue `#2563EB` + Gold `#FCD34D` (밝고 액션 지향) | Primary Blue `#1F4E79` + Navy `#0A192F` + Gold `#C9A227` (차분하고 문서-지향) |
| 클래스 프리픽스 | `.gb-*` | `.gr-*` |
| CSS 파일 | `assets/geumbada-style.css` | `assets/geumbada-report-style.css` |

트리거 — 다음 요청에는 반드시 이 스킬(v2.0)을 사용:
- "금바다 보고서 스타일로", "재무 보고서 서식으로", "결산 보고서 형식으로"
- 계정별원장·부속명세서 등 회계 데이터를 파싱해 만드는 **거래처용 결산·채권현황·경영분석 보고서**
- 당기 vs 전기 비교표, 손익/재무상태 스냅샷, 비율분석(영업이익률·회전율 등), 채권 연령분석이 들어가는 문서

숫자 표 없이 서술형 텍스트가 중심인 문서(질의응답서·공문 등)는 v1(`geumbada-style-apply`)을 사용한다.

## 사무소 정보 (단일 원천 — v1과 동일)

```
사무소명: 금바다세무회계
대표공인회계사: 윤일근
사업자등록번호: 747-11-02122
전화: 055-327-1010
팩스: 055-312-1011
이메일: potamia49@gmail.com
주소: 경남 김해시 계동로 241, 701호 (대청동, 리더스빌딩)
저작권: © 2026 금바다세무회계
```

## 1단계 — CSS·로고 삽입 방식 (항상 인라인 임베드)

v1과 동일하게 **항상 인라인 임베드**한다. HTML 파일 하나만으로 완전히 독립 실행되어야 한다.

- **CSS**: `assets/geumbada-report-style.css` 내용 전문을 `<style>` 태그 안에 그대로 붙여넣는다.
- **로고**: `assets/geumbada-logo.png`를 base64로 변환해 `<img src="data:image/png;base64,...">`로 삽입한다.

## 2단계 — 문서 골격

```html
<div class="gr-page">

  <!-- ① 앱 헤더 -->
  <header class="gr-app-header">
    <div class="gr-brand">
      <img src="data:image/png;base64,..." alt="금바다세무회계">
      <div class="gr-brand-text">
        <span class="gr-brand-name">금바다세무회계</span>
        <span class="gr-brand-sub">세금은 정확하게 · 상담은 친절하게</span>
      </div>
    </div>
    <span class="gr-header-badge">【문서유형】</span>
  </header>

  <!-- ② 본문 -->
  <main class="gr-main">

    <!-- 검토용 시안 배너 (실제 발행본에서는 생략) -->
    <div class="gr-page-note">⚠️ <span><b>설계 검토용 시안입니다.</b> 【데이터 출처 설명】</span></div>

    <div class="gr-document">

      <!-- 문서 헤더 (파란 그라디언트) -->
      <div class="gr-doc-header">
        <div class="gr-doc-label">
          <img src="data:image/png;base64,..." alt="로고">
          <span class="co-name">금바다세무회계</span> 【레이블 텍스트】
        </div>
        <h1>【문서 제목】</h1>
        <div class="gr-doc-meta">
          <span>📋 거래처 : 【고객명】</span>
          <span>📅 작성일 : 【날짜】</span>
          <span>📂 보고기간 : 【기간】</span>
          <span>✍️ 담당 : 회계사 윤일근</span>
        </div>
      </div>

      <div class="gr-content">
        【KPI 배너 + 섹션들 — 3단계 참조】
      </div>

      <!-- 고지문 -->
      <div class="gr-disclaimer">
        <p>【고지문 문구 — 4단계 참조】</p>
        <div class="gr-disclaimer-firm">
          <img src="data:image/png;base64,..." alt="로고">
          금바다세무회계
        </div>
      </div>

    </div><!-- /gr-document -->
  </main>

  <!-- ③ 앱 풋터 -->
  <footer class="gr-app-footer">
    <div class="gr-footer-brand">
      <img src="data:image/png;base64,..." alt="금바다세무회계"> 금바다세무회계
      <div class="gr-footer-brand-sub">대표 공인회계사 윤일근 · 사업자등록번호 747-11-02122<br>경남 김해시 계동로 241, 701호 (대청동, 리더스빌딩)</div>
    </div>
    <div class="gr-footer-contacts">
      <span>📞 055-327-1010</span>
      <span>📠 055-312-1011</span>
      <span>✉️ potamia49@gmail.com</span>
      <span>🕒 평일 09:00~18:00</span>
    </div>
    <span class="gr-footer-copy">© 2026 금바다세무회계. All rights reserved.</span>
  </footer>

</div><!-- /gr-page -->
```

**문서 유형별 레이블 텍스트** (`.gr-header-badge`, `.gr-doc-label`에 사용)

| 문서 유형 | 레이블 텍스트 |
|----------|-------------|
| 결산·채권현황 보고서 | `경영분석 보고서` |
| 월간/분기 결산 보고서 | `결산 보고서` |
| 경영분석 리포트 | `경영분석 보고서` |

## 3단계 — 본문 컴포넌트 조립

### KPI 배너 (문서 최상단, 핵심 지표 5개 요약)

```html
<div class="gr-kpi-banner">
  <div class="gr-kpi-main">
    <div class="lbl">당기순이익 (기간 비교)</div>
    <div class="val">77,760,768원</div>
    <div class="sub down">▼ 4.7% · 전기 동기간 81,611,792원</div>
  </div>
  <div class="gr-kpi-item">
    <div class="lbl">매출</div>
    <div class="val">126,540,922</div>
    <div class="sub down">▼ 11.7%</div>
  </div>
  <!-- kpi-item 3개 더 (비용, 채권 잔액, 3개월 초과 채권 등 핵심 지표) -->
</div>
```

`.sub.up`(빨강, 위험/증가 강조) / `.sub.down`(초록, 긍정/감소 강조) — **매출 감소·비용 증가처럼 나쁜 방향은 up(빨강), 좋은 방향은 down(초록)** 클래스로 매핑한다(단순 증감이 아니라 "재무적으로 좋은/나쁜 방향" 기준).

### 섹션 + 비교표 (당기/전기/증감/증감율 — 4열 규격)

```html
<div class="gr-section">
  <div class="gr-section-title"><span class="gr-section-num">1</span>손익 현황 — 당기(…) vs 전기 동기간(…)</div>

  <div class="gr-subsection-title">매출</div>
  <div class="gr-table-wrap">
    <table class="gr-table">
      <thead><tr><th>계정과목</th><th class="num">당기</th><th class="num">전기 동기간</th><th class="num">증감액</th><th class="num">증감율</th></tr></thead>
      <tbody>
        <tr><td>항목명</td><td class="num">16,630,000</td><td class="num">14,700,000</td><td class="num pos">+1,930,000</td><td class="num pct">+13.1%</td></tr>
      </tbody>
      <tfoot><tr><td>합계</td><td class="num">…</td><td class="num">…</td><td class="num">…</td><td class="num">…</td></tr></tfoot>
    </table>
  </div>
</div>
```

**표 컬럼 순서 원칙(고정)**: `당기 → 전기(동기간) → (선택: 전기 12월 등 추가 참조열) → 증감액 → 증감율`. 재무상태 스냅샷처럼 "당기말/전기말"을 쓰는 경우도 당기가 항상 먼저 온다.

**셀 색상 규칙**: `td.pos`(빨강 `--gr-danger`)=증가, `td.neg`(초록 `--gr-success`)=감소 — 이는 순수 방향(+/-)이며 좋고 나쁨의 의미가 아니다. 재무적으로 좋은/나쁜 방향 강조가 필요하면 KPI 배너의 `.sub.up`/`.sub.down` 규칙을 따로 쓴다.

### 비율 카드 (영업이익률·회전율 등)

```html
<div class="gr-ratio-grid">
  <div class="gr-ratio-card">
    <div class="r-label">영업이익률</div>
    <div class="r-value">61.5%</div>
    <div class="r-sub up">▲ 4.7%p · 전기 56.8%</div>
  </div>
  <!-- 산출 불가 항목은 r-value에 muted 클래스 + "해당없음" -->
  <div class="gr-ratio-card">
    <div class="r-label">매출총이익률</div>
    <div class="r-value muted">해당없음</div>
    <div class="r-sub">매출원가 계정 없음(서비스업)</div>
  </div>
</div>
```

### 채권 연령분석 막대바

```html
<div class="gr-aging-bar-track">
  <div class="gr-aging-bar-seg gr-age-fresh"   style="width:28.8%;">1개월 이내 28.8%</div>
  <div class="gr-aging-bar-seg gr-age-recent"  style="width:32.5%;">1~3개월 32.5%</div>
  <div class="gr-aging-bar-seg gr-age-aging"   style="width:30.9%;">3~6개월 30.9%</div>
  <div class="gr-aging-bar-seg gr-age-overdue" style="width:7.8%;">6~1년 7.8%</div>
</div>
<div class="gr-aging-bar-legend">
  <span><span class="dot gr-age-fresh"></span>1개월 이내 · 9,593,000</span>
  <!-- … -->
</div>
```

구간 색상은 `gr-age-fresh`(초록, 신선)→`gr-age-recent`(블루)→`gr-age-aging`(골드/경고)→`gr-age-overdue`(빨강/위험)→`gr-age-stale`(회색, 1년 초과) 순으로 **오래될수록 위험색**이 진해지도록 고정한다. 임의로 다른 색을 섞지 않는다.

### 강조 콜아웃 (위험 하이라이트)

```html
<div class="gr-callout-strip">
  <div class="big">12,898,000원</div>
  <div class="txt"><b>3개월 초과 채권</b>이 전체의 <b>38.7%</b>를 차지합니다. …</div>
</div>
```

### 팁 박스 / 경고 박스 (참고 설명, 데이터 한계 고지)

```html
<div class="gr-tip-box">
  <div class="gr-tip-title">💡 【제목】</div>
  <p class="gr-note" style="margin-top:0;">【설명】</p>
</div>

<div class="gr-warning-box">⚠️ 【재무상태 스냅샷의 결산 전 잠정치 안내 등】</div>
```

## 4단계 — 고지문 문구 (문서 유형별)

| 문서 유형 | 고지문 내용 |
|----------|------------|
| 결산·채권현황 보고서 / 경영분석 보고서 | 본 보고서는 회계 기장 데이터를 기초로 작성되었으며, 세무·회계 자문 목적으로는 사용할 수 없습니다. 결산 정리분개 반영 전 잠정 수치이며, 정확한 재무제표는 결산 완료 후 별도 안내드립니다. |

## 5단계 — 저장 위치 및 파일명 (매번 확인)

v1과 동일하게 저장 위치·파일명을 고정하지 않는다. 문서 생성 전 사용자에게 반드시 확인한다. 기본 제안 형식: `【문서유형】_【거래처또는주제】_YYYY-MM-DD.html`.

## 완성 전 체크리스트

- [ ] CSS(`geumbada-report-style.css` 전문)·로고 모두 인라인 임베드
- [ ] Google Fonts `<link>` 포함
- [ ] `.gr-doc-label` 안에 `class="co-name"`으로 "금바다세무회계" 골드 처리
- [ ] 모든 비교표가 "당기 → 전기 → 증감 → 증감율" 컬럼 순서 준수
- [ ] KPI 배너 최상단 배치, 핵심 지표 4~5개로 제한(과밀 금지)
- [ ] 채권 연령분석이 있다면 막대바 구간 색상이 fresh→stale 순서 고정 준수
- [ ] 산출 불가 비율은 0이나 빈칸이 아니라 "해당없음" + 사유로 명시
- [ ] 재무상태 스냅샷 등 결산 전 잠정치에는 `.gr-warning-box`로 한계 고지
- [ ] 고지문(`gr-disclaimer`)에 데이터 기준 시점·용도 제한 명시
- [ ] 앱 풋터 연락처 정보 실제값 확인

## Anti-patterns

1. `--gr-gold`(#C9A227)를 흰 배경 위에 사용하지 말 것 — 그라디언트 헤더 내부(`.gr-doc-label .co-name`, `.gr-brand-name`) 전용.
2. 그라디언트(`--gr-gradient`)를 앱 헤더·문서 헤더 외 영역에 쓰지 말 것.
3. 비교표 컬럼 순서를 임의로 바꾸지 말 것(당기가 항상 먼저).
4. 연령분석 막대바 색상을 fresh→stale 시멘틱 순서 없이 임의 배색하지 말 것.
5. v1(`.gb-*`)과 이 스킬(`.gr-*`) 클래스를 한 문서에 섞어 쓰지 말 것 — 문서 유형에 맞는 스킬 하나만 선택.
6. KPI 배너에 6개 이상 지표를 욱여넣지 말 것 — 가장 중요한 4~5개만.
7. `td.pos`/`td.neg`(순수 증감 방향)와 `.sub.up`/`.sub.down`(재무적 호/악재 방향)의 의미를 혼동해 쓰지 말 것.

## 상세 레퍼런스

전체 컴포넌트 CSS·설계 배경(왜 v1과 팔레트를 분리했는지, 실제 시안 검증 과정)은 `references/style-guide.md` 참조.

## Bundled Assets

- `assets/geumbada-report-style.css` — 공식 CSS 파일 전문 (인라인 임베드용)
- `assets/geumbada-logo.png` — 공식 로고 PNG (v1과 동일 파일, base64 임베드용)
