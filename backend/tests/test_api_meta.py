"""app/api/meta.py의 신규 M4 엔드포인트(/regions, /industries) 테스트.

quota/validate-key는 DartClient/FscCorpInfoClient(네트워크 호출)를 타므로
여기서 다루지 않는다 — regions/industries는 정적 데이터만 직렬화하므로
DB/네트워크 의존 없이 바로 TestClient로 검증할 수 있다.

candidates-preview(2026-07-17 추가, M8 3단계에서 `dart_corp_index` 기준으로 교체)는
DB 의존이 있어 `app.api.meta.get_session_factory`를 `db_session_factory`
픽스처(인메모리 SQLite)로 monkeypatch해서 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main as app_main
from app.api import meta as meta_module
from app.core.filters import SIDO_ALIASES
from app.core.industry_data import INDUSTRIES
from app.core.region_data import REGIONS
from app.models.dart_corp_index import DartCorpIndex


def _client() -> TestClient:
    return TestClient(app_main.app)


def test_get_regions_returns_all_17_sido():
    resp = _client().get("/api/meta/regions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 17
    sidos = {entry["sido"] for entry in body}
    assert sidos == set(REGIONS.keys())


def test_get_regions_sido_names_match_sido_aliases_keys():
    """프론트가 그대로 cond_region.sido에 넣을 값이므로 SIDO_ALIASES key와 일치해야 한다."""
    resp = _client().get("/api/meta/regions")
    sidos = {entry["sido"] for entry in resp.json()}
    assert sidos == set(SIDO_ALIASES.keys())


def test_get_regions_gyeongnam_includes_gimhae():
    resp = _client().get("/api/meta/regions")
    body = resp.json()
    gyeongnam = next(entry for entry in body if entry["sido"] == "경상남도")
    assert "김해시" in gyeongnam["sigungu"]


def test_get_regions_sejong_has_empty_sigungu():
    """세종특별자치시는 하위 시/군/구가 없는 단층제 — 빈 배열이어야 한다."""
    resp = _client().get("/api/meta/regions")
    body = resp.json()
    sejong = next(entry for entry in body if entry["sido"] == "세종특별자치시")
    assert sejong["sigungu"] == []


def test_get_industries_returns_21_major_categories():
    resp = _client().get("/api/meta/industries")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 21
    assert {entry["code"] for entry in body} == {entry["code"] for entry in INDUSTRIES}


def test_get_industries_manufacturing_has_children_codes():
    resp = _client().get("/api/meta/industries")
    body = resp.json()
    manufacturing = next(entry for entry in body if entry["code"] == "C")
    assert manufacturing["name"] == "제조업"
    child_codes = {child["code"] for child in manufacturing["children"]}
    assert "10" in child_codes  # 식료품 제조업
    assert all(len(code) == 2 for code in child_codes)


def test_get_candidates_preview_counts_local_matches_without_quota_warning(
    db_session_factory, monkeypatch
):
    monkeypatch.setattr(meta_module, "get_session_factory", lambda: db_session_factory)
    with db_session_factory() as db:
        db.add_all(
            [
                DartCorpIndex(
                    corp_code=f"0000000{i}",
                    corp_name=f"김해사{i}",
                    sido="경상남도",
                    sigungu="김해시",
                )
                for i in range(3)
            ]
        )
        db.commit()

    resp = _client().post(
        "/api/meta/candidates-preview",
        json={"region": {"sido": "경상남도", "sigungu": ["김해시"]}, "industry": []},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_count"] == 3
    assert body["exceeds_daily_quota"] is False
    assert body["estimated_days"] == 1


def test_get_candidates_preview_flags_quota_exceeded(db_session_factory, monkeypatch):
    monkeypatch.setattr(meta_module, "get_session_factory", lambda: db_session_factory)
    # 하루 처리 가능 후보 수 = daily_quota_limit / _DART_CALLS_PER_CANDIDATE 이므로
    # 호출 한도를 10으로 낮추면 하루 2개사가 된다(§4-10-C 기준 변경).
    monkeypatch.setattr(meta_module, "_DART_CALLS_PER_CANDIDATE", 5)
    monkeypatch.setattr(
        meta_module, "get_settings", lambda: SimpleNamespace(daily_quota_limit=10)
    )
    with db_session_factory() as db:
        db.add_all(
            [
                DartCorpIndex(
                    corp_code=f"0000000{i}",
                    corp_name=f"김해사{i}",
                    sido="경상남도",
                    sigungu="김해시",
                )
                for i in range(5)
            ]
        )
        db.commit()

    resp = _client().post(
        "/api/meta/candidates-preview",
        json={"region": {"sido": "경상남도", "sigungu": ["김해시"]}, "industry": []},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_count"] == 5
    assert body["daily_quota_assumed"] == 2
    assert body["exceeds_daily_quota"] is True
    assert body["estimated_days"] == 3
