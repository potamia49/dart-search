"""결과 조회/다운로드 API.

상세개발계획.md §6 (M2~M4 범위):
    GET /api/jobs/{id}/results                  결과 목록 (페이징, parse_status/제외 여부 필터)
    GET /api/jobs/{id}/export?format=xlsx|csv    결과 파일 다운로드

M1에서는 스캐폴딩만 두고 구현하지 않는다. `results` 테이블(app/models/result.py)과
`exporters/excel.py`가 준비되는 M2 후반~M4에서 채운다.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/jobs", tags=["results"])

# TODO(M2/M4): pipeline.py, exporters/excel.py 완성 후 엔드포인트 구현 + main.py에 라우터 등록
