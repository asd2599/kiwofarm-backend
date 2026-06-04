"""v2(KAMIS 코드 키) 로컬 데이터 → v3(crops_master 40종 슬러그) 마이그레이션.

대상:
  data/embeddings/  {KAMIS코드|코드__품종|x_*}.{kind}.npy/json
                    → 40종 매핑분은 {슬러그}.{kind} 로 리네이밍(충돌 시 병합),
                      40종 외는 삭제 (git 추적 중이라 복구 가능)
  data/farminfo/    by_crop/{키}.json 동일 처리 + manifest.json 재생성

원천(raw)은 보존: data/weekfarm/ (작물 무관 회보), data/raw_farmwork.json 등.
재임베딩 없음 — 벡터·청크를 파일 단위로 옮기기만 한다.

실행:
    uv run python scripts/migrate_data_v3.py --dry-run   # 계획만 출력
    uv run python scripts/migrate_data_v3.py             # 실행
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.planting import matrix  # noqa: E402
from app.data.crop_ids import KAMIS_TO_SLUG  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EMBED_DIR = ROOT / "data" / "embeddings"
FARMINFO_DIR = ROOT / "data" / "farminfo"


def _slug_of(key: str) -> str | None:
    """파일 키(코드, 코드__품종, x_*, 이미 슬러그) → 슬러그 or None(=삭제 대상)."""
    if matrix.get_crop(key) is not None:
        return key
    base = key.split("__")[0]
    return KAMIS_TO_SLUG.get(base) or KAMIS_TO_SLUG.get(key)


def _merge_embed(src_json: Path, src_npy: Path, dst_json: Path, dst_npy: Path) -> None:
    """리네이밍 충돌 시 청크·벡터를 기존 파일 뒤에 이어붙인다."""
    dst_meta = json.loads(dst_json.read_text(encoding="utf-8"))
    src_meta = json.loads(src_json.read_text(encoding="utf-8"))
    dst_meta["chunks"] = dst_meta.get("chunks", []) + src_meta.get("chunks", [])
    src_sources = src_meta.get("source", "")
    if src_sources and src_sources not in dst_meta.get("source", ""):
        dst_meta["source"] = f"{dst_meta.get('source', '')}+{src_sources}"
    merged = np.vstack([np.load(dst_npy), np.load(src_npy)])
    np.save(dst_npy, merged)
    dst_json.write_text(json.dumps(dst_meta, ensure_ascii=False), encoding="utf-8")
    src_json.unlink()
    src_npy.unlink()


def migrate_embeddings(dry: bool) -> tuple[int, int, int]:
    renamed = merged = deleted = 0
    # 키 단위로 처리 (json+npy 쌍)
    keys: dict[str, list[str]] = {}  # key → [kind, ...]
    for f in sorted(EMBED_DIR.glob("*.json")):
        key, kind = f.name.rsplit(".json", 1)[0].rsplit(".", 1)
        keys.setdefault(key, []).append(kind)

    for key, kinds in keys.items():
        slug = _slug_of(key)
        for kind in kinds:
            src_json = EMBED_DIR / f"{key}.{kind}.json"
            src_npy = EMBED_DIR / f"{key}.{kind}.npy"
            if slug is None:
                print(f"  delete  {key}.{kind}")
                if not dry:
                    src_json.unlink(missing_ok=True)
                    src_npy.unlink(missing_ok=True)
                deleted += 1
                continue
            if slug == key:
                continue  # 이미 슬러그
            dst_json = EMBED_DIR / f"{slug}.{kind}.json"
            dst_npy = EMBED_DIR / f"{slug}.{kind}.npy"
            if dst_json.exists():
                print(f"  merge   {key}.{kind} -> {slug}.{kind}")
                if not dry:
                    _merge_embed(src_json, src_npy, dst_json, dst_npy)
                merged += 1
            else:
                print(f"  rename  {key}.{kind} -> {slug}.{kind}")
                if not dry:
                    src_json.rename(dst_json)
                    src_npy.rename(dst_npy)
                renamed += 1
    return renamed, merged, deleted


def migrate_farminfo(dry: bool) -> tuple[int, int]:
    by_crop = FARMINFO_DIR / "by_crop"
    kept = deleted = 0
    entries: list[dict] = []
    for f in sorted(by_crop.glob("*.json")):
        key = f.stem
        slug = _slug_of(key)
        if slug is None:
            print(f"  delete  by_crop/{f.name}")
            if not dry:
                f.unlink()
            deleted += 1
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        dst = by_crop / f"{slug}.json"
        if slug != key:
            print(f"  rename  by_crop/{f.name} -> {slug}.json")
            if not dry:
                if dst.exists():  # 병합 (풋고추+? 등)
                    cur = json.loads(dst.read_text(encoding="utf-8"))
                    cur["passages"] = cur.get("passages", []) + data.get("passages", [])
                    cur["count"] = len(cur["passages"])
                    dst.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
                else:
                    data["slug"] = slug
                    data["count"] = len(data.get("passages", []))
                    dst.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                f.unlink()
        kept += 1
        if not dry and dst.exists():
            cur = json.loads(dst.read_text(encoding="utf-8"))
            crop = matrix.get_crop(slug)
            entries.append(
                {
                    "key": slug,
                    "cropName": crop["name"] if crop else cur.get("cropName", ""),
                    "count": cur.get("count", 0),
                }
            )

    if not dry:
        manifest = {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "idScheme": "crops_master slug (v3)",
            "cropCount": len(entries),
            "totalPassages": sum(e["count"] for e in entries),
            "crops": sorted(entries, key=lambda e: e["key"]),
        }
        (FARMINFO_DIR / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print("  manifest.json 재생성")
    return kept, deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="v2 → v3 작물 키 마이그레이션")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry = args.dry_run

    print(f"=== embeddings ({'dry-run' if dry else '실행'}) ===")
    r, m, d = migrate_embeddings(dry)
    print(f"embeddings: rename {r}, merge {m}, delete {d}")

    print(f"=== farminfo ({'dry-run' if dry else '실행'}) ===")
    k, fd = migrate_farminfo(dry)
    print(f"farminfo: keep {k}, delete {fd}")


if __name__ == "__main__":
    main()
