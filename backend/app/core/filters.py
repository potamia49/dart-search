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


def cond_sido_list(cond_region: dict | None) -> list[str]:
    """cond_region의 sido를 정규화된 시도명 리스트로 반환한다.

    시도는 다중 선택(리스트)이 기본이지만, 하위호환을 위해 구 단일 선택(문자열)
    형태(`{"sido": "경남"}`)도 1개짜리 리스트로 흡수한다 — 이미 저장된 Job의
    cond_region JSON과 문자열을 넘기는 기존 테스트가 그대로 동작하도록 하기 위함.
    None/빈 값이면 빈 리스트(지역 조건 없음)를 반환한다.
    """
    if not cond_region:
        return []
    raw = cond_region.get("sido")
    if not raw:
        return []
    values = [raw] if isinstance(raw, str) else list(raw)
    result: list[str] = []
    for v in values:
        if v:
            result.append(normalize_sido(v) or v)
    return result


def cond_region_sigungu_map(cond_region: dict | None) -> dict[str, list[str]] | None:
    """cond_region을 {정규화된 시도: [시군구...]} 매핑으로 변환한다.

    지역 조건이 없으면 None(전체 통과)을 반환한다. 시군구 목록이 비어 있으면
    해당 시도 전체를 의미한다. 지원하는 입력 형태:
      - 신형(시도별 시군구): {"sido": ["경상남도", "부산광역시"],
        "sigungu_by_sido": {"경상남도": ["김해시"], "부산광역시": []}}
      - 구형 평면(시도 1개): {"sido": "경남", "sigungu": ["김해시"]}
        또는 {"sido": ["경남"], "sigungu": ["김해시"]} — 그 유일한 시도에 매핑.
    시군구가 시도별로 그룹화되므로 "중구"처럼 시도 간 시군구명이 충돌하지 않는다.
    """
    if not cond_region:
        return None
    sidos = cond_sido_list(cond_region)
    if not sidos:
        return None
    result: dict[str, list[str]] = {s: [] for s in sidos}
    sbs = cond_region.get("sigungu_by_sido")
    if isinstance(sbs, dict) and sbs:
        for key, values in sbs.items():
            norm_key = normalize_sido(key) or key
            if norm_key in result:
                result[norm_key] = list(values or [])
    else:
        flat = cond_region.get("sigungu") or []
        if flat and len(sidos) == 1:
            result[sidos[0]] = list(flat)
    return result


def region_matches(
    sido: str | None, sigungu: str | None, cond_region: dict | None
) -> bool:
    """corp_profiles의 (sido, sigungu)가 Job.cond_region 조건과 일치하는지 판정.

    cond_region은 시도 다중 선택 + 시도별 시군구(§5, `cond_region_sigungu_map`
    참조)이며 구 평면 형태도 하위호환으로 받아들인다.
    - cond_region이 비어 있으면(지역 조건 없음) 무조건 통과.
    - 프로필의 sido가 선택된 시도 목록에 없으면 탈락.
    - 그 시도에 시군구 목록이 지정됐는데 프로필의 sigungu가 목록에 없으면 탈락
      (목록이 비어 있으면 해당 시도 전체 통과).
    """
    region_map = cond_region_sigungu_map(cond_region)
    if not region_map:
        return True
    if sido not in region_map:
        return False
    allowed = region_map[sido]
    if allowed and sigungu not in allowed:
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
