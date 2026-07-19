"""filters.py 단위 테스트 (상세개발계획.md §4-1 ~ §4-3)."""

from app.core.filters import (
    industry_matches,
    normalize_corp_name,
    normalize_sido,
    parse_address,
    region_matches,
    revenue_matches,
)


def test_normalize_sido_aliases():
    assert normalize_sido("경남") == "경상남도"
    assert normalize_sido("경상남도") == "경상남도"
    assert normalize_sido("서울") == "서울특별시"
    assert normalize_sido("서울시") == "서울특별시"


def test_normalize_sido_unknown_returns_none():
    assert normalize_sido("존재하지않는시도") is None


def test_normalize_corp_name_strips_company_markers():
    assert normalize_corp_name("주식회사 한글텍") == "한글텍"
    assert normalize_corp_name("(주)한글텍") == "한글텍"
    assert normalize_corp_name("유한회사 테스트 (지점)") == "테스트지점"


def test_parse_address_standard_form():
    # SIDO_ALIASES의 표준 시도명은 최신 행정구역명 기준이라 "전라북도" 입력도
    # "전북특별자치도"(키)로 정규화된다 — normalize_sido와 동일한 규칙.
    sido, sigungu = parse_address("전라북도 군산시 현충로 35 (나운동)")
    assert sido == "전북특별자치도"
    assert sigungu == "군산시"


def test_parse_address_accepts_alias_sido():
    sido, sigungu = parse_address("경남 양산시 어딘가로 1")
    assert sido == "경상남도"
    assert sigungu == "양산시"


def test_parse_address_unrecognized_sido_returns_none_none():
    sido, sigungu = parse_address("알수없는지역 어딘가 1")
    assert sido is None
    assert sigungu is None


def test_parse_address_empty_or_none():
    assert parse_address(None) == (None, None)
    assert parse_address("") == (None, None)
    assert parse_address("   ") == (None, None)


def test_region_matches_no_condition_passes_everything():
    assert region_matches("경상남도", "김해시", None) is True
    assert region_matches(None, None, {}) is True


def test_region_matches_sido_filter():
    cond = {"sido": "경남"}
    assert region_matches("경상남도", "김해시", cond) is True
    assert region_matches("경상북도", "포항시", cond) is False
    assert region_matches(None, None, cond) is False


def test_region_matches_sigungu_filter():
    cond = {"sido": "경남", "sigungu": ["김해시", "양산시"]}
    assert region_matches("경상남도", "김해시", cond) is True
    assert region_matches("경상남도", "창원시", cond) is False


def test_region_matches_sigungu_empty_list_means_whole_sido():
    cond = {"sido": "경남", "sigungu": []}
    assert region_matches("경상남도", "창원시", cond) is True


def test_region_matches_per_sido_sigungu_no_cross_collision():
    # 시도별 시군구(신형): 경남은 김해시로 제한, 부산은 전체 — "중구"가 시도 간 섞이지 않는다.
    cond = {
        "sido": ["경상남도", "부산광역시"],
        "sigungu_by_sido": {"경상남도": ["김해시"], "부산광역시": []},
    }
    assert region_matches("경상남도", "김해시", cond) is True
    assert region_matches("경상남도", "창원시", cond) is False
    assert region_matches("부산광역시", "중구", cond) is True
    assert region_matches("서울특별시", "중구", cond) is False


def test_industry_matches_prefix():
    assert industry_matches("C25110", ["C25", "C29"]) is True
    assert industry_matches("G46900", ["C25", "C29"]) is False


def test_industry_matches_no_condition_passes_everything():
    assert industry_matches("C25110", None) is True
    assert industry_matches("C25110", []) is True


def test_industry_matches_missing_code_fails_when_condition_set():
    assert industry_matches(None, ["C25"]) is False


def test_revenue_matches_range():
    cond = {"min_krw": 5_000, "max_krw": 50_000}
    assert revenue_matches(1_000, cond) is False  # 최소 미달
    assert revenue_matches(10_000, cond) is True  # 범위 내
    assert revenue_matches(100_000, cond) is False  # 최대 초과


def test_revenue_matches_unknown_revenue_always_passes():
    assert revenue_matches(None, {"min_krw": 5_000, "max_krw": 50_000}) is True


def test_revenue_matches_no_condition_passes_everything():
    assert revenue_matches(1, {}) is True
    assert revenue_matches(1, None) is True


def test_revenue_matches_one_sided_bounds():
    assert revenue_matches(1, {"min_krw": 100}) is False
    assert revenue_matches(1000, {"min_krw": 100}) is True
    assert revenue_matches(1000, {"max_krw": 100}) is False
    assert revenue_matches(1, {"max_krw": 100}) is True
