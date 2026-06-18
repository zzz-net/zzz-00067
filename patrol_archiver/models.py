from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class AnnotationStatus(str, Enum):
    PENDING = "pending"
    TO_REPHOTO = "to_rephoto"
    CONFIRMED = "confirmed"
    IGNORED = "ignored"
    ARCHIVED = "archived"


class DuplicateStrategy(str, Enum):
    SKIP = "skip"
    RENAME = "rename"
    OVERWRITE = "overwrite"
    BLOCK = "block"


class ArchiveAction(str, Enum):
    MOVE = "move"
    COPY = "copy"


class NoteEntry(BaseModel):
    timestamp: datetime
    author: str = "system"
    content: str


class Point(BaseModel):
    id: str
    name: str
    category: str = ""
    location: str = ""
    description: str = ""
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


class Photo(BaseModel):
    source_path: Path
    file_name: str
    file_size: int
    file_hash: str
    taken_at: Optional[datetime] = None
    point_id: Optional[str] = None
    camera: str = ""

    @field_validator("source_path", mode="before")
    @classmethod
    def _convert_path(cls, v):
        return Path(v) if v else v

    @classmethod
    def from_path(cls, path: Path) -> "Photo":
        stat = path.stat()
        file_hash = cls._compute_hash(path)
        return cls(
            source_path=path,
            file_name=path.name,
            file_size=stat.st_size,
            file_hash=file_hash,
        )

    @staticmethod
    def _compute_hash(path: Path, chunk_size: int = 8192) -> str:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)
        return sha256.hexdigest()


