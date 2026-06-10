"""심기(planting) 도메인 스모크 테스트 — 매트릭스 로드·결정적 추천·작물 API.

추천 엔진은 결정적(코드)이므로 네트워크/LLM 없이 검증한다.
GET 엔드포인트만 TestClient 로 확인(POST /recommend 는 OpenAI 호출이 있어 제외).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.planting import chat, matrix, region
from app.core.planting.recommend import recommend
from app.main import app
from app.schemas.planting import ChatMessage, PlantingInput

SAMPLE = PlantingInput(
    sigungu="경기도 성남시",
    place="베란다",
    sun_hours="3~5h",
    experience="처음",
    area_m2=2,
    prefs=["잎채소", "허브"],
    top_n=6,
)


def test_matrix_loads_40_crops():
    crops = matrix.all_crops()
    assert len(crops) == 40
    # 모든 작물에 필수 메타가 있다
    for c in crops:
        assert c["environments"] and c["category"] and c["days_to_harvest"]
    # 커버 작물은 캘린더가 채워져 있다
    lettuce = matrix.get_crop("lettuce")
    assert lettuce and lettuce["calendar"]
    assert matrix.plantable_in_month(lettuce, 4) is True


def test_region_zone():
    assert region.zone_of("경기도 성남시") == "중부"
    assert region.zone_of("제주특별자치도 서귀포시") == "제주"
    assert region.zone_of("전라남도 순천시") == "남부"
    assert region.zone_of("") == "중부"


def test_recommend_deterministic_and_scored():
    r1 = recommend(SAMPLE, now_month=6)
    r2 = recommend(SAMPLE, now_month=6)
    assert r1.model_dump() == r2.model_dump()  # 결정적
    assert r1.month == 6 and r1.zone == "중부"
    assert 1 <= len(r1.recommendations) <= 6
    # 점수 내림차순
    scores = [it.score for it in r1.recommendations]
    assert scores == sorted(scores, reverse=True)
    # 하드 필터: 베란다에서 키울 수 있는 작물만
    for it in r1.recommendations:
        crop = matrix.get_crop(it.crop_id)
        assert "베란다" in crop["environments"]


def test_recommend_hard_filter_excludes_field_only():
    # 호박은 노지 전용 → 베란다 추천에서 제외
    res = recommend(SAMPLE, now_month=6)
    ids = {it.crop_id for it in res.recommendations}
    assert "pumpkin" not in ids


def test_start_date_shifts_month():
    # startDate(YYYY-MM-DD)가 있으면 그 달 기준으로 추천한다(now_month 무시).
    nxt = PlantingInput(**{**SAMPLE.model_dump(), "startDate": "2026-07-15"})
    res = recommend(nxt, now_month=6)
    assert res.month == 7


def test_get_crops_endpoint():
    client = TestClient(app)
    resp = client.get("/api/v1/planting/crops")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 40
    assert {"id", "name", "category", "difficulty"} <= set(data[0])


def test_get_crop_detail_endpoint():
    client = TestClient(app)
    resp = client.get("/api/v1/planting/crops/lettuce")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "lettuce"
    assert body["calendar"]  # 12개월 캘린더 존재
    assert client.get("/api/v1/planting/crops/__nope__").status_code == 404


# ───────────────────────── 챗봇 ─────────────────────────


def test_chat_detect_crops():
    # 긴 이름 우선: 방울토마토 → cherry_tomato 먼저
    assert chat.detect_crops("방울토마토 물 얼마나?", None)[0] == "cherry_tomato"
    # 별칭: 총각무 → altari_radish 포함
    assert "altari_radish" in chat.detect_crops("총각무 언제 심어요?", None)
    # 컨텍스트 추천작물 우선 포함
    ctx = {"recommendations": [{"crop_id": "basil", "name": "바질"}]}
    assert chat.detect_crops("이거 어때요?", ctx) == ["basil"]


def test_chat_chips_switch():
    # 작물·추천 맥락 없으면 STARTER, 있으면 AFTER_RECO
    assert chat._chips(None, []) == chat.STARTER_CHIPS
    assert chat._chips({"recommendations": [{"crop_id": "basil"}]}, []) == chat.AFTER_RECO_CHIPS
    assert chat._chips(None, ["lettuce"]) == chat.AFTER_RECO_CHIPS


async def test_chat_fallback_without_key(monkeypatch):
    # 키 없으면 200 안내문 + 칩 + sources(감지된 작물) 반환, 네트워크 호출 없음
    monkeypatch.setattr("app.core.planting.chat.settings.openai_api_key", "")
    monkeypatch.setattr("app.core.planting.chat._client", None)
    res = await chat.answer([ChatMessage(role="user", content="상추 키우기 쉬워요?")], None)
    assert res.chips and res.sources[0].crop_id == "lettuce"
    assert "AI" in res.answer or "키" in res.answer
