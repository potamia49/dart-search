"""지역/업종/매출액 필터 로직.

상세개발계획.md §4-1 ~ §4-3. 시도 약칭 매핑 테이블은 정적 데이터라 M1에서
미리 정의해 두고, 실제 필터링 로직(회사명 매칭/컬럼 매칭 등)은 M2에서
`corp_profiles`/금융위 API 연동과 함께 구현한다.
"""

# "경상남도/경남" 등 표기 편차 대응 (상세개발계획.md §4-1 "필터 로직")
# key: 정규화된 표준 시도명, value: 사용자 입력/원문에서 등장할 수 있는 표기 변형들
SIDO_ALIASES: dict[str, list[str]] = {
    "서울특별시": ["서울", "서울시", "서울특별시"],
    "부산광역시": ["부산", "부산시", "부산광역시"],
    "대구광역시": ["대구", "대구시", "대구광역시"],
    "인천광역시": ["인천", "인천시", "인천광역시"],
    "광주광역시": ["광주", "광주시", "광주광역시"],
    "대전광역시": ["대전", "대전시", "대전광역시"],
    "울산광역시": ["울산", "울산시", "울산광역시"],
    "세종특별자치시": ["세종", "세종시", "세종특별자치시"],
    "경기도": ["경기", "경기도"],
    "강원특별자치도": ["강원", "강원도", "강원특별자치도"],
    "충청북도": ["충북", "충청북도"],
    "충청남도": ["충남", "충청남도"],
    "전북특별자치도": ["전북", "전라북도", "전북특별자치도"],
    "전라남도": ["전남", "전라남도"],
    "경상북도": ["경북", "경상북도"],
    "경상남도": ["경남", "경상남도"],
    "제주특별자치도": ["제주", "제주도", "제주특별자치도"],
}


def normalize_sido(raw: str) -> str | None:
    """사용자 입력 시도명을 표준 시도명으로 정규화. 매칭 실패 시 None."""
    raw = raw.strip()
    for standard, aliases in SIDO_ALIASES.items():
        if raw in aliases:
            return standard
    return None


def normalize_corp_name(raw: str) -> str:
    """회사명 매칭 전 정규화: "(주)/주식회사" 제거, 공백/괄호 통일 (§4-1 대응 1)."""
    name = raw.strip()
    for token in ("주식회사", "(주)", "(유)", "유한회사"):
        name = name.replace(token, "")
    name = name.replace(" ", "").replace("(", "").replace(")", "")
    return name


def parse_address(address: str | None) -> tuple[str | None, str | None]:
    """기업개황 API의 `adres` 문자열에서 (표준 시도명, 시군구명)을 추출.

    DART 주소는 "전라북도 군산시 현충로 35 (나운동)"처럼 시도/시군구가 앞의
    두 토큰으로 오는 경우가 대부분이다. 시도 토큰을 `normalize_sido`로 표준화하지
    못하면(세종 등 시군구가 없는 경우 포함, 표기 편차 등) 시군구도 신뢰할 수
    없으므로 함께 None 처리한다. corp_profiles 적재(STEP 3, §4-1 대응 2) 시 사용.
    """
    if not address:
        return None, None
    tokens = address.strip().split()
    if not tokens:
        return None, None
    sido = normalize_sido(tokens[0])
    if sido is None:
        return None, None
    sigungu = tokens[1] if len(tokens) > 1 else None
    return sido, sigungu


def region_matches(
    sido: str | None, sigungu: str | None, cond_region: dict | None
) -> bool:
    """corp_profiles의 (sido, sigungu)가 Job.cond_region 조건과 일치하는지 판정.

    cond_region: {"sido": "경남", "sigungu": ["김해시", "양산시"]} 형태(§5).
    - cond_region이 비어 있으면(지역 조건 없음) 무조건 통과.
    - sido가 지정되었는데 프로필의 sido를 알 수 없거나 다르면 탈락.
    - sigungu 목록이 지정되었는데 프로필의 sigungu가 그 목록에 없으면 탈락
      (목록이 비어 있으면 해당 시도 전체 통과).
    """
    if not cond_region:
        return True
    cond_sido_raw = cond_region.get("sido")
    if cond_sido_raw:
        cond_sido = normalize_sido(cond_sido_raw) or cond_sido_raw
        if sido != cond_sido:
            return False
    cond_sigungu = cond_region.get("sigungu") or []
    if cond_sigungu and sigungu not in cond_sigungu:
        return False
    return True


def industry_matches(induty_code: str | None, cond_industry: list[str] | None) -> bool:
    """corp_profiles.induty_code가 Job.cond_industry(prefix 목록, §5) 중 하나로 시작하는지 판정.

    cond_industry가 비어 있으면(업종 조건 없음) 무조건 통과.
    """
    if not cond_industry:
        return True
    if not induty_code:
        return False
    return any(induty_code.startswith(prefix) for prefix in cond_industry)


def revenue_matches(revenue_cur: float | None, cond_revenue: dict | None) -> bool:
    """results.revenue_cur가 Job.cond_revenue(min_krw/max_krw, §5) 범위 안인지 판정.

    revenue_cur를 모르면(파싱 실패/재무제표 미첨부) 사후 필터를 적용할 수 없으므로
    무조건 통과시킨다 — 매출액 미상 건을 섣불리 제외하지 않는다(§4-3).
    """
    if revenue_cur is None:
        return True
    cond_revenue = cond_revenue or {}
    min_krw = cond_revenue.get("min_krw")
    max_krw = cond_revenue.get("max_krw")
    if min_krw is not None and revenue_cur < min_krw:
        return False
    if max_krw is not None and revenue_cur > max_krw:
        return False
    return True
