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
