"""보고서에 인쇄되는 세무회계사무소 소개 문구 (사용자가 직접 고치는 상수).

`tamplate/audit_proposal_template.html`의 `EMBEDDED_DATA.firm`에 그대로 들어간다.
API 키 같은 비밀값이 아니라 **인쇄물에 찍히는 홍보 문구**라 `.env`가 아니라 여기서
관리한다(CLAUDE.md의 "API 키는 .env" 규칙은 자격증명에 대한 것이다).

문구만 바꾸고 싶으면 **이 파일의 문자열만** 수정하면 된다 — 다른 코드는 손댈
필요가 없다. 키 이름(name/manager/contact/feeNote/pitch)은 템플릿이 그대로 읽으므로
바꾸지 말 것.
"""

from __future__ import annotations

# 아직 실제 연락처를 채우지 않았음을 알아보기 위한 표식(2026-08-03, dart-qa 리뷰 반영).
# 이 접두어로 시작하는 `contact`는 "미설정"으로 보고 보고서 생성 시 경고를 남긴다 —
# 자리표시자 문구가 그대로 고객 발송 문서에 인쇄되는 것을 사용자가 모르고 지나치지
# 않게 하기 위함이다(생성 자체를 막지는 않는다).
CONTACT_PLACEHOLDER_PREFIX = "연락처 미입력"

FIRM_PROFILE: dict[str, str] = {
    # 사무소명 (표지 상단 + 마지막 장 제안 박스 제목에 쓰인다)
    "name": "금바다세무회계",
    # 담당자 표기 (마지막 장 연락처 줄 앞부분)
    "manager": "담당 공인회계사 윤일근",
    # 연락처 한 줄 — 실제 값으로 교체할 것 (예: "T. 055-000-0000  ·  E. name@example.com")
    "contact": "연락처 미입력 — backend/app/reports/firm_profile.py에서 수정하세요",
    # 수임료 안내
    "feeNote": "본 견적은 회사 규모 및 감사범위 협의 후 별도로 제안드립니다.",
    # 사무소 소개 문단
    "pitch": (
        "중소기업 외부감사와 세무 자문 경험을 바탕으로, 회사의 재무 현황을 "
        "먼저 진단하고 감사 범위와 일정을 함께 설계해 드립니다."
    ),
}


def is_placeholder_contact(contact: str | None) -> bool:
    """연락처가 비었거나 위 자리표시자 문구 그대로인지."""
    text = (contact or "").strip()
    return not text or text.startswith(CONTACT_PLACEHOLDER_PREFIX)
