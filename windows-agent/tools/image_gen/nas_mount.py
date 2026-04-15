"""image_gen 用 NAS SMB マウント。

既存 `agent._mount_nas` は STT 用途で .env から読み込むが、image_gen では
agent_config.yaml の `image_gen.nas` と .env の認証情報を組み合わせてドライブ
レターを明示的に割り当てる（既定 `N:`）。
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional


def _find_existing_mapping(unc: str) -> Optional[str]:
    """net use 出力から UNC が既にマップされているドライブレターを探す。

    管理者/ユーザー両セッションの混在や、同 UNC への二重マップを避けるため、
    Agent プロセスから見えている net use の一覧から drive を拾って再利用する。
    """
    try:
        r = subprocess.run(
            ["net", "use"], capture_output=True, text=True, errors="replace", timeout=5,
        )
        if r.returncode != 0:
            return None
        unc_l = unc.lower().rstrip("\\")
        for line in (r.stdout or "").splitlines():
            tokens = line.split()
            # 形式例: "OK           W:        \\host\share                 Microsoft Windows Network"
            if len(tokens) < 3:
                continue
            for t in tokens:
                if len(t) == 2 and t.endswith(":") and t[0].isalpha():
                    drive_tok = t.upper()
                    # 同一行に目標 UNC が含まれるか
                    if unc_l in line.lower():
                        return drive_tok
        return None
    except Exception:
        return None


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
    subpath = (nas_cfg.get("subpath") or "").strip("\\/")
    drive = (nas_cfg.get("mount_drive") or "N:").rstrip("\\")
    user = env.get("NAS_USER") or nas_cfg.get("user") or ""
    pw = env.get("NAS_PASS") or env.get("NAS_PASSWORD") or ""

    if not host:
        return {"ok": False, "drive": drive, "unc": "", "base": drive, "message": "NAS host not configured"}

    unc = rf"\\{host}\{share}"

    def _make_base(d: str) -> str:
        d2 = d.rstrip("\\")
        return d2 if not subpath else f"{d2}\\{subpath.replace('/', chr(92))}"

    # 既存マッピング再利用（別ドライブレターに同 UNC が既に繋がっていれば流用）
    existing = _find_existing_mapping(unc)
    if existing:
        return {
            "ok": True, "drive": existing, "unc": unc, "base": _make_base(existing),
            "message": f"reusing existing mapping {existing}",
        }

    base = _make_base(drive)
    try:
        # 指定ドライブに同 UNC が既にマップされていればそのまま使う
        check = subprocess.run(
            ["net", "use", drive],
            capture_output=True, text=True, errors="replace", timeout=5,
        )
        if check.returncode == 0 and unc.lower() in (check.stdout or "").lower():
            return {"ok": True, "drive": drive, "unc": unc, "base": base, "message": "already mounted"}

        args = ["net", "use", drive, unc]
        if user and pw:
            args += [f"/user:{user}", pw]
        args += ["/persistent:no"]

        result = subprocess.run(
            args, capture_output=True, text=True, errors="replace", timeout=15,
        )
        if result.returncode == 0:
            return {"ok": True, "drive": drive, "unc": unc, "base": base, "message": "mounted"}
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        if "1219" in stderr or "already" in (stderr + stdout).lower():
            return {"ok": True, "drive": drive, "unc": unc, "base": base, "message": "already connected"}
        return {
            "ok": False,
            "drive": drive,
            "unc": unc,
            "base": base,
            "message": f"mount failed (rc={result.returncode}): {stderr or stdout}",
        }
    except Exception as e:
        return {"ok": False, "drive": drive, "unc": unc, "base": base, "message": f"exception: {e}"}


def ensure_mounted(image_gen_cfg: dict, agent_dir: str) -> Optional[str]:
    """マウント済み or 新規マウント成功なら実効ベースパス（drive[\\subpath]）を返す。失敗時 None。"""
    result = mount_image_gen_nas(image_gen_cfg, agent_dir)
    if result["ok"]:
        return result.get("base") or result["drive"]
    return None
