"""WorkflowManager — プリセット(ComfyUI API Format)の読み込み・差し込み・依存抽出。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from src.errors import ValidationError
from src.logger import get_logger
from src.units.image_gen.models import WorkflowRequirements, DEFAULT_PARAMS

log = get_logger(__name__)

_PRESETS_DIR = os.path.join(os.path.dirname(__file__), "presets")
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

# プリセット → デフォルト timeout / main_pc_only 等のメタ
# 実運用では JSON と別ファイルで持つべきだが Phase1 はここに集約。
_PRESET_META: dict[str, dict[str, Any]] = {
    "t2i_base":   {"category": "t2i", "default_timeout_sec": 300,  "main_pc_only": False,
                   "description": "最小構成 t2i（1024x1024, 30 steps）"},
    "t2i_hires":  {"category": "t2i", "default_timeout_sec": 900,  "main_pc_only": False,
                   "description": "Hires.fix 付き t2i"},
    "t2i_lora_1": {"category": "t2i", "default_timeout_sec": 360,  "main_pc_only": False,
                   "description": "LoRA 1 枚適用 t2i"},
    "t2i_lora_2": {"category": "t2i", "default_timeout_sec": 420,  "main_pc_only": False,
                   "description": "LoRA 2 枚適用 t2i"},
    "t2i_lora_3": {"category": "t2i", "default_timeout_sec": 480,  "main_pc_only": False,
                   "description": "LoRA 3 枚適用 t2i"},
}


class WorkflowManager:
    """プリセット JSON の読み込み・差し込み・DB への登録を担う。"""

    def __init__(self, bot):
        self.bot = bot
        self._cache: dict[str, dict] = {}  # name -> raw workflow json

    # --- 起動時ローダ ---

    async def sync_presets_to_db(self) -> None:
        """presets/*.json を走査して workflows テーブルへ upsert する。"""
        if not os.path.isdir(_PRESETS_DIR):
            log.warning("presets dir not found: %s", _PRESETS_DIR)
            return
        for fname in sorted(os.listdir(_PRESETS_DIR)):
            if not fname.endswith(".json"):
                continue
            name = fname[:-5]
            path = os.path.join(_PRESETS_DIR, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    wf = json.load(f)
            except Exception as e:
                log.error("Failed to load preset %s: %s", name, e)
                continue
            self._cache[name] = wf
            req = self.extract_requirements(wf)
            meta = _PRESET_META.get(name, {"category": "t2i", "default_timeout_sec": 300,
                                           "main_pc_only": False, "description": ""})
            try:
                await self.bot.database.workflow_upsert(
                    name=name,
                    description=meta.get("description"),
                    category=meta.get("category", "t2i"),
                    workflow_json=json.dumps(wf, ensure_ascii=False),
                    required_nodes=json.dumps(req.nodes, ensure_ascii=False),
                    required_models=json.dumps(req.models, ensure_ascii=False),
                    required_loras=json.dumps(req.loras, ensure_ascii=False),
                    main_pc_only=bool(meta.get("main_pc_only", False)),
                    default_timeout_sec=int(meta.get("default_timeout_sec", 300)),
                )
                log.info("Preset synced: %s (nodes=%d, placeholders=%d)",
                         name, len(req.nodes), len(req.placeholders))
            except Exception as e:
                log.error("Preset upsert failed for %s: %s", name, e)

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

    async def resolve(self, name: str, params: dict[str, Any]) -> dict:
        """プリセット名とパラメータから、実行用 workflow JSON を返す。

        - params は大文字キー推奨（{{POSITIVE}} に対して "POSITIVE"）
        - 欠けているプレースホルダは DEFAULT_PARAMS → ValidationError の順で補完
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

        # 深いコピーしつつプレースホルダ置換
        return _substitute(wf, resolved_params)

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
