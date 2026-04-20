"""ZZZ Disc Manager: SQLite スキーマ管理 + CRUD（ビルド中心モデル）。

- zzz_characters / zzz_set_masters: マスタ
- zzz_discs: インベントリ（fingerprint で重複排除）
- zzz_builds: キャラ別ビルド（is_current=1 が「現在の装備」、0 がプリセット）
- zzz_build_slots: ビルド × 部位 → disc_id
- zzz_hoyolab_accounts: HoYoLAB cookie（平文）
- zzz_extraction_jobs: VLM 抽出キュー

ドメインごとにサブモジュールへ分割し、当 __init__ で全シンボルを再エクスポートする。
`from src.tools.zzz_disc import models; models.xxx(...)` 形式の既存呼び出しは互換。
"""

from ._base import (
    _SCHEMA_SQL as _SCHEMA_SQL,
)
from ._base import (
    JST as JST,
)
from ._base import (
    _backfill_fingerprints as _backfill_fingerprints,
)
from ._base import (
    _maybe_add_column as _maybe_add_column,
)
from ._base import (
    _now as _now,
)
from ._base import (
    compute_fingerprint as compute_fingerprint,
)
from ._base import (
    init_schema as init_schema,
)
from .builds import *  # noqa: F401,F403
from .characters import *  # noqa: F401,F403
from .discs import *  # noqa: F401,F403
from .hoyolab import *  # noqa: F401,F403
from .jobs import *  # noqa: F401,F403
from .teams import *  # noqa: F401,F403
