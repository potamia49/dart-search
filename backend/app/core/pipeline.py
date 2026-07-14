"""수집 파이프라인 오케스트레이션 (Job 실행).

상세개발계획.md §4 STEP 0~6을 구현하는 곳. M2에서 착수.

각 STEP은 `jobs.current_step`/`progress_done`/`progress_total`을 DB에
체크포인트로 남겨 중단 후 이어하기(resume)가 가능해야 한다
(CLAUDE.md 핵심 제약 5번). `dart_client.QuotaExceededError` 발생 시
Job.status를 PAUSED_QUOTA로 전환하고 그 시점까지의 진행 상태를 보존한다
(CLAUDE.md 핵심 제약 4번).

| STEP | 내용 | 사용 API |
|---|---|---|
| 0 | 조건 입력 검증, Job 생성 | - |
| 1 | corp_cache 확인/갱신 | corpCode.xml (app/core/corp_cache.py) |
| 2 | 외부감사관련(pblntf_ty=F) 공시 목록 페이징 수집 | list.json |
| 3 | 지역 사전 추림 + 기업개황 확정 + corp_profiles 캐시 적재 | 금융위 API 또는 company.json |
| 4 | 감사보고서 원본 다운로드 (zip 해제, 형식 판별) | document.xml |
| 5 | 재무제표 파싱(당기/전기 13항목) + 감사의견 추출 | - (app/parsers) |
| 6 | 매출액 범위 사후 필터 | - |

M1에서는 아직 구현하지 않는다.
"""

# TODO(M2): STEP 1~4 오케스트레이션 구현
# TODO(M3): STEP 5 파싱 연동
# TODO(M2): STEP 6 매출액 필터 연동
