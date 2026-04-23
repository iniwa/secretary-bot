"""SQLite操作（aiosqlite・WALモード）。

ドメインごとに mixin へ分割し、`Database` クラスで合成する構造。
外部からは `Database` / `jst_now` / `JST` の 3 シンボルのみを公開する。
"""

from src.database._base import _SCHEMA_VERSION, JST, DatabaseBase, jst_now
from src.database.clip_pipeline import ClipPipelineMixin
from src.database.conversation import ConversationMixin
from src.database.generation import GenerationJobMixin
from src.database.kobo_watch import KoboWatchMixin
from src.database.lora import LoRAMixin
from src.database.monologue import MonologueMixin
from src.database.pending import PendingActionMixin
from src.database.section import SectionMixin
from src.database.settings import SettingsMixin
from src.database.wildcard import WildcardMixin


class Database(
    ConversationMixin,
    SettingsMixin,
    MonologueMixin,
    PendingActionMixin,
    GenerationJobMixin,
    ClipPipelineMixin,
    SectionMixin,
    WildcardMixin,
    LoRAMixin,
    KoboWatchMixin,
    DatabaseBase,
):
    pass


__all__ = ["JST", "Database", "_SCHEMA_VERSION", "jst_now"]
