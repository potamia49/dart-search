"""감사의견(적정/한정/부적정/의견거절) 추출.

상세개발계획.md §4-4. M3에서 구현 (dart-parser 에이전트 영역). 이 모듈은
디렉터리 구조 스캐폴딩 목적의 골격만 둔다.
"""

from __future__ import annotations

AUDIT_OPINION_VALUES: tuple[str, ...] = ("적정", "한정", "부적정", "의견거절")


def extract_audit_opinion(raw_text: str) -> str | None:
    """감사보고서 원문 텍스트에서 감사의견을 추출.

    M1 시점에는 미구현. 호출 시 NotImplementedError.
    """
    raise NotImplementedError("audit_opinion은 M3에서 구현 예정 (dart-parser 에이전트 영역)")
