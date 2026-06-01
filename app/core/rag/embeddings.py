"""OpenAI 임베딩 래퍼 (text-embedding-3-small, 1536차원).

summary.py 의 AsyncOpenAI 사용 패턴을 따른다. 키 없으면 명시적 에러로
호출자가 503 등으로 매핑할 수 있게 한다.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings

MODEL = "text-embedding-3-small"
DIM = 1536
_BATCH = 96  # OpenAI 임베딩 배치 상한 여유


class EmbeddingError(RuntimeError):
    """임베딩 호출 실패 (키 미설정·네트워크·쿼터 등)."""


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if not settings.openai_api_key:
        raise EmbeddingError("OPENAI_API_KEY 가 설정되지 않았습니다 (.env 확인)")
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """텍스트 리스트 → 임베딩 리스트(입력 순서 보존). 빈 입력은 빈 결과."""
    if not texts:
        return []
    client = _get_client()
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        batch = texts[i : i + _BATCH]
        try:
            resp = await client.embeddings.create(model=MODEL, input=batch)
        except Exception as e:  # noqa: BLE001 - 어떤 실패든 EmbeddingError 로 수렴
            raise EmbeddingError(f"임베딩 호출 실패: {e}") from e
        out.extend(d.embedding for d in resp.data)
    return out


async def embed_query(text: str) -> list[float]:
    result = await embed_texts([text])
    return result[0]
