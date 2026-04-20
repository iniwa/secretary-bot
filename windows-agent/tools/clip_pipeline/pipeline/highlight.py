"""
ハイライト判定モジュール
Ollama REST API (Gemma 4) を使用して、文字起こし+音声特徴+感情からハイライト候補を抽出
"""

import json
import os
import time
from bisect import bisect_left

import requests

from .config import OLLAMA_URL, OLLAMA_REQUEST_DELAY, LLM_CHUNK_SIZE, HIGHLIGHT_TOP_N


SYSTEM_PROMPT = """あなたはゲーム配信の切り抜き担当編集者です。
以下はゲーム配信の文字起こしです（タイムスタンプ付き）。
音声の特徴量（音量RMS、ピッチ、有声フレーム比率）や感情ラベルも付与されています。

視聴者が見て面白い・盛り上がると感じそうな箇所を{top_n_text}、タイムスタンプで教えてください。
理由も1行で添えてください。

**重要: 各クリップは30秒〜180秒の長さにしてください。**
短い盛り上がりは前後の文脈を含めてまとめ、1つの見応えのあるシーンにしてください。
1秒や数秒の切り抜きではなく、視聴者が楽しめるまとまったシーンを切り出してください。

判定基準:
- テキストの内容（面白い発言、リアクション、ハプニング）
- 音量が高い箇所（興奮、叫び）
- ピッチが高い箇所（驚き、喜び）
- 有声フレーム比率が高い箇所（活発に喋っている）
- 感情ラベルが強い箇所（joy, surprise, anger等）
- 近接する盛り上がりは1つのクリップにまとめること

必ず以下のJSON形式で返してください。JSON以外のテキストは含めないでください。
[
  {{"start": 120.0, "end": 180.0, "reason": "敵を倒して大声を上げた"}},
  ...
]"""


def _build_segment_text(transcript: list[dict], audio_features: list[dict], emotions: list[dict]) -> list[str]:
    """セグメントデータを統合してテキストチャンクに変換"""
    # 音声特徴量と感情をタイムスタンプで引けるようにする
    audio_by_time = {af["start"]: af for af in audio_features}
    audio_keys = sorted(audio_by_time.keys())

    emotion_by_time = {em["start"]: em for em in emotions}
    emotion_keys = sorted(emotion_by_time.keys())

    lines = []
    for seg in transcript:
        line = f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}"

        # 最も近い音声特徴量を検索（二分探索）
        closest_audio = _find_closest(audio_keys, audio_by_time, seg["start"])
        if closest_audio:
            vr = closest_audio.get('voicing_ratio', closest_audio.get('speech_rate', '?'))
            line += f" | RMS={closest_audio['rms']}, pitch={closest_audio['pitch_mean']}Hz, voicing_ratio={vr}"

        # 最も近い感情ラベルを検索（二分探索）
        closest_emotion = _find_closest(emotion_keys, emotion_by_time, seg["start"])
        if closest_emotion:
            line += f" | emotion={closest_emotion['emotion']}({closest_emotion.get('confidence', '?')})"

        lines.append(line)

    # チャンク分割
    chunks = []
    for i in range(0, len(lines), LLM_CHUNK_SIZE):
        chunk = "\n".join(lines[i:i + LLM_CHUNK_SIZE])
        chunks.append(chunk)

    return chunks


def _find_closest(sorted_keys: list, by_time: dict, target: float) -> dict | None:
    """最も近いタイムスタンプのエントリを返す（二分探索 O(log n)）"""
    if not sorted_keys:
        return None
    idx = bisect_left(sorted_keys, target)
    candidates = []
    if idx > 0:
        candidates.append(sorted_keys[idx - 1])
    if idx < len(sorted_keys):
        candidates.append(sorted_keys[idx])
    closest = min(candidates, key=lambda k: abs(k - target))
    if abs(closest - target) < 10:  # 10秒以内なら対応とみなす
        return by_time[closest]
    return None


def _salvage_json_array(text: str, log=print) -> list[dict]:
    """壊れたJSON配列から有効な {"start":..., "end":..., "reason":...} を正規表現で救出"""
    import re
    pattern = re.compile(
        r'\{\s*"start"\s*:\s*([0-9.]+)\s*,\s*"end"\s*:\s*([0-9.]+)\s*,\s*"reason"\s*:\s*"([^"]*)"'
    )
    results = []
    for m in pattern.finditer(text):
        try:
            results.append({
                "start": float(m.group(1)),
                "end": float(m.group(2)),
                "reason": m.group(3),
            })
        except (ValueError, IndexError):
            continue
    return results


