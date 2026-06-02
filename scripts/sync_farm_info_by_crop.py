"""농사로 이달의 농업기술 + 주간농사정보 → 작목별 정리 (RAG 검색용).

두 출처의 텍스트를 KAMIS 작목명 사전으로 작물에 귀속(attribution)시켜
backend/data/farminfo/by_crop/{key}.json 으로 묶는다. 나중에 작목별로 임베딩·
검색하기 좋은 형태(작물별 passage 모음)다.

출처별 귀속 방식:
  - 이달의 농업기술(monthFarmTech): API 본문 + 제목/요약. 제목 작물 + 본문 청크 작물.
  - 주간농사정보(weekFarmInfo): 회보 PDF 텍스트(작물 미태깅) → 청크별로 언급 작물 매칭.

작물명 매칭:
  - 2글자 이상: 부분문자열(예: '사과' → '사과나무'도 매칭)
  - 1글자(무·파·벼·콩·팥·깨·갓): 앞뒤가 한글이 아닐 때만(‘아무/무엇’ 오탐 방지)

실행 (기본: 이달의 전체 + 주간 최신 12호):
    uv run python scripts/sync_farm_info_by_crop.py
    uv run python scripts/sync_farm_info_by_crop.py --month-limit 50 --week-pdfs 8
    uv run python scripts/sync_farm_info_by_crop.py --no-week   # 이달의만
환경: .env 의 NONGSARO_API_KEY2.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import kamis_crops  # noqa: E402
from app.data import nongsaro_monthtech as mt  # noqa: E402
from app.data import nongsaro_weekfarm as wf  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync_farm_info_by_crop")

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "farminfo"
BY_CROP_DIR = DATA_DIR / "by_crop"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


# ─────────────────────── 작목 사전 / 귀속 ───────────────────────


# KAMIS 시드엔 없지만 주말·귀농으로 재배하는 작물(합성 키 x_*). 농사로엔 자주 나옴.
_EXTRA_CROPS: dict[str, str] = {
    "옥수수": "x_corn",
    "벼": "x_rice",  # 귀농 핵심(쌀=가공품만 KAMIS에 있음)
    "인삼": "x_ginseng",
    "두릅": "x_durup",
    "무화과": "x_fig",
    "국화": "x_chrysanthemum",  # 귀농 화훼
    "장미": "x_rose",
    "수국": "x_hydrangea",
}
# 농사로 표기(generic) → 기존 KAMIS itemCode 별칭. KAMIS가 변형명만 가진 작물 보완.
_ALIASES: dict[str, str] = {
    "마늘": "244",  # 피마늘(244) — KAMIS엔 깐마늘/피마늘만, 농사로는 '마늘'
    "키위": "419",  # 참다래(419)
    "감": "416",  # 단감(416)
    "감나무": "416",
    "배나무": "412",  # 배(412) — 1글자 '배'가 '배나무'를 못 잡는 문제 보완
    "고추": "242",  # 풋고추(242, 생) — 농사로 generic '고추'
}


def _build_vocab() -> dict[str, str]:
    """작목명 → itemCode/합성키. KAMIS + 보완 작물(_EXTRA_CROPS) + 별칭(_ALIASES)."""
    name_to_code: dict[str, str] = {}
    for r in kamis_crops.all_crops():
        name_to_code.setdefault(r["itemName"], r["itemCode"])
    for name, code in {**_EXTRA_CROPS, **_ALIASES}.items():
        name_to_code.setdefault(name, code)
    return name_to_code


# 농사 맥락어: 동음이의 작물명(가지=여러 가지, 무=무엇 등)을 작물로 인정할 근거.
_CTX_RE = re.compile(
    "재배|수확|파종|정식|아주심기|육묘|모종|병해|방제|살포|시비|웃거름|밑거름|"
    "착과|적과|순지르기|봉지|생육|포장|관수|물주기|저장|출하|수세|결실|개화|월동"
)
# 작물명이지만 일반어로도 흔해 맥락어 인접 시에만 인정할 2글자+ 이름(1글자류는 자동 포함).
_AMBIGUOUS_2 = {"가지"}  # 가지: '여러 가지', '가지치기' 등 일반어 오탐 방지
# 작물이지만 농사 회보엔 거의 안 나오고 일반어(조기=일찍)로 오탐만 내는 이름.
_BLOCK = {"조기"}
# 본문(주간 회보) 스캔에서 제외할 이름: '가지'는 가지치기/가지에(=branch) 노이즈가 커
# 본문에선 빼고, 이달의 농업기술의 깨끗한 '제목'에서만 인정한다.
_BODY_BLOCK = {"가지"}
_CTX_WINDOW = 20
# '3가지', '여러 가지', '한 가지' 같은 수량 표현(작물 아님)을 거르는 직전 토큰.
_COUNT_RE = re.compile(r"(?:\d|여러|몇|한|두|세|네|갖은)\s*$")


def _make_matchers(names: list[str]) -> list[tuple[str, re.Pattern[str], bool]]:
    """(작물명, 매칭패턴, 동음이의여부). 1글자는 양쪽 한글경계, 2글자+는 왼쪽 경계."""
    matchers: list[tuple[str, re.Pattern[str], bool]] = []
    for name in names:
        if name in _BLOCK:
            continue
        if len(name) == 1:
            pat = re.compile(rf"(?<![가-힣]){re.escape(name)}(?![가-힣])")
            ambiguous = True
        else:
            pat = re.compile(rf"(?<![가-힣]){re.escape(name)}")
            ambiguous = name in _AMBIGUOUS_2
        matchers.append((name, pat, ambiguous))
    return matchers


def _good_ambiguous(text: str, m: re.Match[str]) -> bool:
    """동음이의 매치가 진짜 작물인지: 수량표현 아님 + 농사 맥락어 인접."""
    if _COUNT_RE.search(text[: m.start()]):
        return False
    s = text[max(0, m.start() - _CTX_WINDOW) : m.end() + _CTX_WINDOW]
    return _CTX_RE.search(s) is not None


def _detect(
    text: str,
    matchers: list[tuple[str, re.Pattern[str], bool]],
    *,
    block: frozenset[str] = frozenset(),
) -> set[str]:
    found: set[str] = set()
    for name, pat, ambiguous in matchers:
        if name in block:
            continue
        matches = list(pat.finditer(text))
        if not matches:
            continue
        if ambiguous and not any(_good_ambiguous(text, m) for m in matches):
            continue
        found.add(name)
    return found


def _chunk(text: str) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    out = [text[i : i + CHUNK_SIZE].strip() for i in range(0, len(text), step)]
    return [c for c in out if c]


def _slug(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", s).strip("_")


# ─────────────────────── 수집 → 작목별 버킷 ───────────────────────


def _add(buckets: dict[str, list[dict]], crop: str, passage: dict) -> None:
    buckets.setdefault(crop, []).append(passage)


async def collect_monthtech(matchers, *, limit: int | None) -> dict[str, list[dict]]:
    log.info("이달의 농업기술 수집 시작 (limit=%s)", limit)
    articles = await mt.fetch_all_articles(max_items=limit)
    log.info("이달의 기사 %d건 수집", len(articles))
    buckets: dict[str, list[dict]] = {}
    for a in articles:
        # 기사 제목으로만 작물 귀속(요약·본문은 '3가지' 등 누수가 있어 제외).
        title_crops = _detect(a.title, matchers)
        if not title_crops:
            continue  # 작물 미특정 기사(일반 기술)는 작목별 정리 대상 아님
        chunks = _chunk(a.body) or ([a.summary] if a.summary else [])
        for idx, ch in enumerate(chunks):
            for crop in title_crops:
                _add(
                    buckets,
                    crop,
                    {
                        "source": "monthtech",
                        "refId": a.curation_no,
                        "date": a.svc_date,
                        "title": a.title,
                        "chunkIndex": idx,
                        "text": ch,
                        "via": "title",
                    },
                )
    return buckets


async def collect_weekfarm(matchers, *, num_pdfs: int) -> dict[str, list[dict]]:
    log.info("주간농사정보 수집 시작 (최신 PDF %d호)", num_pdfs)
    infos = await wf.fetch_all(max_items=None)
    with_pdf = [i for i in infos if i.pdf_url][:num_pdfs]
    log.info("PDF 보유 회보 %d호 대상", len(with_pdf))
    buckets: dict[str, list[dict]] = {}
    for info in with_pdf:
        text = await wf.fetch_pdf_text(info)
        if not text:
            continue
        for idx, ch in enumerate(_chunk(text)):
            for crop in _detect(ch, matchers, block=_BODY_BLOCK):
                _add(
                    buckets,
                    crop,
                    {
                        "source": "weekfarm",
                        "refId": info.cntnts_no,
                        "date": info.reg_date,
                        "title": info.subject,
                        "chunkIndex": idx,
                        "text": ch,
                        "via": "body",
                    },
                )
        log.info("  회보 %s 처리 (%s)", info.cntnts_no, info.subject[:28])
    return buckets


def _merge(dst: dict[str, list[dict]], src: dict[str, list[dict]]) -> None:
    for crop, passages in src.items():
        dst.setdefault(crop, []).extend(passages)


async def main() -> None:
    ap = argparse.ArgumentParser(description="농사로 이달의/주간 → 작목별 정리")
    ap.add_argument("--month-limit", type=int, default=None, help="이달의 기사 최대 수(기본 전체)")
    ap.add_argument("--week-pdfs", type=int, default=24, help="주간 회보 PDF 최신 N호(기본 24)")
    ap.add_argument("--no-month", action="store_true", help="이달의 농업기술 건너뛰기")
    ap.add_argument("--no-week", action="store_true", help="주간농사정보 건너뛰기")
    args = ap.parse_args()

    name_to_code = _build_vocab()
    matchers = _make_matchers(sorted(name_to_code, key=len, reverse=True))
    log.info("작목 사전 %d종 로드", len(name_to_code))

    buckets: dict[str, list[dict]] = {}
    if not args.no_month:
        _merge(buckets, await collect_monthtech(matchers, limit=args.month_limit))
    if not args.no_week:
        _merge(buckets, await collect_weekfarm(matchers, num_pdfs=args.week_pdfs))

    # 작목별 파일 + 매니페스트 작성
    BY_CROP_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for crop, passages in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        # 동일 (source,refId,chunkIndex) 중복 제거
        seen: set[tuple] = set()
        uniq: list[dict] = []
        for p in passages:
            k = (p["source"], p["refId"], p["chunkIndex"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(p)
        item_code = name_to_code.get(crop, "")
        key = item_code or f"x_{_slug(crop)}"
        by_src = {"monthtech": 0, "weekfarm": 0}
        for p in uniq:
            by_src[p["source"]] += 1
        (BY_CROP_DIR / f"{key}.json").write_text(
            json.dumps(
                {"cropName": crop, "itemCode": item_code, "count": len(uniq), "passages": uniq},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        manifest.append(
            {
                "key": key,
                "cropName": crop,
                "itemCode": item_code,
                "inKamis": bool(item_code) and not item_code.startswith("x_"),
                "count": len(uniq),
                "sources": by_src,
            }
        )

    (DATA_DIR / "manifest.json").write_text(
        json.dumps(
            {
                "generatedAt": datetime.now().isoformat(timespec="seconds"),
                "cropCount": len(manifest),
                "totalPassages": sum(m["count"] for m in manifest),
                "crops": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    matched_kamis = sum(1 for m in manifest if m["inKamis"])
    log.info(
        "완료: 작목 %d종(KAMIS매칭 %d) / passage %d개 → %s",
        len(manifest), matched_kamis, sum(m["count"] for m in manifest), BY_CROP_DIR,
    )


if __name__ == "__main__":
    asyncio.run(main())
