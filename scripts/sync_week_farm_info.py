"""농사로 주간농사정보(weekFarmInfo) 로컬 정리 스크립트.

농사로 weekFarmInfoList 를 페이지 순회로 전부(또는 --limit) 수집해
backend/data/weekfarm/index.json 으로 정리 저장한다. 선택적으로 최신 N건의
PDF 본문을 받아 텍스트(backend/data/weekfarm/text/{cntntsNo}.txt)로 추출한다.

실행:
    uv run python scripts/sync_week_farm_info.py                 # 메타 인덱스 전체
    uv run python scripts/sync_week_farm_info.py --limit 24      # 최신 24건만
    uv run python scripts/sync_week_farm_info.py --pdf-text 12   # + 최신 12건 PDF 텍스트
환경:
    .env 의 NONGSARO_API_KEY2 (주간농사정보 신청 키) 사용.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# scripts/ 직접 실행 대응
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import nongsaro_weekfarm as wf  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync_week_farm_info")

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "weekfarm"
INDEX_PATH = DATA_DIR / "index.json"
TEXT_DIR = DATA_DIR / "text"


def _to_record(info: wf.WeekFarmInfo) -> dict:
    return {
        "cntntsNo": info.cntnts_no,
        "subject": info.subject,
        "regDate": info.reg_date,
        "writer": info.writer,
        "hitCount": info.hit_count,
        "pdfUrl": info.pdf_url,
        "files": [
            {"name": f.name, "url": f.url, "seCode": f.se_code} for f in info.files
        ],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="농사로 주간농사정보 로컬 정리")
    parser.add_argument("--limit", type=int, default=None, help="최대 수집 건수(기본: 전체)")
    parser.add_argument("--num-rows", type=int, default=100, help="페이지당 건수(기본 100)")
    parser.add_argument(
        "--pdf-text", type=int, default=0, metavar="N",
        help="최신 N건의 PDF 본문을 텍스트로 추출 저장(기본 0=안 함)",
    )
    parser.add_argument("--out", type=Path, default=INDEX_PATH, help="인덱스 JSON 경로")
    args = parser.parse_args()

    log.info("주간농사정보 수집 시작 (limit=%s, num_rows=%d)", args.limit, args.num_rows)
    infos = await wf.fetch_all(max_items=args.limit, num_of_rows=args.num_rows)
    log.info("수집 완료: %d건", len(infos))

    records = [_to_record(i) for i in infos]
    payload = {
        "service": "weekFarmInfo",
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
        "count": len(records),
        "items": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("인덱스 저장: %s (%d건)", args.out, len(records))

    if args.pdf_text > 0:
        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        targets = infos[: args.pdf_text]
        log.info("PDF 본문 추출 시작: 최신 %d건", len(targets))
        ok = 0
        for info in targets:
            text = await wf.fetch_pdf_text(info)
            if not text:
                log.info("  skip cntnts=%s (PDF/텍스트 없음)", info.cntnts_no)
                continue
            (TEXT_DIR / f"{info.cntnts_no}.txt").write_text(text, encoding="utf-8")
            ok += 1
            log.info(
                "  saved cntnts=%s chars=%d (%s)",
                info.cntnts_no, len(text), info.subject[:30],
            )
        log.info("PDF 본문 추출 완료: %d/%d건", ok, len(targets))


if __name__ == "__main__":
    asyncio.run(main())