def _query_ollama(model: str, prompt: str, top_n: int, log=print) -> list[dict]:
    """Ollama REST APIにクエリ送信"""
    top_n_text = f"最大{top_n}件" if top_n > 0 else "できるだけ多く"
    system = SYSTEM_PROMPT.format(top_n_text=top_n_text)
    log(f"  Ollama リクエスト送信中（モデル: {model}, プロンプト長: {len(prompt)}文字）...")
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "think": False,
        },
        timeout=600,
    )
    log(f"  Ollama レスポンス: HTTP {resp.status_code}")
    resp.raise_for_status()
    raw_body = resp.json()
    content = raw_body["message"]["content"]
    log(f"  Ollama 生レスポンス（先頭500文字）: {content[:500]}")

    # Markdownコードブロック除去（Gemma 4 が ```json ... ``` で囲む場合がある）
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        # 先頭の ```json と末尾の ``` を除去
        lines = lines[1:]  # ```json 行を除去
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)

    # JSONパース（配列またはオブジェクト内の配列）
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as e:
        log(f"  JSON パース失敗: {e}")
        log(f"  部分サルベージを試行...")
        # 壊れたJSON配列から有効なオブジェクトを救出
        salvaged = _salvage_json_array(stripped, log)
        if salvaged:
            log(f"  サルベージ成功: {len(salvaged)}件を救出")
            return salvaged
        log(f"  サルベージ失敗。レスポンス全文: {content}")
        return []

    if isinstance(parsed, list):
        log(f"  パース結果: 配列 {len(parsed)}件")
        return parsed
    if isinstance(parsed, dict):
        log(f"  パース結果: オブジェクト（キー: {list(parsed.keys())}）")
        for k, v in parsed.items():
            if isinstance(v, list):
                log(f"  キー '{k}' から配列 {len(v)}件を取得")
                return v
    log(f"  警告: ハイライト配列が見つかりませんでした。パース結果の型: {type(parsed).__name__}")
    return []


def detect_highlights(
    transcript: list[dict],
    audio_features: list[dict],
    emotions: list[dict],
    model: str,
    output_dir: str,
    top_n: int = None,
    log=print,
    progress_callback=None,
) -> list[dict]:
    """
    ハイライト候補を検出する。

    Args:
        transcript: 文字起こし結果
        audio_features: 音声特徴量
        emotions: 感情分析結果
        model: Ollamaモデル名
        output_dir: 出力ディレクトリ
        top_n: 最大候補数（0 or None = 無制限）
        log: ログ出力関数

    Returns:
        [{"start": 120.0, "end": 180.0, "reason": "..."}, ...]
    """
    if top_n is None:
        top_n = HIGHLIGHT_TOP_N

    if not transcript:
        log("文字起こし結果がありません。ハイライト検出をスキップします。")
        return []

    chunks = _build_segment_text(transcript, audio_features, emotions)
    num_chunks = len(chunks)
    log(f"Ollamaにハイライト判定を依頼中...（{num_chunks}チャンク, 入力セグメント数: {len(transcript)}）")
    log(f"  音声特徴量: {len(audio_features)}件, 感情: {len(emotions)}件")

    # チャンクごとの top_n を均等配分（偏り防止）
    if top_n > 0 and num_chunks > 1:
        import math
        chunk_top_n = math.ceil(top_n / num_chunks)
    else:
        chunk_top_n = top_n

    all_highlights = []
    for i, chunk in enumerate(chunks):
        log(f"  チャンク {i + 1}/{num_chunks} を処理中（{len(chunk)}文字）...")
        if progress_callback:
            progress_callback(i, num_chunks, f"ハイライト判定 {i + 1}/{num_chunks}")
        try:
            results = _query_ollama(model, chunk, chunk_top_n, log=log)
            log(f"  チャンク {i + 1} 結果: {len(results)}件のハイライト")
            for j, r in enumerate(results):
                try:
                    dur = float(r.get('end', 0)) - float(r.get('start', 0))
                except (TypeError, ValueError):
                    dur = 0
                log(f"    [{j+1}] start={r.get('start')}, end={r.get('end')}, "
                    f"duration={dur:.1f}s, reason={r.get('reason', '(なし)')}")
            all_highlights.extend(results)
        except Exception as e:
            log(f"  チャンク {i + 1} でエラー: {e}")
            import traceback
            log(f"  トレースバック: {traceback.format_exc()}")

        if i < num_chunks - 1:
            time.sleep(OLLAMA_REQUEST_DELAY)

    log(f"全チャンク合計: {len(all_highlights)}件のハイライト候補")

    # top_n > 0 の場合のみ件数を絞る
    if top_n > 0:
        if len(all_highlights) > top_n:
            log(f"top_n={top_n} に絞り込み（{len(all_highlights)} → {top_n}件）")
        all_highlights = all_highlights[:top_n]

    # start順にソート
    all_highlights.sort(key=lambda h: h.get("start", 0))

    log(f"候補{len(all_highlights)}件を検出しました")
    for i, h in enumerate(all_highlights):
        log(f"  [{i+1}] {h.get('start', '?')}s - {h.get('end', '?')}s "
            f"(duration={h.get('end', 0) - h.get('start', 0):.1f}s) : {h.get('reason', '(なし)')}")

    highlights_path = os.path.join(output_dir, "highlights.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(highlights_path, "w", encoding="utf-8") as f:
        json.dump(all_highlights, f, ensure_ascii=False, indent=2)

    return all_highlights
