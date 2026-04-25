"""WorkflowManager — プリセット(ComfyUI API Format)の読み込み・差し込み・依存抽出。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from src.errors import ValidationError
from src.logger import get_logger
from src.units.image_gen.models import DEFAULT_PARAMS, WorkflowRequirements

log = get_logger(__name__)

_PRESETS_DIR = os.path.join(os.path.dirname(__file__), "presets")
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

_DEFAULT_META: dict[str, Any] = {
    "description": "",
    "category": "t2i",
    "main_pc_only": False,
    "default_timeout_sec": 300,
}


class WorkflowManager:
    """プリセット JSON の読み込み・差し込み・DB への登録を担う。

    - presets/*.json は初期シード用（`_meta` キーにメタデータを同梱）。
    - 既に DB に同名 row があれば上書きしない（WebGUI 編集分を温存）。
    - `register_workflow()` は WebGUI からの登録/更新にも使う共通 I/F。
    """

    def __init__(self, bot):
        self.bot = bot
        self._cache: dict[str, dict] = {}  # name -> raw workflow json

    # --- 起動時ローダ ---

    async def sync_presets_to_db(self) -> None:
        """presets/*.json をシード登録する（既存行はスキップ）。"""
        if not os.path.isdir(_PRESETS_DIR):
            log.warning("presets dir not found: %s", _PRESETS_DIR)
            return
        for fname in sorted(os.listdir(_PRESETS_DIR)):
            if not fname.endswith(".json"):
                continue
            name = fname[:-5]
            existing = await self.bot.database.workflow_get_by_name(name)
            if existing:
                continue
            path = os.path.join(_PRESETS_DIR, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                log.error("Failed to load preset %s: %s", name, e)
                continue
            meta = dict(_DEFAULT_META)
            if isinstance(raw, dict) and isinstance(raw.get("_meta"), dict):
                meta.update(raw.pop("_meta"))
            try:
                await self.register_workflow(
                    name=name,
                    workflow_json=raw,
                    description=meta.get("description") or None,
                    category=str(meta.get("category") or "t2i"),
                    main_pc_only=bool(meta.get("main_pc_only", False)),
                    default_timeout_sec=int(meta.get("default_timeout_sec", 300)),
                )
                log.info("Preset seeded: %s", name)
            except Exception as e:
                log.error("Preset seed failed for %s: %s", name, e)

    async def register_workflow(
        self, *, name: str, workflow_json: dict,
        description: str | None = None, category: str = "t2i",
        main_pc_only: bool = False, default_timeout_sec: int = 300,
    ) -> int:
        """WebGUI / シード共通の登録ヘルパ。依存抽出 + upsert + キャッシュ更新。"""
        if not isinstance(workflow_json, dict) or not workflow_json:
            raise ValidationError("workflow_json must be a non-empty object")
        # `_meta` が混入していたら落とす（インポート直後に許容）
        clean = {k: v for k, v in workflow_json.items() if k != "_meta"}
        req = self.extract_requirements(clean)
        wid = await self.bot.database.workflow_upsert(
            name=name,
            description=description,
            category=category or "t2i",
            workflow_json=json.dumps(clean, ensure_ascii=False),
            required_nodes=json.dumps(req.nodes, ensure_ascii=False),
            required_models=json.dumps(req.models, ensure_ascii=False),
            required_loras=json.dumps(req.loras, ensure_ascii=False),
            main_pc_only=bool(main_pc_only),
            default_timeout_sec=int(default_timeout_sec),
        )
        self._cache[name] = clean
        return int(wid)

    def invalidate_cache(self, name: str | None = None) -> None:
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)

    # --- 解析 ---

    def extract_requirements(self, workflow_json: dict) -> WorkflowRequirements:
        """使用ノード・モデル・LoRA・プレースホルダを抽出する。"""
        nodes: list[str] = []
        models: list[dict[str, str]] = []
        loras: list[dict[str, str]] = []
        placeholders: set[str] = set()

        for _node_id, node in workflow_json.items():
            if not isinstance(node, dict):
                continue
            ctype = node.get("class_type", "")
            if ctype and ctype not in nodes:
                nodes.append(ctype)

            inputs = node.get("inputs", {}) or {}
            # モデル系ノードの参照ファイル名
            if ctype == "CheckpointLoaderSimple":
                v = inputs.get("ckpt_name")
                if isinstance(v, str):
                    models.append({"type": "checkpoints", "filename": v})
            elif ctype == "LoraLoader":
                v = inputs.get("lora_name")
                if isinstance(v, str):
                    loras.append({"type": "loras", "filename": v})
            elif ctype == "VAELoader":
                v = inputs.get("vae_name")
                if isinstance(v, str):
                    models.append({"type": "vae", "filename": v})

            # プレースホルダ抽出（文字列値のみ）
            for _k, v in inputs.items():
                if isinstance(v, str):
                    for m in _PLACEHOLDER_RE.findall(v):
                        placeholders.add(m)

        return WorkflowRequirements(
            nodes=nodes, models=models, loras=loras,
            placeholders=sorted(placeholders),
        )

    # --- 差し込み ---

    async def compose_and_resolve(
        self, name: str, params: dict[str, Any],
        *,
        section_ids: list[int] | None = None,
        user_positive: str | None = None,
        user_negative: str | None = None,
        user_position: str = "tail",
    ) -> tuple[dict, dict]:
        """セクション合成 → placeholder 差し込み。

        section_ids が空 or None の場合は、従来通り params["POSITIVE"] / ["NEGATIVE"] を
        そのまま使う。セクションを指定する場合は合成結果で POSITIVE/NEGATIVE を上書きする。

        戻り値: (resolved_workflow_json, compose_info)
            compose_info には warnings / positive / negative / sections / user_position
            を含み、ジョブ行の sections_json として保存する用途にも使える。
        """
        from src.units.image_gen.section_composer import compose_prompt

        compose_info: dict[str, Any] = {
            "section_ids": list(section_ids or []),
            "user_position": user_position,
            "warnings": [],
            "positive": None,
            "negative": None,
        }

        merged = dict(params or {})
        if section_ids:
            sections = await self.bot.database.section_get_many(section_ids)
            # user_positive/negative は params["POSITIVE"] をデフォルトに
            up = user_positive if user_positive is not None else merged.get("POSITIVE")
            un = user_negative if user_negative is not None else merged.get("NEGATIVE")
            result = compose_prompt(
                sections, user_positive=up, user_negative=un,
                user_position=user_position,
            )
            merged["POSITIVE"] = result.positive
            merged["NEGATIVE"] = result.negative
            compose_info["warnings"] = list(result.warnings)
            compose_info["positive"] = result.positive
            compose_info["negative"] = result.negative
            compose_info["dropped"] = list(result.dropped)
        else:
            compose_info["positive"] = merged.get("POSITIVE")
            compose_info["negative"] = merged.get("NEGATIVE")

        resolved = await self.resolve(name, merged)
        return resolved, compose_info

    async def resolve(self, name: str, params: dict[str, Any]) -> dict:
        """プリセット名とパラメータから、実行用 workflow JSON を返す。

        - params は大文字キー推奨（{{POSITIVE}} に対して "POSITIVE"）
        - 欠けているプレースホルダは DEFAULT_PARAMS → ValidationError の順で補完
        - params["__LORA_OVERRIDES__"] が在れば LoraLoader ノードを書き換える
          （disabled は削除して再配線、enabled は strength を上書き）
        """
        wf = await self._get_workflow(name)
        if wf is None:
            raise ValidationError(f"Workflow '{name}' not found")

        resolved_params: dict[str, Any] = {}
        resolved_params.update(DEFAULT_PARAMS)
        for k, v in params.items():
            if v is None:
                continue
            resolved_params[str(k).upper()] = v

        # LoRA override（プレースホルダ置換より先に処理）
        overrides = resolved_params.pop("__LORA_OVERRIDES__", None)
        wf_to_use = wf
        if overrides:
            try:
                wf_to_use = apply_lora_overrides(wf, overrides)
            except Exception as e:
                log.warning("apply_lora_overrides failed: %s", e)

        # 深いコピーしつつプレースホルダ置換
        return _substitute(wf_to_use, resolved_params)

    def list_lora_nodes(self, workflow_json: dict) -> list[dict]:
        """workflow JSON から LoraLoader ノード一覧を返す（UI 表示用）。"""
        out: list[dict] = []
        for node_id, node in workflow_json.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") != "LoraLoader":
                continue
            inputs = node.get("inputs", {}) or {}
            out.append({
                "node_id": str(node_id),
                "lora_name": inputs.get("lora_name") if isinstance(inputs.get("lora_name"), str) else None,
                "strength_model": inputs.get("strength_model"),
                "strength_clip": inputs.get("strength_clip"),
                "title": (node.get("_meta") or {}).get("title") if isinstance(node.get("_meta"), dict) else None,
            })
        return out

    async def list_lora_nodes_by_name(self, name: str) -> list[dict]:
        wf = await self._get_workflow(name)
        if wf is None:
            return []
        return self.list_lora_nodes(wf)

    async def _get_workflow(self, name: str) -> dict | None:
        if name in self._cache:
            return self._cache[name]
        # DB から復元
        row = await self.bot.database.workflow_get_by_name(name)
        if not row:
            return None
        try:
            wf = json.loads(row["workflow_json"])
            self._cache[name] = wf
            return wf
        except Exception as e:
            log.error("Failed to decode workflow %s: %s", name, e)
            return None


def apply_lora_overrides(workflow: dict, overrides: list[dict]) -> dict:
    """LoraLoader ノードの enabled/strength/lora_name オーバーライドおよび追加挿入を適用する。

    overrides の要素は次の2種類:
      1) 既存ノード操作: {"node_id": str, "enabled": bool?, "strength": float?, "lora_name": str?}
         - enabled=False で workflow から削除、参照は上流へ付け替え（バイパス）
         - strength 指定で strength_model/clip を上書き
         - lora_name 指定でファイル名を差し替え
      2) 新規追加: {"op": "add", "lora_name": str, "strength": float?, "enabled": bool?}
         - 既存 LoraLoader チェーンの末尾（無ければ CheckpointLoaderSimple）の後ろに連結
         - 末尾の MODEL(0)/CLIP(1) 出力を参照していた下流ノードを、追加した最後の LoRA に張り替え
         - enabled=False の add は無視（追加しない）

    workflow 自体は破壊せず deep copy 上で操作する。
    """
    if not overrides:
        return workflow

    disabled: set[str] = set()
    strength_map: dict[str, float] = {}
    lora_name_map: dict[str, str] = {}
    add_entries: list[dict] = []
    for ov in overrides:
        if not isinstance(ov, dict):
            continue
        if ov.get("op") == "add":
            if ov.get("enabled") is False:
                continue
            ln = ov.get("lora_name")
            if not isinstance(ln, str) or not ln.strip():
                continue
            try:
                strength = float(ov.get("strength", 0.6))
            except (TypeError, ValueError):
                strength = 0.6
            add_entries.append({"lora_name": ln.strip(), "strength": strength})
            continue
        nid = str(ov.get("node_id") or "")
        if not nid:
            continue
        node = workflow.get(nid)
        if not isinstance(node, dict) or node.get("class_type") != "LoraLoader":
            continue
        if ov.get("enabled") is False:
            disabled.add(nid)
            continue
        if ov.get("strength") is not None:
            try:
                strength_map[nid] = float(ov["strength"])
            except (TypeError, ValueError):
                pass
        if isinstance(ov.get("lora_name"), str) and ov["lora_name"].strip():
            lora_name_map[nid] = ov["lora_name"].strip()

    # 削除ノードの入力元を記録（MODEL=output 0, CLIP=output 1）
    bypass_map: dict[str, dict[str, Any]] = {}
    for nid in disabled:
        node = workflow[nid]
        inputs = node.get("inputs", {}) or {}
        bypass_map[nid] = {
            0: inputs.get("model"),   # output index 0 → input.model
            1: inputs.get("clip"),    # output index 1 → input.clip
        }

    def _redirect(ref_id: str, out_idx: int, seen: set[str]) -> list:
        """disabled なノードへの参照を、上流の生きているノードへチェーン解決。"""
        if ref_id not in disabled or ref_id in seen:
            return [ref_id, out_idx]
        seen.add(ref_id)
        src = bypass_map.get(ref_id, {}).get(out_idx)
        if not (isinstance(src, list) and len(src) == 2 and isinstance(src[0], str)):
            return [ref_id, out_idx]   # フォールバック（壊れた workflow）
        return _redirect(src[0], int(src[1]), seen)

    out: dict[str, Any] = {}
    for nid, node in workflow.items():
        if nid in disabled:
            continue
        new_node = json.loads(json.dumps(node))   # deep copy
        # 強度・lora_name 上書き
        if new_node.get("class_type") == "LoraLoader":
            inputs = new_node.setdefault("inputs", {})
            if nid in strength_map:
                inputs["strength_model"] = strength_map[nid]
                inputs["strength_clip"] = strength_map[nid]
            if nid in lora_name_map:
                inputs["lora_name"] = lora_name_map[nid]
        # 入力リンクの再配線
        inputs = new_node.get("inputs", {}) or {}
        for k, v in list(inputs.items()):
            if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)):
                continue
            inputs[k] = _redirect(v[0], int(v[1]), set())
        out[nid] = new_node

    if add_entries:
        out = _append_added_loras(out, add_entries)
    return out


def _find_lora_chain_tail(workflow: dict) -> tuple[str | None, str | None]:
    """LoraLoader チェーンの末尾ノードIDを返す。

    末尾 = MODEL/CLIP 出力が他の LoraLoader 以外（KSampler / CLIPTextEncode 等）
    から参照されているノード。LoraLoader が無ければ CheckpointLoaderSimple を返す。

    戻り値: (tail_node_id, tail_class_type) もしくは (None, None)
    """
    lora_ids = {
        nid for nid, n in workflow.items()
        if isinstance(n, dict) and n.get("class_type") == "LoraLoader"
    }
    if not lora_ids:
        for nid, n in workflow.items():
            if isinstance(n, dict) and n.get("class_type") == "CheckpointLoaderSimple":
                return nid, "CheckpointLoaderSimple"
        return None, None
    # 他 LoraLoader の input から参照されているノード集合
    referenced_by_lora = set()
    for nid, n in workflow.items():
        if not isinstance(n, dict) or n.get("class_type") != "LoraLoader":
            continue
        for inp in (n.get("inputs", {}) or {}).values():
            if isinstance(inp, list) and len(inp) == 2 and isinstance(inp[0], str):
                referenced_by_lora.add(inp[0])
    tails = lora_ids - referenced_by_lora
    if not tails:
        return None, None
    # 複数末尾は通常想定外。決定的に1つを選ぶ。
    return sorted(tails)[0], "LoraLoader"


def _alloc_node_id(workflow: dict, prefix: str) -> str:
    i = 1
    while True:
        nid = f"{prefix}_{i}"
        if nid not in workflow:
            return nid
        i += 1


def _append_added_loras(workflow: dict, adds: list[dict]) -> dict:
    """LoraLoader チェーン末尾に新ノードを連結し、下流参照を張り替える。"""
    tail_id, _ = _find_lora_chain_tail(workflow)
    if tail_id is None:
        log.warning("apply_lora_overrides: cannot find chain tail; skip add")
        return workflow

    out = json.loads(json.dumps(workflow))
    new_ids: list[str] = []
    current_model = [tail_id, 0]
    current_clip = [tail_id, 1]
    for i, add in enumerate(adds):
        new_id = _alloc_node_id(out, "added_lora")
        out[new_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": str(add["lora_name"]),
                "strength_model": float(add["strength"]),
                "strength_clip": float(add["strength"]),
                "model": list(current_model),
                "clip": list(current_clip),
            },
            "_meta": {"title": f"Added LoRA {i + 1}"},
        }
        new_ids.append(new_id)
        current_model = [new_id, 0]
        current_clip = [new_id, 1]

    last_id = new_ids[-1]
    # 末尾ノードの MODEL(0) / CLIP(1) を参照する下流ノードを最終ノードへ張り替え。
    # CheckpointLoaderSimple の VAE(2) は触らない。新ノード自身も除外する。
    skip = set(new_ids) | {tail_id}
    for nid, n in out.items():
        if nid in skip or not isinstance(n, dict):
            continue
        inputs = n.get("inputs", {}) or {}
        for k, v in list(inputs.items()):
            if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)):
                continue
            if v[0] == tail_id and int(v[1]) in (0, 1):
                inputs[k] = [last_id, int(v[1])]
    return out


def _substitute(obj: Any, params: dict[str, Any]) -> Any:
    """workflow JSON の {{VAR}} を再帰置換する。
    値が文字列全体が {{VAR}} だけなら型保持（int/float/bool も通す）。
    """
    if isinstance(obj, dict):
        return {k: _substitute(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, params) for v in obj]
    if isinstance(obj, str):
        m = _PLACEHOLDER_RE.fullmatch(obj)
        if m:
            key = m.group(1)
            if key not in params:
                raise ValidationError(f"Placeholder {{{{{key}}}}} not supplied")
            return params[key]
        # 部分置換（文字列内に埋め込み）
        def _repl(m: re.Match) -> str:
            key = m.group(1)
            if key not in params:
                raise ValidationError(f"Placeholder {{{{{key}}}}} not supplied")
            return str(params[key])
        return _PLACEHOLDER_RE.sub(_repl, obj)
    return obj
