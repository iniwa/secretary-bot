"""ZZZ Disc Manager: リクエスト/レスポンスDTO（Pydantic）。"""

from pydantic import BaseModel, Field
from typing import Any


class SubStat(BaseModel):
    name: str
    value: float
    upgrades: int = 0


class DiscIn(BaseModel):
    slot: int = Field(..., ge=1, le=6)
    set_id: int | None = None
    main_stat_name: str
    main_stat_value: float
    sub_stats: list[SubStat] = Field(default_factory=list)
    source_image_path: str | None = None
    note: str | None = None


class DiscOut(BaseModel):
    id: int
    slot: int
    set_id: int | None
    main_stat_name: str
    main_stat_value: float
    sub_stats: list[dict]
    source_image_path: str | None
    note: str | None
    created_at: str
    updated_at: str


class PresetIn(BaseModel):
    preferred_set_ids: list[int] = Field(default_factory=list)
    preferred_main_stats: list[str] = Field(default_factory=list)
    sub_stat_priority: list[dict] = Field(default_factory=list)  # [{name, weight}]


class PresetOut(BaseModel):
    character_id: int
    slot: int
    preferred_set_ids: list[int]
    preferred_main_stats: list[str]
    sub_stat_priority: list[dict]
    updated_at: str


class CharacterOut(BaseModel):
    id: int
    slug: str
    name_ja: str
    element: str | None = None
    faction: str | None = None
    icon_url: str | None = None
    display_order: int = 0


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


class CandidateOut(BaseModel):
    character_id: int
    character_slug: str
    character_name_ja: str
    slot: int
    score: float


class JobOut(BaseModel):
    id: int
    status: str
    source: str
    image_path: str | None
    extracted_json: Any | None
    normalized_json: Any | None
    error_message: str | None
    created_at: str
    updated_at: str


class JobConfirmIn(BaseModel):
    """ready ジョブの確定保存リクエスト（編集後のディスク値）。"""
    disc: DiscIn


class JobCaptureIn(BaseModel):
    """「今の画面を解析」ボタンからのリクエスト。"""
    source: str = "capture-mss"  # "capture-mss" | "capture-obs"
