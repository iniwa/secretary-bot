"""Download faster-whisper (CTranslate2) models to NAS for Auto-Kirinuki.

faster-whisper は CTranslate2 形式のモデルディレクトリ
(`model.bin` / `config.json` / `tokenizer.json` / `vocabulary.*`) を参照する。
Agent 側 `list_nas_models` は `<dest>/<model>/model.bin` の存在でモデルを検出するため、
このスクリプトでは HuggingFace Hub から `Systran/faster-whisper-*` 相当の CT2 リポジトリを
`<dest>/<model>/` に展開する。

配置先の決め方（image_gen の nas_mount.py に揃える）:
    share    <- agent_config.yaml clip_pipeline.nas.share
                → 無ければ .env NAS_SHARE → "Work"
    subpath  <- clip_pipeline.nas.subpath (既定 "auto-kirinuki")
    whisper  <- clip_pipeline.nas.whisper_subdir (既定 "models/whisper")

認証は `.env` の NAS_HOST / NAS_USER / NAS_PASS。Agent と同じ方式で
`\\<HOST>\<SHARE>` を一時 `net use` してから UNC 直書きするので、
`W:` / `N:` のドライブレターが切れていても配置できる。

Usage:
    python scripts/download_whisper_models.py large-v3-turbo large-v3
    python scripts/download_whisper_models.py --all
    python scripts/download_whisper_models.py --dest "W:/auto-kirinuki/models/whisper" large-v3

前提:
    - pip install huggingface_hub pyyaml
    - windows-agent/config/.env に NAS_HOST / NAS_USER / NAS_PASS がある
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_ENV = REPO_ROOT / "windows-agent" / "config" / ".env"
AGENT_CONFIG = REPO_ROOT / "windows-agent" / "config" / "agent_config.yaml"

# モデル名 → HuggingFace Hub リポジトリ ID
REPO_MAP: dict[str, str] = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}

def _read_env(env_path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _read_clip_nas_cfg() -> dict:
    """agent_config.yaml の clip_pipeline.nas を返す。無ければ空 dict。"""
    if not AGENT_CONFIG.exists():
        return {}
    try:
        import yaml  # lazy
    except ImportError:
        print("[warn] PyYAML が無いので agent_config.yaml を読み飛ばします (pip install pyyaml 推奨)")
        return {}
    try:
        data = yaml.safe_load(AGENT_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[warn] {AGENT_CONFIG} の読み込みに失敗: {e}")
        return {}
    return ((data.get("clip_pipeline") or {}).get("nas")) or {}


def _resolve_nas_subpath() -> str:
    """agent_config.yaml から clip_pipeline.nas.subpath + whisper_subdir を組む。
    既定は `auto-kirinuki\models\whisper`。
    """
    cfg = _read_clip_nas_cfg()
    subpath = (cfg.get("subpath") or "auto-kirinuki").strip("\\/")
    whisper_sub = (cfg.get("whisper_subdir") or "models/whisper").strip("\\/")
    return (subpath + "/" + whisper_sub).replace("/", "\\")


def ensure_nas_mount() -> str | None:
    """Agent と同じ share で `\\<HOST>\<SHARE>` を一時 net use し、UNC パス base を返す。

    share の決定順:
      1. agent_config.yaml の clip_pipeline.nas.share
      2. .env の NAS_SHARE
      3. "Work"

    image_gen の nas_mount.py と合わせる。既にマウント済みなら net use はスキップ。
    """
    env = _read_env(AGENT_ENV)
    cfg = _read_clip_nas_cfg()
    host = (cfg.get("host") or env.get("NAS_HOST") or "").strip()
    share = (cfg.get("share") or env.get("NAS_SHARE") or "Work").strip()
    user = env.get("NAS_USER", "").strip()
    pw = env.get("NAS_PASS", "").strip() or env.get("NAS_PASSWORD", "").strip()
    if not host:
        print(f"[warn] NAS_HOST not set in {AGENT_ENV}")
        return None

    unc = rf"\\{host}\{share}"

    # 既に UNC で読めるか試す
    try:
        if os.path.isdir(unc):
            print(f"[nas] already accessible: {unc}")
            return unc
    except OSError:
        pass

    if not user or not pw:
        print(f"[warn] NAS_USER / NAS_PASS not set in {AGENT_ENV}")
        return None

    # net use で一時マウント（/persistent:no）
    print(f"[nas] net use {unc} (user={user})")
    result = subprocess.run(
        ["net", "use", unc, pw, f"/user:{user}", "/persistent:no"],
        capture_output=True, text=True, errors="replace", timeout=20,
    )
    if result.returncode == 0:
        print(f"[nas] connected: {unc}")
        return unc
    out = (result.stderr or "").strip() or (result.stdout or "").strip()
    if "1219" in out or "already" in out.lower():
        print(f"[nas] already connected: {unc}")
        return unc
    print(f"[error] net use failed (rc={result.returncode}): {out}")
    return None


def download_model(name: str, dest_base: Path) -> bool:
    from huggingface_hub import snapshot_download

    repo = REPO_MAP.get(name)
    if not repo:
        print(f"[skip] unknown model: {name}")
        return False
    dest = dest_base / name
    marker = dest / "model.bin"
    if marker.exists():
        print(f"[skip] already present: {dest}")
        return True
    print(f"[download] {name}  <-  {repo}")
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo,
        local_dir=str(dest),
        allow_patterns=[
            "model.bin",
            "config.json",
            "tokenizer.json",
            "vocabulary.*",
            "preprocessor_config.json",
            "generation_config.json",
        ],
    )
    if not marker.exists():
        print(f"[warn] model.bin not found after download: {dest}")
        return False
    print(f"[done] {dest}")
    return True


def resolve_dest(arg_dest: str | None) -> Path | None:
    """--dest の解決。未指定なら `\\<HOST>\<SHARE>\<subpath>\<whisper_subdir>` を
    agent_config.yaml から組み立てる。"""
    if arg_dest:
        return Path(arg_dest)
    base = ensure_nas_mount()
    if not base:
        return None
    return Path(base) / _resolve_nas_subpath()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "models", nargs="*",
        help="model names to download (e.g. large-v3 large-v3-turbo)",
    )
    parser.add_argument("--all", action="store_true", help="download every known model")
    parser.add_argument(
        "--dest", default=None,
        help=r"destination base directory (default: \\<NAS_HOST>\<NAS_SHARE>\auto-kirinuki\models\whisper)",
    )
    parser.add_argument(
        "--list", action="store_true", help="print available model names and exit",
    )
    args = parser.parse_args()

    if args.list:
        print("available models:")
        for n, r in REPO_MAP.items():
            print(f"  {n:<18} <- {r}")
        return 0

    names = list(REPO_MAP.keys()) if args.all else args.models
    if not names:
        parser.print_help()
        print("\n(specify model names or --all)")
        return 1

    unknown = [n for n in names if n not in REPO_MAP]
    if unknown:
        print(f"[error] unknown model(s): {', '.join(unknown)}")
        print("use --list to see available names")
        return 2

    dest_base = resolve_dest(args.dest)
    if dest_base is None:
        print("[error] failed to resolve NAS destination (see warnings above)")
        return 3
    try:
        dest_base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[error] cannot create destination: {dest_base}: {e}")
        return 3

    print(f"[dest] {dest_base}")
    ok = 0
    for n in names:
        if download_model(n, dest_base):
            ok += 1
    total = len(names)
    print(f"\nsummary: {ok}/{total} model(s) ready under {dest_base}")
    return 0 if ok == total else 4


if __name__ == "__main__":
    sys.exit(main())
