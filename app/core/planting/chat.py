"""심기 도메인 챗봇 — 작목 상담사 (§5, 부록 D/E).

"매트릭스가 결정, AI는 설명" 원칙을 챗봇에도 적용한다:
  - 시기/난이도/수확일 등 사실은 matrix.json(작물 카드)에서 주입.
  - 재배 지식은 RAG(retrieve_boosted, garden 가중치 최상 + _common 병행)에서 회수.
  - gpt-4o-mini 는 그 근거로만 한국어로 쉽게 답한다(환각 방지 system prompt).

키 없거나 LLM 실패 시에도 200 으로 안내문 + 칩을 반환(프론트가 깨지지 않게).
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings
from app.core.planting import matrix
from app.core.rag import retrieve as rag
from app.data import crop_ids
from app.schemas.planting import ChatMessage, ChatResponse, ChatSource

_MODEL = "gpt-4o-mini"
_TIMEOUT_S = 15.0
_client: AsyncOpenAI | None = None

# 부록 E — 진입/추천후 칩
STARTER_CHIPS = [
    "내 환경에 맞는 작물 추천받기",
    "이번 달 심을 수 있는 작물",
    "초보도 실패 없는 작물 3가지",
    "베란다(남향)에서 키우기 쉬운 채소",
    "물 자주 못 줘도 되는 작물",
]
AFTER_RECO_CHIPS = [
    "이 작물 화분 크기는?",
    "씨앗으로 할까 모종으로 할까?",
    "다음 달엔 뭘 심지?",
    "물은 얼마나 자주 줘요?",
]

_SYS = (
    "당신은 텃밭 초보를 돕는 한국어 작목 상담사입니다.\n"
    "규칙:\n"
    "- 제공된 '작물 데이터'와 '참고지식'에 근거해서만 답하세요.\n"
    "- 재배 시기·수치는 데이터 값만 사용하고, 임의로 지어내지 마세요. 모르면 모른다고 하세요.\n"
    "- 초보도 이해할 쉬운 말로 1~3문장 + 필요하면 짧은 목록으로 답하세요.\n"
    "- 진단·수익 보장 등 단정적 표현은 피하고, 참고용임을 전제로 합니다."
)

_MAX_CROPS = 3  # 한 번에 컨텍스트로 넣을 작물 수
_RAG_K = 6  # 작물별 회수 청크 수(깊이) — 근거를 두껍게.
_COMMON_K = 4  # 공통(_common) 회수 청크 수.
_GLOBAL_K = 8  # 작물이 특정되지 않은 질문에 전 작물 임베딩에서 회수할 청크 수.
_MAX_CTX_CHARS = 4500  # 깊어진 회수를 담도록 컨텍스트 상한도 상향.


def _get_client() -> AsyncOpenAI | None:
    global _client
    if not settings.openai_api_key:
        return None
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=_TIMEOUT_S)
    return _client


def _last_user(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return m.content
    return messages[-1].content if messages else ""


def detect_crops(text: str, context: dict | None) -> list[str]:
    """문장에서 작물 슬러그 추출. 질문에 '명시한' 작물을 추천 맥락보다 우선한다.

    긴 이름 우선(방울토마토 > 토마토)으로 부분일치 스캔. 별칭(crop_ids.NAME_ALIAS) 포함.
    명시 작물을 먼저 둬야 slugs[:_MAX_CROPS] 로 자를 때 사용자가 지금 물어본 작물이
    추천작물에 밀려 빠지지 않는다(예: 추천 3개 후 '바질' 질문 → 바질 유지).
    """
    slugs: list[str] = []

    # 1) 본문에서 명시한 작물 먼저(현재 질문 의도 최우선)
    name_to_slug: dict[str, str] = {c["name"]: c["id"] for c in matrix.all_crops()}
    for alias, real in crop_ids.NAME_ALIAS.items():
        rec = crop_ids.find_by_name(real)
        if rec:
            name_to_slug.setdefault(alias, rec["id"])
    for nm in sorted(name_to_slug, key=len, reverse=True):
        if nm in text and name_to_slug[nm] not in slugs:
            slugs.append(name_to_slug[nm])

    # 2) 경로 A 컨텍스트의 추천 작물 보강(명시 작물 다음)
    if context:
        for r in context.get("recommendations", []) or []:
            cid = r.get("crop_id") if isinstance(r, dict) else None
            if cid and matrix.get_crop(cid) and cid not in slugs:
                slugs.append(cid)
    return slugs


def _crop_card(slug: str) -> str | None:
    c = matrix.get_crop(slug)
    if not c:
        return None
    cal = c["calendar"]
    plant = sorted(
        {int(m) for m, acts in cal.items() for a in acts if a["action"] in ("파종", "정식")}
    )
    harvest = sorted({int(m) for m, acts in cal.items() for a in acts if a["action"] == "수확"})
    note = f" {c['climate_note']}" if c.get("climate_note") else ""
    review = " [AI보강·검수전]" if c.get("needs_review") else ""
    return (
        f"- {c['name']}({slug}): 난이도 {c['difficulty']}/3, 수확까지 {c['days_to_harvest']}일, "
        f"심는 달 {plant or '데이터없음'}, 수확 달 {harvest or '데이터없음'}, "
        f"장소 {c['environments']}, 일조 {c['sunlight']}, 물 {c['water_need']}.{note}{review}"
    )


# 임베딩 kind → 사용자에게 보여줄 농사로 데이터셋 이름(출처 표기용).
_DATASET_LABEL = {
    "garden": "농사로 텃밭가꾸기",
    "cultivation": "농사로 작물재배정보",
    "monthtech": "이달의 농업기술",
    "ncpms": "병해충정보(NCPMS)",
    "general": "표준 재배지식",
    "_common": "텃밭 공통 가이드",
}
_DATASET_ORDER = ("garden", "cultivation", "monthtech", "ncpms", "general", "_common")


async def _rag_context(slugs: list[str], query: str) -> tuple[str, list[str]]:
    """RAG 근거 텍스트 + 실제 사용된 농사로 데이터셋 라벨(출처) 반환."""
    pairs: list[tuple[str, str]] = []  # (청크, kind)
    if slugs:
        # 특정 작물이 잡히면 그 작물 위주 + 공통 가이드.
        for slug in slugs[:_MAX_CROPS]:
            pairs.extend(
                await rag.retrieve_boosted(
                    slug, query, k=_RAG_K, boost={"garden": 0.1}, with_meta=True
                )
            )
        for c in await rag.retrieve("_common", query, k=_COMMON_K):
            pairs.append((c, "_common"))
    else:
        # 작물이 특정되지 않으면 전 작물 임베딩에서 전역 검색(모든 작물정보 활용).
        pairs.extend(await rag.retrieve_global(query, k=_GLOBAL_K, with_meta=True))
    text = "\n".join(f"· {c.strip()}" for c, _ in pairs if c.strip())[:_MAX_CTX_CHARS]
    kinds = {k for _, k in pairs}
    labels = [_DATASET_LABEL[k] for k in _DATASET_ORDER if k in kinds]
    return text, labels


def _chips(context: dict | None, slugs: list[str]) -> list[str]:
    has_reco = bool(context and context.get("recommendations"))
    return AFTER_RECO_CHIPS if (has_reco or slugs) else STARTER_CHIPS


async def answer(messages: list[ChatMessage], context: dict | None) -> ChatResponse:
    query = _last_user(messages)
    slugs = detect_crops(query, context)
    chips = _chips(context, slugs)
    sources = [
        ChatSource(crop_id=s, name=matrix.get_crop(s)["name"]) for s in slugs if matrix.get_crop(s)
    ]

    client = _get_client()
    if client is None:
        return ChatResponse(
            answer="지금은 AI 답변을 켤 수 없어요(키 미설정). 아래 버튼으로 시작해 보세요.",
            chips=chips,
            sources=sources,
        )

    cards = (
        "\n".join(filter(None, (_crop_card(s) for s in slugs[:_MAX_CROPS]))) or "(특정 작물 미지정)"
    )
    # 작물이 잡히면 그 작물 위주, 안 잡히면 전 작물 임베딩에서 전역 검색(_rag_context 내부 처리).
    rag_ctx, data_sources = await _rag_context(slugs, query)
    reco_ctx = ""
    if context and context.get("recommendations"):
        names = [
            r.get("name") or r.get("crop_id")
            for r in context["recommendations"]
            if isinstance(r, dict)
        ]
        reco_ctx = f"\n\n[사용자 추천 맥락]\n추천된 작물: {', '.join(filter(None, names))}"

    rag_block = rag_ctx or "(검색된 재배지식 없음 — 일반 텃밭 상식으로 신중히, 시기는 단정 말 것)"
    context_block = f"[작물 데이터]\n{cards}\n\n[참고지식]\n{rag_block}{reco_ctx}"

    llm_messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYS},
        {"role": "system", "content": context_block},
    ]
    for m in messages[-8:]:  # 최근 대화만
        role = m.role if m.role in ("user", "assistant") else "user"
        llm_messages.append({"role": role, "content": m.content})

    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=llm_messages,
            temperature=0.5,
            max_tokens=600,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:
        text = ""

    if not text:
        text = "죄송해요, 지금 답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요."
    return ChatResponse(
        answer=text, chips=chips, sources=sources, dataSources=data_sources
    )
