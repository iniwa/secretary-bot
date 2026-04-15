"""ZZZ Disc Manager: リクエスト/レスポンスDTO（Pydantic）。ビルド中心モデル。"""

from pydantic import BaseModel, Field, model_validator
from typing import Any


# ---------- Discs ----------

# ZZZ スロット別メインステ制約（手動登録時バリデーション用）
# slot 1/2/3 は固定。slot 4/5/6 は候補内から選択。
SLOT_FIXED_MAIN_STAT: dict[int, str] = {
    1: "HP",
    2: "攻撃力",
    3: "防御力",
}
SLOT_ALLOWED_MAIN_STATS: dict[int, set[str]] = {
    4: {"HP%", "攻撃力%", "防御力%", "会心率%", "会心ダメージ%", "異常掌握"},
    5: {"HP%", "攻撃力%", "防御力%", "貫通率%",
        "物理属性ダメージ%", "炎属性ダメージ%", "氷属性ダメージ%",
        "電気属性ダメージ%", "エーテル属性ダメージ%"},
    6: {"HP%", "攻撃力%", "防御力%", "異常マスタリー", "異常掌握",
        "衝撃力%", "エネルギー自動回復%"},
}
# rarity 別のレベル上限（B=9/A=12/S=15）。ほぼ S 運用なので緩め。
RARITY_LEVEL_MAX: dict[str, int] = {"B": 9, "A": 12, "S": 15}


class SubStat(BaseModel):
    name: str
    value: float
    upgrades: int = 0
    is_percent: bool = False


class DiscIn(BaseModel):
    slot: int = Field(..., ge=1, le=6)
    set_id: int | None = None
    main_stat_name: str
    main_stat_value: float
    sub_stats: list[SubStat] = Field(default_factory=list)
    level: int = Field(0, ge=0, le=15)
    rarity: str | None = None
    hoyolab_disc_id: str | None = None
    source_image_path: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _validate_main_stat_by_slot(self):
        fixed = SLOT_FIXED_MAIN_STAT.get(self.slot)
        if fixed and self.main_stat_name != fixed:
            raise ValueError(
                f"slot {self.slot} の main_stat は '{fixed}' 固定です "
                f"(指定: '{self.main_stat_name}')"
            )
        allowed = SLOT_ALLOWED_MAIN_STATS.get(self.slot)
        if allowed and self.main_stat_name not in allowed:
            raise ValueError(
                f"slot {self.slot} の main_stat '{self.main_stat_name}' は "
                f"許可されていません。候補: {sorted(allowed)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_level_by_rarity(self):
        if self.rarity:
            lv_max = RARITY_LEVEL_MAX.get(self.rarity.upper())
            if lv_max is not None and self.level > lv_max:
                raise ValueError(
                    f"rarity={self.rarity} の level 上限は {lv_max} です "
                    f"(指定: {self.level})"
                )
        return self


class DiscOut(BaseModel):
    id: int
    slot: int
    set_id: int | None
    main_stat_name: str
    main_stat_value: float
    sub_stats: list[dict]
    level: int = 0
    rarity: str | None = None
    fingerprint: str | None = None
    hoyolab_disc_id: str | None = None
    icon_url: str | None = None
    name: str | None = None
    source_image_path: str | None = None
    note: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


# ---------- Masters ----------

class CharacterOut(BaseModel):
    id: int
    slug: str
    name_ja: str
    element: str | None = None
    faction: str | None = None
    icon_url: str | None = None
    display_order: int = 0
    hoyolab_agent_id: str | None = None
    recommended_substats: list[str] = Field(default_factory=list)


class SetMasterOut(BaseModel):
    id: int
    slug: str
    name_ja: str
    aliases: list[str] = Field(default_factory=list)
    two_pc_effect: str | None = None
    four_pc_effect: str | None = None


class MastersResponse(BaseModel):
    characters: list[CharacterOut]
    sets: list[SetMasterOut]


# ---------- Builds ----------

class BuildSlotOut(BaseModel):
    slot: int
    disc_id: int | None = None
    disc: DiscOut | None = None


class BuildOut(BaseModel):
    id: int
    character_id: int
    name: str
    tag: str | None = None
    rank: str | None = None
    notes: str | None = None
    is_current: bool
    stats: dict = Field(default_factory=dict)
    synced_at: str | None = None
    created_at: str
    updated_at: str
    slots: list[BuildSlotOut] = Field(default_factory=list)


class BuildMetaIn(BaseModel):
    """プリセットのメタ情報更新。"""
    name: str | None = None
    tag: str | None = None
    rank: str | None = None
    notes: str | None = None


class BuildSavePresetIn(BaseModel):
    """現在の装備をプリセットとして保存するリクエスト。"""
    name: str
    tag: str | None = None
    rank: str | None = None
    notes: str | None = None


class BuildSlotAssignIn(BaseModel):
    """ビルドの特定スロットに disc を割り当て。"""
    disc_id: int | None = None


# ---------- Shared discs ----------

class SharedDiscBuildRef(BaseModel):
    build_id: int
    name: str
    is_current: bool
    character_id: int
    character_slug: str
    character_name_ja: str
    slot: int


class SharedDiscOut(BaseModel):
    disc: DiscOut
    usage_count: int
    used_by: list[SharedDiscBuildRef]


# ---------- HoYoLAB ----------

class HoyolabAccountIn(BaseModel):
    uid: str
    region: str  # "prod_gf_jp" / "prod_gf_us" / etc
    ltuid_v2: str
    ltoken_v2: str
    nickname: str | None = None


class HoyolabCredentialsIn(BaseModel):
    """自動ログイン用 email/password の保存。"""
    email: str
    password: str
    auto_login_enabled: bool = True


class HoyolabAutoLoginIn(BaseModel):
    """ワンショット自動ログイン（credentials は任意で保存）。"""
    email: str
    password: str
    uid: str | None = None       # 未登録時の初回登録用
    region: str | None = None    # 未登録時の初回登録用
    nickname: str | None = None
    save_credentials: bool = True


class HoyolabAccountOut(BaseModel):
    uid: str
    region: str
    nickname: str | None = None
    last_synced_at: str | None = None
    auto_login_enabled: bool = False
    last_auto_login_at: str | None = None
    last_auto_login_error: str | None = None
    email: str | None = None  # password は返さない


class HoyolabSyncResult(BaseModel):
    synced_characters: int
    synced_discs: int
    errors: list[str] = Field(default_factory=list)


# ---------- Jobs ----------

class JobOut(BaseModel):
    id: int
    status: str
    source: str
    image_path: str | None = None
    extracted_json: Any | None = None
    normalized_json: Any | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str


class JobConfirmIn(BaseModel):
    """ready ジョブの確定保存リクエスト（編集後のディスク値）。"""
    disc: DiscIn


class JobCaptureIn(BaseModel):
    source: str = "capture-mss"
