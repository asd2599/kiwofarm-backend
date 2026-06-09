"""수확카드 조립 — 보관·손질 / 섭취·영양 / 제철 / 레시피.

1차 소스: data/monthfd/by_crop.json (이달의음식, 18작물 구조화 데이터).
없는 작물은 gpt-4o-mini 로 생성 후 메모리 캐시 (source="ai" 표기).
자세한 레시피는 외부 deep link 로 위임 (기획서 정책).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from openai import AsyncOpenAI

from app.config import settings
from app.core.planting import matrix

log = logging.getLogger(__name__)

MONTHFD_PATH = Path(__file__).resolve().parents[3] / "data" / "monthfd" / "by_crop.json"
_GEN_MODEL = "gpt-4o-mini"
MAX_SECTION = 700  # 카드 섹션 길이 상한(문장 경계 트림)
MAX_RECIPES = 3

_ai_cache: dict[str, dict[str, Any]] = {}


def _trim(text: str, limit: int = MAX_SECTION) -> str:
    """문장 경계로 자른다 (카드는 요약 화면 — 전체 본문은 RAG 챗봇 몫)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", ".\n", "다.", "요."):
        idx = cut.rfind(sep)
        if idx > limit // 2:
            return cut[: idx + len(sep)].strip()
    return cut.strip() + "…"


@lru_cache(maxsize=1)
def _monthfd() -> dict[str, dict[str, Any]]:
    if not MONTHFD_PATH.exists():
        return {}
    try:
        data = json.loads(MONTHFD_PATH.read_text(encoding="utf-8"))
        return {g["slug"]: g for g in data.get("crops", [])}
    except (OSError, json.JSONDecodeError, KeyError) as e:
        log.warning("monthfd by_crop 로드 실패: %s", e)
        return {}


def _season_months(ingredients: list[dict]) -> list[int]:
    months: set[int] = set()
    for ing in ingredients:
        for ym in ing.get("months", []):
            try:
                months.add(int(ym.split("-")[1]))
            except (IndexError, ValueError):
                continue
    return sorted(months)


def _pick_recipes(recipes: list[dict], crop_name: str) -> list[dict]:
    """카드용 레시피 상위 N개 선별.

    단품 요리(짧은 이름) + 영양값 보유 우선. '양상추'처럼 작물명을 다른 단어의
    일부로 포함해 오매칭된 레시피는 제외한다.
    """
    def _bad_containment(name: str) -> bool:
        idx = name.find(crop_name)
        # 작물명 바로 앞이 한글이면 다른 단어의 일부일 가능성 (양상추, 알감자 등 예외 일부 감수)
        return idx > 0 and "가" <= name[idx - 1] <= "힣"

    def _energy(r: dict) -> float:
        try:
            return float(r.get("nutrients", {}).get("에너지(kcal)", 0) or 0)
        except ValueError:
            return 0.0

    pool = [
        r for r in recipes
        if crop_name not in r.get("name", "") or not _bad_containment(r["name"])
    ]
    pool.sort(key=lambda r: (-(1 if _energy(r) > 0 else 0), len(r.get("name", ""))))
    return pool[:MAX_RECIPES]


def deep_links(crop_name: str) -> list[dict[str, str]]:
    q = quote(crop_name)
    return [
        {"label": "만개의레시피에서 더 보기", "url": f"https://www.10000recipe.com/recipe/list.html?q={q}"},
        {"label": "네이버 레시피 검색", "url": f"https://search.naver.com/search.naver?query={quote(crop_name + ' 레시피')}"},
    ]


async def _generate_card_text(crop_name: str) -> dict[str, str]:
    """monthfd 미보유 작물의 보관·섭취·영양 텍스트 생성 (메모리 캐시)."""
    if crop_name in _ai_cache:
        return _ai_cache[crop_name]
    if not settings.openai_api_key:
        return {}
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=_GEN_MODEL,
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 식재료 전문가입니다. 갓 수확한 텃밭 작물의 활용 정보를 "
                    "초보자 눈높이 한국어로, 각 항목 3~5문장으로 작성합니다. JSON 으로만 답합니다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"작물: {crop_name}\n"
                    '형식: {"storage": "보관방법과 손질법", '
                    '"eating": "맛있게 먹는 방법", "nutrition": "주요 영양성분과 효능"}'
                ),
            },
        ],
    )
    try:
        data = json.loads(resp.choices[0].message.content or "{}")
        out = {k: str(data.get(k, "")) for k in ("storage", "eating", "nutrition")}
    except json.JSONDecodeError:
        out = {}
    _ai_cache[crop_name] = out
    return out


async def build_card(crop_slug: str) -> dict[str, Any] | None:
    """작물 슬러그 → 수확카드 데이터. 마스터에 없는 작물이면 None."""
    crop = matrix.get_crop(crop_slug)
    if crop is None:
        return None
    name = crop["name"]
    group = _monthfd().get(crop_slug)

    if group and group.get("ingredients"):
        ing = max(group["ingredients"], key=lambda i: len(i.get("storage", "")))
        recipes = [
            {
                "name": r.get("name", ""),
                "nutrients": {
                    k: r["nutrients"][k]
                    for k in ("에너지(kcal)", "탄수화물(g)", "단백질(g)")
                    if k in r.get("nutrients", {})
                },
            }
            for r in _pick_recipes(group.get("recipes", []), name)
        ]
        return {
            "cropSlug": crop_slug,
            "cropName": name,
            "category": crop.get("category", ""),
            "difficulty": crop.get("difficulty"),
            "daysToHarvest": crop.get("days_to_harvest"),
            "source": "nongsaro:monthFd",
            "storage": _trim(ing.get("storage", "")),
            "eating": _trim(ing.get("eating", "")),
            "nutrition": _trim(ing.get("nutrition", "")),
            "seasonMonths": _season_months(group["ingredients"]),
            "recipes": recipes,
            "links": deep_links(name),
        }

    gen = await _generate_card_text(name)
    return {
        "cropSlug": crop_slug,
        "cropName": name,
        "category": crop.get("category", ""),
        "difficulty": crop.get("difficulty"),
        "daysToHarvest": crop.get("days_to_harvest"),
        "source": "ai",
        "storage": _trim(gen.get("storage", "")),
        "eating": _trim(gen.get("eating", "")),
        "nutrition": _trim(gen.get("nutrition", "")),
        "seasonMonths": [],
        "recipes": [],
        "links": deep_links(name),
    }
