"""M8 1단계 — DART 기업개황 인덱스 크롤러 단위 테스트. 상세개발계획.md §4-10.

실제 네트워크는 타지 않는다(실측 검증은 §4-10-A에 기록된 스파이크가 담당).
여기서는 **위치 결합의 정합성 검증**과 업종 코드 역매핑에 집중한다 —
위치 결합은 순서가 어긋나면 전 행이 조용히 오염되므로 이 프로젝트에서
가장 조용히 망가지기 쉬운 지점이다.
"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.core.dart_corp_index import (
    DartIndexCrawlError,
    build_induty_code_lookup,
    filter_local_candidates,
    find_ambiguous_corp_codes,
    merge_by_position,
    mid_level_codes,
    parse_industry_excel,
    parse_search_page,
    reconcile_ambiguous_rows,
    upsert_dart_corp_index,
)
from app.core.filters import parse_address

# 실측 트리 구조를 축약한 것 (§4-10-A: 대분류는 ROOT####, 나머지는 숫자 코드)
TREE = [
    {"PARENT": "#", "TEXT": "전체", "ID": "all"},
    {"PARENT": "all", "TEXT": "제조업", "ID": "ROOT1034"},
    {"PARENT": "ROOT1034", "TEXT": "식료품 제조업", "ID": "10"},
    {"PARENT": "10", "TEXT": "도축, 육류 가공 및 저장 처리업", "ID": "101"},
    {"PARENT": "101", "TEXT": "육류 가공 및 저장 처리업", "ID": "1012"},
    {"PARENT": "1012", "TEXT": "육류 통조림 및 유사 저장식품 제조업", "ID": "10121"},
    {"PARENT": "ROOT1034", "TEXT": "음료 제조업", "ID": "11"},
]


def _excel_bytes(rows: list[list[str | None]]) -> bytes:
    """`downloadExcel.do` 응답과 같은 14열 xlsx를 만든다."""
    wb = Workbook()
    sheet = wb.active
    sheet.append(
        [
            "회사이름", "영문명", "공시회사명", "종목코드", "대표자명", "법인구분",
            "법인등록번호", "사업자등록번호", "주소", "홈페이지", "IR홈페이지",
            "업종명", "설립일", "결산월",
        ]
    )
    for row in rows:
        sheet.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _row(name: str, *, jurir: str = "110111-1234567", address: str = "경상남도 김해시 삼안로 1",
         induty: str = "육류 통조림 및 유사 저장식품 제조업", stock: str | None = None) -> list:
    return [
        name, "ENG", name, stock, "홍길동", "기타법인", jurir, "123-45-67890",
        address, None, None, induty, "2001-01-01", "12월",
    ]


# ---------------------------------------------------------------------------
# 업종 트리
# ---------------------------------------------------------------------------


def test_mid_level_codes_picks_two_digit_numeric_only():
    assert mid_level_codes(TREE) == ["10", "11"]


def test_build_induty_code_lookup_covers_all_depths():
    """회사별 부여 깊이가 2~5자리로 갈리므로 전 레벨이 역매핑돼야 한다."""
    lookup = build_induty_code_lookup(TREE)
    assert lookup["식료품제조업"] == "10"
    # 쉼표는 정규화에서 제거하지 않는다 — 트리와 엑셀 양쪽에 동일하게 존재해
    # 그대로 두고도 실측 매칭률이 100%였다(§4-10-G 열린 질문 2).
    assert lookup["도축,육류가공및저장처리업"] == "101"
    assert lookup["육류가공및저장처리업"] == "1012"
    assert lookup["육류통조림및유사저장식품제조업"] == "10121"
    # ROOT 코드(대분류)는 숫자가 아니므로 제외된다
    assert "제조업" not in lookup


def test_build_induty_code_lookup_normalizes_paren_and_semicolon():
    """같은 KSIC인데 `(의복 제외)` vs `; 의복제외`로 표기가 갈린다(실측 15건)."""
    tree = [{"ID": "13", "TEXT": "섬유제품 제조업; 의복제외"}]
    lookup = build_induty_code_lookup(tree)
    assert lookup[  # 우리 쪽 표기로 조회해도 맞아야 한다
        "".join("섬유제품 제조업(의복 제외)".split()).replace("(", "").replace(")", "")
    ] == "13"


# ---------------------------------------------------------------------------
# 응답 파싱
# ---------------------------------------------------------------------------


def test_parse_search_page_extracts_corp_codes_and_total_pages():
    html = (
        "<a onclick=\"select('00415114')\">21세기화장품</a>"
        "<a onclick=\"select('01627336')\">912메디컬리조트</a>"
        "<span>[1/195]</span>"
    )
    pairs, total = parse_search_page(html)
    assert pairs == [("00415114", "21세기화장품"), ("01627336", "912메디컬리조트")]
    assert total == 195


def test_parse_industry_excel_reads_rows_positionally():
    rows = parse_industry_excel(_excel_bytes([_row("(주)가나다"), _row("주식회사 라마바")]))
    assert [r["corp_name"] for r in rows] == ["(주)가나다", "주식회사 라마바"]
    assert rows[0]["jurir_no"] == "110111-1234567"  # 하이픈 제거는 merge 단계에서


def test_parse_industry_excel_rejects_unexpected_column_count():
    """DART가 열을 바꾸면 조용히 밀려 읽지 말고 즉시 실패해야 한다."""
    wb = Workbook()
    wb.active.append(["회사이름", "주소"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(DartIndexCrawlError, match="열 수"):
        parse_industry_excel(buf.getvalue())


# ---------------------------------------------------------------------------
# 위치 결합 — 가장 조용히 망가지기 쉬운 지점
# ---------------------------------------------------------------------------


def test_merge_by_position_joins_and_derives_fields():
    excel = parse_industry_excel(_excel_bytes([_row("(주)가나다"), _row("주식회사 라마바")]))
    pairs = [("00415114", "가나다"), ("01627336", "라마바")]
    merged = merge_by_position(
        excel, pairs, business_code="10", induty_lookup=build_induty_code_lookup(TREE)
    )
    assert [m["corp_code"] for m in merged] == ["00415114", "01627336"]
    assert merged[0]["jurir_no"] == "1101111234567"  # 하이픈 제거 = FSC crno 조인키
    assert merged[0]["sido"] == "경상남도"
    assert merged[0]["sigungu"] == "김해시"
    # 업종명 -> 정밀 코드(5자리) 역매핑. 크롤 단위(중분류)는 따로 보관한다.
    assert merged[0]["induty_code"] == "10121"
    assert merged[0]["crawl_induty_code"] == "10"


def test_merge_by_position_raises_on_row_count_mismatch():
    excel = parse_industry_excel(_excel_bytes([_row("(주)가나다")]))
    with pytest.raises(DartIndexCrawlError, match="불일치"):
        merge_by_position(excel, [("00415114", "가나다"), ("999", "여분")], business_code="10")


def test_merge_by_position_raises_when_order_looks_shuffled():
    """행 수만 맞고 순서가 어긋나면 전 행이 오염되므로 반드시 막아야 한다.

    허용 하한이 3건이므로 표본이 3건 이하면 원리적으로 셔플과 정상 변형을
    구분할 수 없다 — 실제 업종은 최소 수십 건이라 문제되지 않는다.
    """
    names = [f"(주)회사{i}" for i in range(8)]
    excel = parse_industry_excel(_excel_bytes([_row(n) for n in names]))
    shuffled = [(f"{i}", f"전혀다른회사{i}") for i in range(8)]
    with pytest.raises(DartIndexCrawlError, match="불일치"):
        merge_by_position(excel, shuffled, business_code="10")


def test_merge_by_position_accepts_abbreviated_disclosure_names():
    """회귀(2026-07-20): 보험업 실측 축약형 때문에 크롤이 통째로 멈췄던 케이스.

    `search.ax`는 공시회사명 축약형을 준다 — 99행 중 9행이 이런 형태라
    완전 일치만 인정하던 검증이 90.91%로 오탐을 냈고 재시도 20회를 모두
    소진했다. 순서는 완벽히 맞았으므로 접두사 관계를 허용해야 한다.
    """
    real_pairs = [
        ("동양생명보험(주)", "동양생명"),
        ("코리안리재보험(주)", "코리안리"),
        ("현대해상화재보험(주)", "현대해상"),
        ("흥국화재해상보험(주)", "흥국화재"),
        ("퍼스트어메리칸권원보험㈜", "퍼스트어메리칸권원보험"),
        ("캑터스바이아웃제6호사모투자 합자회사", "캑터스바이아웃제6호사모투자"),
        ("삼성화재해상보험(주)", "삼성화재해상보험"),
    ]
    excel = parse_industry_excel(_excel_bytes([_row(name) for name, _ in real_pairs]))
    pairs = [(f"{i:08d}", listed) for i, (_, listed) in enumerate(real_pairs)]
    merged = merge_by_position(excel, pairs, business_code="65")
    assert len(merged) == len(real_pairs)


def test_merge_by_position_accepts_leading_prefix_stripped_names():
    """회귀(2026-07-20, 두 번째 스톨): 축약이 **앞쪽**에서 일어나는 경우.

    중분류 84에서 `재단법인 전북연구원` → `전북연구원`처럼 접두어가 잘린
    형태가 2/29 나와 크롤이 또 멈췄다 — 접두사 규칙만으로는 못 잡는다.
    """
    real_pairs = [
        ("재단법인 전북연구원", "전북연구원"),
        ("재단법인 한마음평화연구재단", "한마음평화연구재단"),
        ("(주)가나다", "가나다"),
    ]
    excel = parse_industry_excel(_excel_bytes([_row(name) for name, _ in real_pairs]))
    pairs = [(f"{i:08d}", listed) for i, (_, listed) in enumerate(real_pairs)]
    assert len(merge_by_position(excel, pairs, business_code="84")) == 3


def test_merge_by_position_allows_a_few_mismatches_in_small_industry():
    """고정 비율만 쓰면 소규모 업종에서 오탐이 난다 — 절대 허용 하한이 필요하다."""
    names = [f"(주)회사{i}" for i in range(20)]
    excel = parse_industry_excel(_excel_bytes([_row(n) for n in names]))
    pairs = [(f"{i:08d}", n.replace("(주)", "")) for i, n in enumerate(names)]
    for i in range(3):  # 20건 중 3건 불일치 = 15% (구 임계값 5%였다면 실패)
        pairs[i] = (f"{i:08d}", f"전혀다른이름{i}")
    assert len(merge_by_position(excel, pairs, business_code="10")) == 20


def test_merge_by_position_still_rejects_shuffle_despite_prefix_rule():
    """포함 관계 허용이 셔플 감지 능력을 훼손하지 않아야 한다."""
    names = [
        "동양생명보험(주)", "코리안리재보험(주)", "현대해상화재보험(주)", "흥국화재해상보험(주)",
        "삼성생명보험(주)", "한화생명보험(주)", "미래에셋생명보험(주)", "교보생명보험주식회사",
    ]
    excel = parse_industry_excel(_excel_bytes([_row(n) for n in names]))
    shuffled = [(f"{i}", n) for i, n in enumerate(reversed(
        ["동양생명", "코리안리", "현대해상", "흥국화재",
         "삼성생명", "한화생명", "미래에셋생명", "교보생명보험"]
    ))]
    with pytest.raises(DartIndexCrawlError, match="불일치"):
        merge_by_position(excel, shuffled, business_code="65")


def test_merge_by_position_tolerates_disclosure_name_variants():
    """`CJ프레시웨이` vs `씨제이프레시웨이`류 표기 차이(실측 0.62%)는 통과해야 한다."""
    names = [f"(주)회사{i}" for i in range(20)]
    excel = parse_industry_excel(_excel_bytes([_row(n) for n in names]))
    pairs = [(f"{i:08d}", n.replace("(주)", "")) for i, n in enumerate(names)]
    pairs[0] = ("00000000", "씨제이프레시웨이")  # 1/20 = 5% 불일치 → 임계값 통과
    merged = merge_by_position(excel, pairs, business_code="10")
    assert len(merged) == 20


def test_merge_by_position_falls_back_to_mid_level_code_when_name_unknown():
    """업종명 역매핑이 실패해도 필터가 빈 결과를 내지 않도록 중분류로 폴백한다."""
    excel = parse_industry_excel(_excel_bytes([_row("(주)가나다", induty="듣도보도못한업종")]))
    merged = merge_by_position(
        excel, [("00415114", "가나다")], business_code="10",
        induty_lookup=build_induty_code_lookup(TREE),
    )
    assert merged[0]["induty_code"] == "10"


# ---------------------------------------------------------------------------
# 적재
# ---------------------------------------------------------------------------


def test_upsert_dart_corp_index_inserts_then_updates(db_session_factory):
    from app.models.dart_corp_index import DartCorpIndex

    excel = parse_industry_excel(_excel_bytes([_row("(주)가나다")]))
    merged = merge_by_position(excel, [("00415114", "가나다")], business_code="10")
    with db_session_factory() as db:
        assert upsert_dart_corp_index(db, merged) == (1, 0)

    excel2 = parse_industry_excel(
        _excel_bytes([_row("(주)가나다", address="부산광역시 사상구 학감대로 1")])
    )
    merged2 = merge_by_position(excel2, [("00415114", "가나다")], business_code="10")
    with db_session_factory() as db:
        assert upsert_dart_corp_index(db, merged2) == (0, 1)
        assert db.get(DartCorpIndex, "00415114").sido == "부산광역시"


# ---------------------------------------------------------------------------
# A2 — 지역/업종 로컬 필터
# ---------------------------------------------------------------------------


def _seed(db, rows: list[tuple[str, str, str, str, str | None]], *, corp_cls: str = "기타법인") -> None:
    """(corp_code, 주소, 업종코드, 회사명, 종목코드) 시드."""
    from app.models.dart_corp_index import DartCorpIndex

    for corp_code, address, induty, name, stock in rows:
        sido, sigungu = parse_address(address)
        db.add(
            DartCorpIndex(
                corp_code=corp_code, corp_name=name, address=address,
                sido=sido, sigungu=sigungu, induty_code=induty, stock_code=stock,
                corp_cls=corp_cls,
            )
        )
    db.commit()


def test_filter_local_candidates_matches_narrow_industry_precisely(db_session_factory):
    """2026-07-18 회귀의 근본 해결 — 중분류를 고르면 그 중분류만 나와야 한다.

    FSC `sic_name` 문자열 매칭 시절에는 중분류를 골라도 대분류 전체가 통과했다.
    """
    with db_session_factory() as db:
        _seed(db, [
            ("00000001", "경상남도 김해시 삼안로 1", "10121", "육류가공", None),
            ("00000002", "경상남도 김해시 삼안로 2", "29171", "자동차부품", None),
            ("00000003", "경상남도 김해시 삼안로 3", "10", "식료품일반", None),
        ])
        got = filter_local_candidates(
            db, cond_region={"sido": ["경상남도"]}, cond_industry=["10"]
        )
    # 같은 제조업(대분류 C)이라도 자동차부품(29)은 걸러진다
    assert {r.corp_code for r in got} == {"00000001", "00000003"}


def test_filter_local_candidates_expands_major_class_letter(db_session_factory):
    """대분류는 알파벳(C)인데 `induty_code`는 숫자라, 펼치지 않으면 조용히 0건이 된다.

    `GET /api/meta/industries`가 대분류를 A~U로 주므로 사용자가 "제조업"만 고른
    조건이 그대로 들어올 수 있다 — 소속 중분류 전체로 펼쳐 매칭해야 한다.
    """
    with db_session_factory() as db:
        _seed(db, [
            ("00000001", "경상남도 김해시 1", "10121", "식료품사", None),
            ("00000002", "경상남도 김해시 2", "29171", "자동차부품사", None),
            ("00000003", "경상남도 김해시 3", "47", "소매업체", None),
        ])
        got = filter_local_candidates(
            db, cond_region={"sido": ["경상남도"]}, cond_industry=["C"]
        )
    # 제조업(C) 소속 중분류 10·29는 통과하고, 도소매(G)의 47은 걸러진다
    assert {r.corp_code for r in got} == {"00000001", "00000002"}


def test_filter_local_candidates_supports_sub_class_prefix(db_session_factory):
    """소분류(3자리) 선택 — 얕게(2자리) 분류된 회사는 못 잡는 것이 정상이다."""
    with db_session_factory() as db:
        _seed(db, [
            ("00000001", "경상남도 김해시 1", "10121", "세세분류", None),
            ("00000002", "경상남도 김해시 2", "10", "중분류만", None),
        ])
        got = filter_local_candidates(
            db, cond_region={"sido": ["경상남도"]}, cond_industry=["101"]
        )
    assert {r.corp_code for r in got} == {"00000001"}


def test_filter_local_candidates_excludes_listed_companies(db_session_factory):
    """상장사는 감사보고서를 별도 공시하지 않아 Phase 2에서 전부 FAILED가 된다."""
    with db_session_factory() as db:
        _seed(db, [("00000001", "경상남도 김해시 1", "10121", "비상장", None)])
        _seed(db, [("00000002", "경상남도 김해시 2", "10121", "코스닥상장", "123456")],
              corp_cls="코스닥시장")
        _seed(db, [("00000003", "경상남도 김해시 3", "10121", "유가상장", "654321")],
              corp_cls="유가증권시장")
        got = filter_local_candidates(db, cond_region={"sido": ["경상남도"]}, cond_industry=[])
    assert {r.corp_code for r in got} == {"00000001"}


def test_filter_local_candidates_keeps_delisted_companies(db_session_factory):
    """회귀(2026-07-20): `stock_code`로 상장 여부를 판정하면 **상장폐지 기업**이 누락된다.

    실측상 `기타법인`인데 `stock_code`가 남아 있는 회사가 1,219개다((주)프리젠,
    영풍산업 등). 이들은 지금은 비상장 외감법인이라 정당한 타깃이다.
    """
    with db_session_factory() as db:
        _seed(db, [("00000001", "경상남도 김해시 1", "10121", "상장폐지기업", "060910")])
        got = filter_local_candidates(db, cond_region={"sido": ["경상남도"]}, cond_industry=[])
    assert {r.corp_code for r in got} == {"00000001"}


def test_filter_local_candidates_narrows_by_sigungu(db_session_factory):
    with db_session_factory() as db:
        _seed(db, [
            ("00000001", "경상남도 김해시 1", "10121", "김해", None),
            ("00000002", "경상남도 창원시 2", "10121", "창원", None),
        ])
        got = filter_local_candidates(
            db,
            cond_region={"sido": ["경상남도"], "sigungu_by_sido": {"경상남도": ["김해시"]}},
            cond_industry=[],
        )
    assert {r.corp_code for r in got} == {"00000001"}


def test_upsert_dart_corp_index_dedupes_within_batch(db_session_factory):
    """같은 회사가 여러 업종에 걸쳐 등장해도 한 행으로 병합돼야 한다."""
    excel = parse_industry_excel(_excel_bytes([_row("(주)가나다"), _row("(주)가나다")]))
    merged = merge_by_position(
        excel, [("00415114", "가나다"), ("00415114", "가나다")], business_code="10"
    )
    with db_session_factory() as db:
        assert upsert_dart_corp_index(db, merged) == (1, 0)


# ---------------------------------------------------------------------------
# 동명 회사 교차 교정 (2026-07-20, M8 6단계 검증에서 발견한 버그)
# ---------------------------------------------------------------------------


class _FakeCompanyClient:
    """DartClient.get_company 대체 — corp_code -> company.json 페이로드."""

    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    async def get_company(self, corp_code: str) -> dict:
        self.calls.append(corp_code)
        return self.payloads[corp_code]


def _seed_pair(db) -> None:
    """실측 사례(동산밸브)를 그대로 옮긴 시드 — 두 행의 속성이 서로 뒤바뀐 상태다."""
    from app.models.dart_corp_index import DartCorpIndex

    db.add(
        DartCorpIndex(
            corp_code="01179565", corp_name="동산밸브 주식회사", corp_name_norm="동산밸브",
            crawl_induty_code="29", jurir_no="2062110003835",
            address="전라남도 여수시 화산1길 48-9", sido="전라남도", sigungu="여수시",
            induty_code="2913", induty_name="펌프 및 압축기 제조업", ceo_name="여수대표",
        )
    )
    db.add(
        DartCorpIndex(
            corp_code="00929714", corp_name="(주)동산밸브", corp_name_norm="동산밸브",
            crawl_induty_code="29", jurir_no="1955110200281",
            address="경상남도 김해시 진례면 고모로324번안길 167-2", sido="경상남도", sigungu="김해시",
            induty_code="29133", induty_name="탭, 밸브 및 유사장치 제조업", ceo_name="김해대표",
        )
    )
    db.commit()


def test_find_ambiguous_corp_codes_groups_same_name_within_industry(db_session_factory):
    """같은 이름이라도 크롤 업종이 다르면 위치 결합에서 섞일 수 없어 위험군이 아니다."""
    from app.models.dart_corp_index import DartCorpIndex

    with db_session_factory() as db:
        _seed_pair(db)
        db.add(
            DartCorpIndex(
                corp_code="00000009", corp_name="동산밸브(주)", corp_name_norm="동산밸브",
                crawl_induty_code="68", jurir_no="9999999999999",
            )
        )
        db.commit()
        groups = find_ambiguous_corp_codes(db)

    assert [sorted(g) for g in groups] == [["00929714", "01179565"]]


@pytest.mark.asyncio
async def test_reconcile_restores_crossed_attributes_by_jurir_no(db_session_factory):
    """핵심 회귀 — 교차된 주소/업종/대표자가 jurir_no 기준으로 제자리를 찾아야 한다.

    company.json이 주지 않는 `induty_name`까지 복원되는 것이 덮어쓰기가 아니라
    순열 교정을 택한 이유다.
    """
    from app.models.dart_corp_index import DartCorpIndex

    with db_session_factory() as db:
        _seed_pair(db)

    client = _FakeCompanyClient(
        {
            # 정본: 01179565가 김해, 00929714가 여수 (인덱스와 정반대)
            "01179565": {"jurir_no": "1955110200281", "adres": "경상남도 김해시 ..."},
            "00929714": {"jurir_no": "2062110003835", "adres": "전라남도 여수시 ..."},
        }
    )
    stats = await reconcile_ambiguous_rows(client, db_session_factory)

    assert stats["repaired"] == 2
    assert stats["fallback"] == 0
    with db_session_factory() as db:
        kimhae = db.get(DartCorpIndex, "01179565")
        yeosu = db.get(DartCorpIndex, "00929714")
    assert (kimhae.sido, kimhae.sigungu) == ("경상남도", "김해시")
    assert kimhae.induty_code == "29133"
    assert kimhae.induty_name == "탭, 밸브 및 유사장치 제조업"
    assert kimhae.ceo_name == "김해대표"
    assert (yeosu.sido, yeosu.sigungu) == ("전라남도", "여수시")
    assert yeosu.induty_name == "펌프 및 압축기 제조업"


@pytest.mark.asyncio
async def test_reconcile_leaves_already_correct_rows_untouched(db_session_factory):
    """교차가 없던 그룹은 값이 그대로여야 한다(멱등) — repaired가 0으로 잡힌다."""
    from app.models.dart_corp_index import DartCorpIndex

    with db_session_factory() as db:
        _seed_pair(db)

    client = _FakeCompanyClient(
        {
            "01179565": {"jurir_no": "2062110003835", "adres": "전라남도 여수시 ..."},
            "00929714": {"jurir_no": "1955110200281", "adres": "경상남도 김해시 ..."},
        }
    )
    stats = await reconcile_ambiguous_rows(client, db_session_factory)

    assert stats["repaired"] == 0
    with db_session_factory() as db:
        assert db.get(DartCorpIndex, "00929714").sigungu == "김해시"
        assert db.get(DartCorpIndex, "01179565").sigungu == "여수시"


@pytest.mark.asyncio
async def test_reconcile_falls_back_to_company_json_when_jurir_no_unknown(db_session_factory):
    """그룹 안에 해당 법인등록번호가 없으면 정본 값으로 덮어쓴다.

    엑셀이 애초에 다른 회사를 담고 있던 경우로, 순열 교정으로는 복원할 수 없다.
    이때 `induty_name`은 지운다 — 코드만 정본으로 바꾸고 이름을 남겨두면
    화면에 서로 어긋난 업종이 표시된다.
    """
    from app.models.dart_corp_index import DartCorpIndex

    with db_session_factory() as db:
        _seed_pair(db)

    client = _FakeCompanyClient(
        {
            "01179565": {
                "jurir_no": "7777777777777",
                "adres": "부산광역시 사하구 다대로 1",
                "induty_code": "29119",
                "ceo_nm": "부산대표",
            },
            "00929714": {"jurir_no": "1955110200281", "adres": "경상남도 김해시 ..."},
        }
    )
    stats = await reconcile_ambiguous_rows(client, db_session_factory)

    assert stats["fallback"] == 1
    with db_session_factory() as db:
        row = db.get(DartCorpIndex, "01179565")
    assert (row.sido, row.sigungu) == ("부산광역시", "사하구")
    assert row.induty_code == "29119"
    assert row.induty_name is None
    assert row.ceo_name == "부산대표"