class Annotation(BaseModel):
    id: str
    point_id: str
    photo_id: Optional[str] = None
    status: AnnotationStatus = AnnotationStatus.PENDING
    notes: List[NoteEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def add_note(self, content: str, author: str = "user") -> None:
        self.notes.append(NoteEntry(
            timestamp=datetime.now(),
            author=author,
            content=content,
        ))
        self.updated_at = datetime.now()


class PreviewItem(BaseModel):
    photo: Photo
    point: Optional[Point] = None
    target_path: Path
    will_conflict: bool = False
    duplicate_strategy: DuplicateStrategy = DuplicateStrategy.BLOCK
    annotation: Optional[Annotation] = None

    @field_validator("target_path", mode="before")
    @classmethod
    def _convert_target_path(cls, v):
        return Path(v) if v else v


class Conflict(BaseModel):
    id: str
    target_path: Path
    existing_source: Optional[Path] = None
    new_source: Path
    reason: str
    resolved: bool = False
    resolution: str = ""

    @field_validator("target_path", "existing_source", "new_source", mode="before")
    @classmethod
    def _convert_paths(cls, v):
        return Path(v) if v else v


class UndoRecord(BaseModel):
    id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    action_type: str
    description: str
    previous_state: Dict[str, Any]


class Batch(BaseModel):
    id: str
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    config_version: int = 1
    points: Dict[str, Point] = Field(default_factory=dict)
    photos: Dict[str, Photo] = Field(default_factory=dict)
    annotations: Dict[str, Annotation] = Field(default_factory=dict)
    previews: List[PreviewItem] = Field(default_factory=list)
    conflicts: List[Conflict] = Field(default_factory=list)
    undo_stack: List[UndoRecord] = Field(default_factory=list)
    notes_json_path: Optional[Path] = None
    photo_dir: Optional[Path] = None
    csv_path: Optional[Path] = None

    @field_validator("notes_json_path", "photo_dir", "csv_path", mode="before")
    @classmethod
    def _convert_opt_paths(cls, v):
        return Path(v) if v else v


class LastSnapshotInfo(BaseModel):
    source_path: Optional[Path] = None
    snapshot_version: Optional[int] = None
    imported_at: Optional[datetime] = None
    imported_by: str = "user"
    snapshot_name: str = ""

    @field_validator("source_path", mode="before")
    @classmethod
    def _convert_source_path(cls, v):
        return Path(v) if v else v


class ArchiverConfig(BaseModel):
    version: int = 1
    naming_template: str = "{point.category}/{point.id}_{point.name}_{photo.taken_at:%Y%m%d_%H%M%S}{photo.source_path.suffix}"
    allowed_extensions: List[str] = Field(default_factory=lambda: [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"])
    duplicate_strategy: DuplicateStrategy = DuplicateStrategy.BLOCK
    archive_action: ArchiveAction = ArchiveAction.COPY
    archive_dir: Path = Path("./archive")
    photo_dir: Path = Path("./photos")
    notes_json: Path = Path("./notes.json")
    points_csv: Path = Path("./points.csv")
    default_author: str = "user"
    last_snapshot: Optional[LastSnapshotInfo] = None

    @field_validator("archive_dir", "photo_dir", "notes_json", "points_csv", mode="before")
    @classmethod
    def _convert_paths(cls, v):
        return Path(v) if v else v

    @field_validator("allowed_extensions")
    @classmethod
    def _normalize_extensions(cls, v):
        return [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in v]


class RuleSnapshot(BaseModel):
    schema_version: int = 1
    snapshot_id: str
    created_at: datetime
    created_by: str = "user"
    name: str = ""
    description: str = ""
    config_version: int
    naming_template: str
    allowed_extensions: List[str]
    duplicate_strategy: DuplicateStrategy
    archive_action: ArchiveAction
    archive_dir: Path
    photo_dir: Path
    notes_json: Path
    points_csv: Path
    default_author: str = "user"

    @field_validator("archive_dir", "photo_dir", "notes_json", "points_csv", mode="before")
    @classmethod
    def _convert_snapshot_paths(cls, v):
        return Path(v) if v else v

    @field_validator("allowed_extensions")
    @classmethod
    def _normalize_snapshot_extensions(cls, v):
        return [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in v]


class ConflictType(str, Enum):
    CONFIG_EXISTS = "config_exists"
    BATCH_EXISTS = "batch_exists"
    VERSION_MISMATCH = "version_mismatch"
    EXTENSION_CONFLICT = "extension_conflict"
    TEMPLATE_CONFLICT = "template_conflict"
    STRATEGY_CONFLICT = "strategy_conflict"


class SnapshotConflict(BaseModel):
    type: ConflictType
    field: str = ""
    existing_value: str = ""
    incoming_value: str = ""
    message: str


class ImportResult(BaseModel):
    success: bool = False
    applied: bool = False
    conflicts: List[SnapshotConflict] = Field(default_factory=list)
    message: str = ""
    snapshot: Optional[RuleSnapshot] = None


class OperationLogEntry(BaseModel):
    id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    operation: str
    author: str = "user"
    details: Dict[str, Any] = Field(default_factory=dict)
    status: str = "success"
    message: str = ""


class SnapshotImportLog(OperationLogEntry):
    snapshot_id: str = ""
    snapshot_name: str = ""
    snapshot_version: int = 0
    source_path: Optional[Path] = None
    conflicts_resolved: List[str] = Field(default_factory=list)
    config_version_before: int = 0
    config_version_after: int = 0

    @field_validator("source_path", mode="before")
    @classmethod
    def _convert_log_source_path(cls, v):
        return Path(v) if v else v


class InvalidNamingTemplateError(ValueError):
    """命名模板含未知变量或不合法，在持久化/导入前抛出。"""

    def __init__(self, template: str, errors: List[str]):
        self.template = template
        self.errors = list(errors)
        msg_lines = ["命名模板不合法："] + [f"  • {e}" for e in self.errors]
        super().__init__("\n".join(msg_lines))


class ConflictSummary(BaseModel):
    total: int = 0
    unresolved: int = 0
    by_reason: Dict[str, int] = Field(default_factory=dict)


class DraftSourceInfo(BaseModel):
    batch_id: str
    batch_name: str
    batch_created_at: datetime
    points_count: int
    photos_count: int
    points_hash: str
    photos_hash: str

    @field_validator("batch_created_at", mode="before")
    @classmethod
    def _convert_datetime(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v


class ArchiveDraft(BaseModel):
    id: str
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    description: str = ""
    source: DraftSourceInfo
    config_version: int
    duplicate_strategy: DuplicateStrategy
    archive_action: ArchiveAction
    previews: List[PreviewItem] = Field(default_factory=list)
    conflicts: List[Conflict] = Field(default_factory=list)
    conflict_summary: ConflictSummary = Field(default_factory=ConflictSummary)
    naming_template: str = ""
    allowed_extensions: List[str] = Field(default_factory=list)

    @field_validator("created_at", mode="before")
    @classmethod
    def _convert_created_at(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v


class DraftRestoreResult(BaseModel):
    success: bool = False
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    needs_confirmation: bool = False
    confirmation_prompt: str = ""
