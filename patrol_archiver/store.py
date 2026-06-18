from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    Annotation,
    AnnotationStatus,
    ArchiverConfig,
    Batch,
    Conflict,
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
        self.current_batch_file = self.data_dir / "current_batch.json"
        self.operation_log_file = self.data_dir / "operation_log.jsonl"
        self._current_batch: Optional[Batch] = None

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)

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
