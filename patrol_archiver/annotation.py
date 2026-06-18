from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import (
    Annotation,
    AnnotationStatus,
    Batch,
    Point,
)
from .store import BatchStore


class AnnotationManager:
    def __init__(self, workspace: Path, store: BatchStore):
        self.workspace = Path(workspace).resolve()
        self.store = store

    def mark_point(
        self,
        batch: Batch,
        point_id: str,
        status: AnnotationStatus,
        note: Optional[str] = None,
        author: str = "user",
    ) -> Tuple[Optional[Annotation], Optional[str]]:
        if point_id not in batch.points:
            return None, f"点位 ID '{point_id}' 不存在"

        if point_id not in batch.annotations:
            batch.annotations[point_id] = Annotation(
                id=f"ann_{point_id}",
                point_id=point_id,
                status=AnnotationStatus.PENDING,
            )

        annotation = self.store.update_annotation(
            batch=batch,
            point_id=point_id,
            status=status,
            note=note,
            author=author,
        )

        return annotation, None

    def mark_to_rephoto(
        self,
        batch: Batch,
        point_id: str,
        note: Optional[str] = None,
        author: str = "user",
    ) -> Tuple[Optional[Annotation], Optional[str]]:
        return self.mark_point(batch, point_id, AnnotationStatus.TO_REPHOTO, note, author)

    def mark_confirmed(
        self,
        batch: Batch,
        point_id: str,
        note: Optional[str] = None,
        author: str = "user",
    ) -> Tuple[Optional[Annotation], Optional[str]]:
        return self.mark_point(batch, point_id, AnnotationStatus.CONFIRMED, note, author)

    def mark_ignored(
        self,
        batch: Batch,
        point_id: str,
        note: Optional[str] = None,
        author: str = "user",
    ) -> Tuple[Optional[Annotation], Optional[str]]:
        return self.mark_point(batch, point_id, AnnotationStatus.IGNORED, note, author)

    def mark_archived(
        self,
        batch: Batch,
        point_id: str,
        note: Optional[str] = None,
        author: str = "user",
    ) -> Tuple[Optional[Annotation], Optional[str]]:
        return self.mark_point(batch, point_id, AnnotationStatus.ARCHIVED, note, author)

    def add_note(
        self,
        batch: Batch,
        point_id: str,
        note: str,
        author: str = "user",
    ) -> Tuple[Optional[Annotation], Optional[str]]:
        if point_id not in batch.points:
            return None, f"点位 ID '{point_id}' 不存在"

        if point_id not in batch.annotations:
            batch.annotations[point_id] = Annotation(
                id=f"ann_{point_id}",
                point_id=point_id,
                status=AnnotationStatus.PENDING,
            )

        annotation = self.store.add_note_to_annotation(
            batch=batch,
            point_id=point_id,
            note=note,
            author=author,
        )

        return annotation, None

    def undo_last(self, batch: Batch) -> Tuple[bool, Optional[str], Optional[str]]:
        if not batch.undo_stack:
            return False, None, "撤销栈为空，没有可撤销的操作"

        undo_record = self.store.undo_last(batch)

        if undo_record is None:
            return False, None, "撤销失败"

        return True, undo_record.description, None

    def get_annotation(self, batch: Batch, point_id: str) -> Optional[Annotation]:
        return batch.annotations.get(point_id)

    def get_annotations_by_status(
        self,
        batch: Batch,
        status: AnnotationStatus,
    ) -> List[Tuple[Point, Annotation]]:
        result = []
        for point_id, annotation in batch.annotations.items():
            if annotation.status == status and point_id in batch.points:
                result.append((batch.points[point_id], annotation))
        return result

    def get_status_summary(self, batch: Batch) -> Dict[AnnotationStatus, int]:
        summary: Dict[AnnotationStatus, int] = {
            AnnotationStatus.PENDING: 0,
            AnnotationStatus.TO_REPHOTO: 0,
            AnnotationStatus.CONFIRMED: 0,
            AnnotationStatus.IGNORED: 0,
            AnnotationStatus.ARCHIVED: 0,
        }
        for annotation in batch.annotations.values():
            if annotation.status in summary:
                summary[annotation.status] += 1
        return summary

    def get_unannotated_points(self, batch: Batch) -> List[Point]:
        return [
            point for point_id, point in batch.points.items()
            if point_id not in batch.annotations
            or batch.annotations[point_id].status == AnnotationStatus.PENDING
        ]

    def get_points_with_notes(self, batch: Batch) -> List[Tuple[Point, Annotation]]:
        result = []
        for point_id, annotation in batch.annotations.items():
            if annotation.notes and point_id in batch.points:
                result.append((batch.points[point_id], annotation))
        return result

    def format_note_history(self, annotation: Annotation) -> str:
        if not annotation.notes:
            return "（无备注历史）"

        lines = ["备注历史："]
        for i, note in enumerate(annotation.notes, 1):
            lines.append(
                f"  {i}. [{note.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"({note.author}) {note.content}"
            )
        return "\n".join(lines)
