"""Job 생성/조회/취소/재시도 API.

상세개발계획.md §6, §8 (M2 범위):
    POST /api/jobs                     조건 입력 → Job 생성 + 백그라운드 실행 시작
    GET  /api/jobs                     Job 목록 (상태/진행률 포함)
    GET  /api/jobs/{id}                Job 상세 (프론트가 2초 간격 폴링)
    POST /api/jobs/{id}/cancel         실행 취소
    POST /api/jobs/{id}/resume         중단(쿼터/오류) Job 이어하기
    POST /api/jobs/{id}/retry-failed   파싱 실패 건만 재시도

M1에서는 스캐폴딩만 두고 구현하지 않는다. 파이프라인 오케스트레이션
(`app/core/pipeline.py`)이 준비되는 M2에서 이 라우터를 채우고 `main.py`에
등록한다.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# TODO(M2): pipeline.py 완성 후 엔드포인트 구현 + main.py에 라우터 등록
