"""旧 STT 要約（people_memory 上）の掃除スクリプト。

A案適用前（commit 549bca7 以前）は STT 要約を ChromaDB の `stt_summaries` と
`people_memory` の両方に保存していた。A案適用後は `stt_summaries` のみに
集約しているため、過去に `people_memory` に流れ込んだ STT 要約
（metadata.source == "stt"）は重複した残骸となる。本スクリプトはそれを抽出して
削除する。

判定基準:
  metadata.source == "stt"

デフォルトは dry-run（件数とサンプルを表示するのみ）。`--apply` を付けたときだけ
実際に削除する。

Pi 上で実行する場合:
    docker exec secretary-bot python3 scripts/cleanup_stt_in_people_memory.py
    docker exec secretary-bot python3 scripts/cleanup_stt_in_people_memory.py --apply

ローカルで実行する場合:
    python scripts/cleanup_stt_in_people_memory.py --path /path/to/chromadb
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# プロジェクトルートを sys.path に通して src.* を import 可能にする
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import chromadb
from chromadb.config import Settings

COLLECTION = "people_memory"
DEFAULT_PATH = "/app/data/chromadb"
SAMPLE_LIMIT = 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", default=DEFAULT_PATH, help=f"ChromaDB 永続化パス（既定: {DEFAULT_PATH}）")
    parser.add_argument("--apply", action="store_true", help="実際に削除する（指定しなければ dry-run）")
    args = parser.parse_args()

    client = chromadb.PersistentClient(path=args.path, settings=Settings(anonymized_telemetry=False))

    try:
        col = client.get_collection(COLLECTION)
    except Exception as e:
        print(f"[ERROR] collection '{COLLECTION}' を開けません: {e}", file=sys.stderr)
        return 1

    total = col.count()
    print(f"people_memory total entries: {total}")

    # source == "stt" を抽出
    try:
        results = col.get(where={"source": "stt"}, include=["documents", "metadatas"])
    except Exception as e:
        print(f"[ERROR] get(where=...) に失敗: {e}", file=sys.stderr)
        return 1

    ids = results.get("ids", []) or []
    docs = results.get("documents", []) or []
    metas = results.get("metadatas", []) or []
    hit = len(ids)
    print(f"matched (source=stt): {hit}")

    if hit == 0:
        print("削除対象なし。終了します。")
        return 0

    # 期間サマリ
    starts = [m.get("period_start", "") for m in metas if m]
    ends = [m.get("period_end", "") for m in metas if m]
    if any(starts):
        print(f"period_start range: {min(s for s in starts if s)} ～ {max(s for s in starts if s)}")
    if any(ends):
        print(f"period_end   range: {min(e for e in ends if e)} ～ {max(e for e in ends if e)}")

    # サンプル表示
    print(f"\n--- sample (up to {SAMPLE_LIMIT}) ---")
    for i in range(min(SAMPLE_LIMIT, hit)):
        text = (docs[i] or "").replace("\n", " ")
        snippet = text[:120] + ("…" if len(text) > 120 else "")
        m = metas[i] or {}
        print(f"[{ids[i]}] saved_at={m.get('saved_at', '?')} period={m.get('period_start', '?')} -> {m.get('period_end', '?')}")
        print(f"  {snippet}")

    if not args.apply:
        print("\n[DRY-RUN] --apply を付けて再実行すると上記 {n} 件を削除します。".format(n=hit))
        return 0

    # 実削除
    try:
        col.delete(ids=ids)
    except Exception as e:
        print(f"[ERROR] delete に失敗: {e}", file=sys.stderr)
        return 1

    after = col.count()
    print(f"\n[APPLIED] 削除完了: {hit} 件削除（people_memory: {total} -> {after}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
