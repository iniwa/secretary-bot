"""image_gen 用 NAS SMB マウント。

既存 `agent._mount_nas` は STT 用途で .env から読み込むが、image_gen では
agent_config.yaml の `image_gen.nas` と .env の認証情報を組み合わせてドライブ
レターを明示的に割り当てる（既定 `N:`）。
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional


def _read_env(env_path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if not os.path.exists(env_path):
        return env
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def mount_image_gen_nas(image_gen_cfg: dict, agent_dir: str) -> dict:
    """image_gen.nas 設定と .env を元に SMB を指定ドライブにマッピング。

    Returns: { ok: bool, drive: str, unc: str, message: str }
    """
    nas_cfg = image_gen_cfg.get("nas", {}) or {}
    env = _read_env(os.path.join(agent_dir, "config", ".env"))

    host = nas_cfg.get("host") or env.get("NAS_HOST") or ""
    share = nas_cfg.get("share") or env.get("NAS_SHARE") or "ai-image"
    drive = (nas_cfg.get("mount_drive") or "N:").rstrip("\\")
    user = env.get("NAS_USER") or nas_cfg.get("user") or ""
    pw = env.get("NAS_PASS") or env.get("NAS_PASSWORD") or ""

    if not host:
        return {"ok": False, "drive": drive, "unc": "", "message": "NAS host not configured"}

    unc = rf"\\{host}\{share}"
    try:
        # 既存マッピングを確認
        check = subprocess.run(
            ["net", "use", drive],
            capture_output=True, text=True, errors="replace", timeout=5,
        )
        if check.returncode == 0 and unc.lower() in (check.stdout or "").lower():
            return {"ok": True, "drive": drive, "unc": unc, "message": "already mounted"}

        args = ["net", "use", drive, unc]
        if user and pw:
            args += [f"/user:{user}", pw]
        args += ["/persistent:no"]

        result = subprocess.run(
            args, capture_output=True, text=True, errors="replace", timeout=15,
        )
        if result.returncode == 0:
            return {"ok": True, "drive": drive, "unc": unc, "message": "mounted"}
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        if "1219" in stderr or "already" in (stderr + stdout).lower():
            return {"ok": True, "drive": drive, "unc": unc, "message": "already connected"}
        return {
            "ok": False,
            "drive": drive,
            "unc": unc,
            "message": f"mount failed (rc={result.returncode}): {stderr or stdout}",
        }
    except Exception as e:
        return {"ok": False, "drive": drive, "unc": unc, "message": f"exception: {e}"}


def ensure_mounted(image_gen_cfg: dict, agent_dir: str) -> Optional[str]:
    """マウント済み or 新規マウント成功なら drive を返す。失敗時 None。"""
    result = mount_image_gen_nas(image_gen_cfg, agent_dir)
    if result["ok"]:
        return result["drive"]
    return None
