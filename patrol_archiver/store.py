from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    Annotation,
    AnnotationStatus,
    ArchiverConfig,
    ArchiveAction,
    ArchiveDraft,
    Batch,
    Conflict,
    ConflictSummary,
    DraftRestoreResult,
    DraftSourceInfo,
    DuplicateStrategy,
    NoteEntry,
    OperationLogEntry,
    Photo,
    Point,
    PreviewItem,
    SnapshotImportLog,
    UndoRecord,
)


class BatchStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.data_dir = self.workspace / ".patrol-archiver"
        self.batches_dir = self.data_dir / "batches"
        self.drafts_dir = self.data_dir / "drafts"
        self.current_batch_file = self.data_dir / "current_batch.json"
        self.operation_log_file = self.data_dir / "operation_log.jsonl"
        self._current_batch: Optional[Batch] = None

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)
        self.drafts_dir.mkdir(parents=True, exist_ok=True)

    def create_batch(self, name: Optional[str] = None, config_version: int = 1) -> Batch:
        self.ensure_dirs()
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        name = name or f"Batch {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        batch = Batch(id=batch_id, name=name, config_version=config_version)
        self._save_batch(batch)
        self._set_current_batch(batch_id)
        self._current_batch = batch
        return batch

    def _save_batch(self, batch: Batch) -> None:
        self.ensure_dirs()
        batch.updated_at = datetime.now()
        batch_file = self.batches_dir / f"{batch.id}.json"
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump(batch.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

    def find_batch(self, identifier: str) -> Optional[Batch]:
        if not self.batches_dir.exists():
            return None
        batch_file = self.batches_dir / f"{identifier}.json"
        if batch_file.exists():
            with open(batch_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Batch.model_validate(data)
        for batch_file in self.batches_dir.glob("batch_*.json"):
            with open(batch_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("name") == identifier:
                return Batch.model_validate(data)
        return None

    def load_batch(self, batch_id: str) -> Optional[Batch]:
        batch = self.find_batch(batch_id)
        if batch:
            self._current_batch = batch
        return batch

    def get_current_batch(self) -> Optional[Batch]:
        if self._current_batch is not None:
            return self._current_batch

        if not self.current_batch_file.exists():
            return None

        with open(self.current_batch_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        batch_id = data.get("batch_id")
        if not batch_id:
            return None

        return self.load_batch(batch_id)

    def _set_current_batch(self, batch_id: str) -> None:
        self.ensure_dirs()
        with open(self.current_batch_file, "w", encoding="utf-8") as f:
            json.dump({"batch_id": batch_id}, f, indent=2)

    def set_current_batch(self, identifier: str) -> Optional[Batch]:
        batch = self.find_batch(identifier)
        if batch:
            self._set_current_batch(batch.id)
            self._current_batch = batch
        return batch

    def save_current_batch(self) -> None:
        if self._current_batch:
            self._save_batch(self._current_batch)

    def list_batches(self) -> List[Dict[str, Any]]:
        if not self.batches_dir.exists():
            return []
        batches = []
        for batch_file in sorted(self.batches_dir.glob("batch_*.json"), reverse=True):
            try:
                with open(batch_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                batches.append({
                    "id": data["id"],
                    "name": data["name"],
                    "created_at": data["created_at"],
                    "updated_at": data["updated_at"],
                    "points_count": len(data.get("points", {})),
                    "photos_count": len(data.get("photos", {})),
                    "annotations_count": len(data.get("annotations", {})),
                    "conflicts_count": len(data.get("conflicts", [])),
                })
            except Exception:
                continue
        return batches

    def add_points(self, batch: Batch, points: List[Point]) -> None:
        for point in points:
            batch.points[point.id] = point
            if point.id not in batch.annotations:
                batch.annotations[point.id] = Annotation(
                    id=f"ann_{point.id}",
                    point_id=point.id,
                    status=AnnotationStatus.PENDING,
                )
        self._save_batch(batch)

    def add_photos(self, batch: Batch, photos: List[Photo]) -> None:
        for photo in photos:
            batch.photos[photo.file_hash] = photo
        self._save_batch(batch)

    def update_annotation(
        self,
        batch: Batch,
        point_id: str,
        status: AnnotationStatus,
        note: Optional[str] = None,
        author: str = "user",
        photo_id: Optional[str] = None,
    ) -> Optional[Annotation]:
        if point_id not in batch.annotations:
            return None

        annotation = batch.annotations[point_id]
        prev_state = annotation.model_dump(mode="json")

        annotation.status = status
        if photo_id:
            annotation.photo_id = photo_id
        if note:
            annotation.add_note(note, author)
        annotation.updated_at = datetime.now()

        undo_id = f"undo_{uuid.uuid4().hex[:8]}"
        batch.undo_stack.append(UndoRecord(
            id=undo_id,
            action_type="update_annotation",
            description=f"更新点位 {point_id} 状态为 {status.value}",
            previous_state={"point_id": point_id, "state": prev_state},
        ))

        self._save_batch(batch)
        return annotation

    def add_note_to_annotation(
        self,
        batch: Batch,
        point_id: str,
        note: str,
        author: str = "user",
    ) -> Optional[Annotation]:
        if point_id not in batch.annotations:
            return None

        annotation = batch.annotations[point_id]
        prev_state = annotation.model_dump(mode="json")

        annotation.add_note(note, author)
        annotation.updated_at = datetime.now()

        undo_id = f"undo_{uuid.uuid4().hex[:8]}"
        batch.undo_stack.append(UndoRecord(
            id=undo_id,
            action_type="add_note",
            description=f"为点位 {point_id} 添加备注",
            previous_state={"point_id": point_id, "state": prev_state},
        ))

        self._save_batch(batch)
        return annotation

    def undo_last(self, batch: Batch) -> Optional[UndoRecord]:
        if not batch.undo_stack:
            return None

        undo_record = batch.undo_stack.pop()

        if undo_record.action_type in ("update_annotation", "add_note"):
            prev_state = undo_record.previous_state.get("state")
            point_id = undo_record.previous_state.get("point_id")
            if point_id is None:
                ann_id = undo_record.previous_state.get("annotation_id", "")
                if ann_id.startswith("ann_"):
                    point_id = ann_id[4:]
            if point_id and prev_state and point_id in batch.annotations:
                batch.annotations[point_id] = Annotation.model_validate(prev_state)

        self._save_batch(batch)
        return undo_record

    def set_previews(self, batch: Batch, previews: List[PreviewItem]) -> None:
        batch.previews = previews
        self._save_batch(batch)

    def add_conflict(self, batch: Batch, conflict: Conflict) -> None:
        batch.conflicts.append(conflict)
        self._save_batch(batch)

    def clear_conflicts(self, batch: Batch) -> None:
        batch.conflicts = []
        self._save_batch(batch)

    def resolve_conflict(self, batch: Batch, conflict_id: str, resolution: str) -> Optional[Conflict]:
        for conflict in batch.conflicts:
            if conflict.id == conflict_id or conflict.id.startswith(conflict_id):
                conflict.resolved = True
                conflict.resolution = resolution
                self._save_batch(batch)
                return conflict
        return None

    def has_unresolved_conflicts(self, batch: Batch) -> bool:
        return any(not c.resolved for c in batch.conflicts)

    def update_config_version(self, batch: Batch, config_version: int) -> None:
        batch.config_version = config_version
        self._save_batch(batch)

    def update_all_batches_config_version(self, config_version: int) -> int:
        updated_count = 0
        if not self.batches_dir.exists():
            return updated_count

        for batch_file in self.batches_dir.glob("batch_*.json"):
            try:
                with open(batch_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("config_version") != config_version:
                    data["config_version"] = config_version
                    data["updated_at"] = datetime.now().isoformat()
                    with open(batch_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    updated_count += 1
            except Exception:
                continue

        if self._current_batch:
            self._current_batch.config_version = config_version

        return updated_count

    def set_file_paths(
        self,
        batch: Batch,
        csv_path: Optional[Path] = None,
        notes_json_path: Optional[Path] = None,
        photo_dir: Optional[Path] = None,
    ) -> None:
        if csv_path:
            batch.csv_path = Path(csv_path)
        if notes_json_path:
            batch.notes_json_path = Path(notes_json_path)
        if photo_dir:
            batch.photo_dir = Path(photo_dir)
        self._save_batch(batch)

    def add_operation_log(self, entry: OperationLogEntry) -> None:
        self.ensure_dirs()
        with open(self.operation_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def list_operation_logs(self, limit: int = 50, operation: Optional[str] = None) -> List[OperationLogEntry]:
        if not self.operation_log_file.exists():
            return []

        logs: List[OperationLogEntry] = []
        with open(self.operation_log_file, "r", encoding="utf-8") as f:
            for line in reversed(f.readlines()):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    entry = OperationLogEntry.model_validate(data)
                    if operation and entry.operation != operation:
                        continue
                    logs.append(entry)
                    if len(logs) >= limit:
                        break
                except Exception:
                    continue

        return logs

    def add_snapshot_import_log(self, log: SnapshotImportLog) -> None:
        self.add_operation_log(log)

    def list_snapshot_import_logs(self, limit: int = 50) -> List[SnapshotImportLog]:
        if not self.operation_log_file.exists():
            return []

        logs: List[SnapshotImportLog] = []
        with open(self.operation_log_file, "r", encoding="utf-8") as f:
            for line in reversed(f.readlines()):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("operation") != "snapshot_import":
                        continue
                    entry = SnapshotImportLog.model_validate(data)
                    logs.append(entry)
                    if len(logs) >= limit:
                        break
                except Exception:
                    continue

        return logs

    def _compute_points_hash(self, points: Dict[str, Point]) -> str:
        data = json.dumps({pid: p.model_dump(mode="json") for pid, p in sorted(points.items())}, sort_keys=True)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    def _compute_photos_hash(self, photos: Dict[str, Photo]) -> str:
        data = json.dumps({pid: p.model_dump(mode="json") for pid, p in sorted(photos.items())}, sort_keys=True)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    def _compute_conflict_summary(self, conflicts: List[Conflict]) -> ConflictSummary:
        by_reason: Dict[str, int] = {}
        for c in conflicts:
            by_reason[c.reason] = by_reason.get(c.reason, 0) + 1
        return ConflictSummary(
            total=len(conflicts),
            unresolved=sum(1 for c in conflicts if not c.resolved),
            by_reason=by_reason,
        )

    def save_draft(
        self,
        batch: Batch,
        name: str,
        description: str = "",
        config: Optional[ArchiverConfig] = None,
    ) -> ArchiveDraft:
        if not batch.previews:
            raise ValueError("当前批次没有预览数据，请先运行 preview 命令")

        self.ensure_dirs()

        draft_id = f"draft_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        source_info = DraftSourceInfo(
            batch_id=batch.id,
            batch_name=batch.name,
            batch_created_at=batch.created_at,
            points_count=len(batch.points),
            photos_count=len(batch.photos),
            points_hash=self._compute_points_hash(batch.points),
            photos_hash=self._compute_photos_hash(batch.photos),
        )

        conflict_summary = self._compute_conflict_summary(batch.conflicts)

        draft = ArchiveDraft(
            id=draft_id,
            name=name,
            description=description,
            source=source_info,
            config_version=batch.config_version,
            duplicate_strategy=config.duplicate_strategy if config else DuplicateStrategy.BLOCK,
            archive_action=config.archive_action if config else ArchiveAction.COPY,
            previews=list(batch.previews),
            conflicts=list(batch.conflicts),
            conflict_summary=conflict_summary,
            naming_template=config.naming_template if config else "",
            allowed_extensions=list(config.allowed_extensions) if config else [],
        )

        draft_file = self.drafts_dir / f"{draft_id}.json"
        with open(draft_file, "w", encoding="utf-8") as f:
            json.dump(draft.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

        return draft

    def list_drafts(self) -> List[Dict[str, Any]]:
        if not self.drafts_dir.exists():
            return []

        drafts = []
        for draft_file in sorted(self.drafts_dir.glob("draft_*.json"), reverse=True):
            try:
                with open(draft_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                drafts.append({
                    "id": data["id"],
                    "name": data["name"],
                    "created_at": data["created_at"],
                    "description": data.get("description", ""),
                    "source_batch_name": data["source"]["batch_name"],
                    "config_version": data["config_version"],
                    "previews_count": len(data.get("previews", [])),
                    "conflicts_total": data.get("conflict_summary", {}).get("total", 0),
                    "conflicts_unresolved": data.get("conflict_summary", {}).get("unresolved", 0),
                })
            except Exception:
                continue
        return drafts

    def load_draft(self, draft_id: str) -> Optional[ArchiveDraft]:
        if not self.drafts_dir.exists():
            return None

        draft_file = self.drafts_dir / f"{draft_id}.json"
        if not draft_file.exists():
            for f in self.drafts_dir.glob("draft_*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    if data.get("name") == draft_id:
                        return ArchiveDraft.model_validate(data)
                except Exception:
                    continue
            return None

        with open(draft_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ArchiveDraft.model_validate(data)

    def delete_draft(self, draft_id: str) -> bool:
        if not self.drafts_dir.exists():
            return False

        draft_file = self.drafts_dir / f"{draft_id}.json"
        if draft_file.exists():
            draft_file.unlink()
            return True

        for f in self.drafts_dir.glob("draft_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                if data.get("name") == draft_id:
                    f.unlink()
                    return True
            except Exception:
                continue

        return False

    def check_draft_restore_compatibility(
        self,
        draft: ArchiveDraft,
        current_batch: Batch,
        current_config: ArchiverConfig,
    ) -> DraftRestoreResult:
        result = DraftRestoreResult(success=True)

        if current_config.version != draft.config_version:
            result.warnings.append(
                f"规则版本不匹配：草稿基于 v{draft.config_version}，当前为 v{current_config.version}"
            )
            result.needs_confirmation = True

        current_points_hash = self._compute_points_hash(current_batch.points)
        if draft.source.points_hash != current_points_hash:
            result.warnings.append(
                f"点位内容已变化：草稿时点位 {draft.source.points_count} 个，当前 {len(current_batch.points)} 个"
            )
            result.needs_confirmation = True

        current_photos_hash = self._compute_photos_hash(current_batch.photos)
        if draft.source.photos_hash != current_photos_hash:
            result.warnings.append(
                f"照片扫描结果已变化：草稿时照片 {draft.source.photos_count} 张，当前 {len(current_batch.photos)} 张"
            )
            result.needs_confirmation = True

        if draft.duplicate_strategy != current_config.duplicate_strategy:
            result.warnings.append(
                f"重复策略不匹配：草稿为 {draft.duplicate_strategy.value}，当前为 {current_config.duplicate_strategy.value}"
            )

        if draft.archive_action != current_config.archive_action:
            result.warnings.append(
                f"归档动作不匹配：草稿为 {draft.archive_action.value}，当前为 {current_config.archive_action.value}"
            )

        if result.warnings:
            result.confirmation_prompt = (
                "检测到草稿与当前状态存在差异，恢复后将覆盖当前批次的预览和冲突数据。"
                "是否继续？"
            )

        return result

    def restore_draft(
        self,
        draft: ArchiveDraft,
        current_batch: Batch,
    ) -> Batch:
        current_batch.previews = list(draft.previews)
        current_batch.conflicts = list(draft.conflicts)
        current_batch.config_version = draft.config_version
        self._save_batch(current_batch)
        return current_batch
