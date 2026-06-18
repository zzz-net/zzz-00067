from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import (
    AnnotationStatus,
    ArchiverConfig,
    ArchiveAction,
    Batch,
    DuplicateStrategy,
    PreviewItem,
)
from .store import BatchStore


@dataclass
class ArchiveResult:
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    failures: List[Tuple[Path, str]] = field(default_factory=list)
    successes: List[Tuple[Path, Path]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.success_count + self.failed_count + self.skipped_count


class Archiver:
    def __init__(self, workspace: Path, config: ArchiverConfig, store: BatchStore):
        self.workspace = Path(workspace).resolve()
        self.config = config
        self.store = store

    def validate_previews(self, batch: Batch) -> Tuple[bool, List[str]]:
        errors: List[str] = []

        if not batch.previews:
            errors.append("没有可归档的预览项，请先生成预览")
            return False, errors

        unresolved_conflicts = [c for c in batch.conflicts if not c.resolved]

        if unresolved_conflicts:
            errors.append(f"存在 {len(unresolved_conflicts)} 个未解决的冲突，归档已被阻止")
            for conflict in unresolved_conflicts:
                errors.append(
                    f"  - 目标: {conflict.target_path}\n"
                    f"    源: {conflict.new_source}\n"
                    f"    原因: {conflict.reason}"
                )
            return False, errors

        target_paths_seen: Dict[Path, PreviewItem] = {}
        for preview in batch.previews:
            if preview.will_conflict and preview.duplicate_strategy == DuplicateStrategy.BLOCK:
                errors.append(
                    f"预览项存在冲突（策略为 BLOCK）：\n"
                    f"  源: {preview.photo.source_path}\n"
                    f"  目标: {preview.target_path}"
                )

            if preview.target_path in target_paths_seen:
                existing = target_paths_seen[preview.target_path]
                errors.append(
                    f"同一目标路径被多个照片映射：\n"
                    f"  目标: {preview.target_path}\n"
                    f"  源1: {existing.photo.source_path}\n"
                    f"  源2: {preview.photo.source_path}"
                )
            target_paths_seen[preview.target_path] = preview

        return len(errors) == 0, errors

    def execute_archive(
        self,
        batch: Batch,
        confirmed: bool = False,
        author: str = "user",
    ) -> ArchiveResult:
        result = ArchiveResult()

        if not confirmed:
            result.skipped_count = len(batch.previews)
            return result

        is_valid, errors = self.validate_previews(batch)
        if not is_valid:
            for err in errors:
                result.failures.append((Path("error"), err))
            result.failed_count = len(errors)
            return result

        for preview in batch.previews:
            if preview.will_conflict and preview.duplicate_strategy == DuplicateStrategy.SKIP:
                result.skipped_count += 1
                continue

            if preview.will_conflict and preview.duplicate_strategy == DuplicateStrategy.BLOCK:
                result.failures.append((
                    preview.photo.source_path,
                    f"冲突阻止：目标 {preview.target_path} 已存在",
                ))
                result.failed_count += 1
                continue

            try:
                target_path = preview.target_path
                target_path.parent.mkdir(parents=True, exist_ok=True)

                action = getattr(preview, "archive_action", self.config.archive_action)
                if action == ArchiveAction.MOVE:
                    shutil.move(str(preview.photo.source_path), str(target_path))
                else:
                    shutil.copy2(str(preview.photo.source_path), str(target_path))

                result.successes.append((preview.photo.source_path, target_path))
                result.success_count += 1

                if preview.point and preview.point.id in batch.annotations:
                    self.store.update_annotation(
                        batch=batch,
                        point_id=preview.point.id,
                        status=AnnotationStatus.ARCHIVED,
                        note=f"照片已归档到 {target_path}",
                        author=author,
                        photo_id=preview.photo.file_hash,
                    )

            except Exception as e:
                result.failures.append((
                    preview.photo.source_path,
                    f"归档失败: {e}",
                ))
                result.failed_count += 1

        return result

    def dry_run(self, batch: Batch) -> ArchiveResult:
        result = ArchiveResult()

        for preview in batch.previews:
            if preview.will_conflict and preview.duplicate_strategy == DuplicateStrategy.SKIP:
                result.skipped_count += 1
            elif preview.will_conflict and preview.duplicate_strategy == DuplicateStrategy.BLOCK:
                result.failures.append((
                    preview.photo.source_path,
                    f"将被阻止：目标 {preview.target_path} 已存在",
                ))
                result.failed_count += 1
            else:
                result.successes.append((preview.photo.source_path, preview.target_path))
                result.success_count += 1

        return result

    def get_archive_stats(self, batch: Batch) -> Dict[str, int]:
        stats = {
            "total_previews": len(batch.previews),
            "will_conflict": 0,
            "will_skip": 0,
            "will_block": 0,
            "will_overwrite": 0,
            "will_rename": 0,
            "can_archive": 0,
        }

        for preview in batch.previews:
            if preview.will_conflict:
                stats["will_conflict"] += 1
                if preview.duplicate_strategy == DuplicateStrategy.SKIP:
                    stats["will_skip"] += 1
                elif preview.duplicate_strategy == DuplicateStrategy.BLOCK:
                    stats["will_block"] += 1
                elif preview.duplicate_strategy == DuplicateStrategy.OVERWRITE:
                    stats["will_overwrite"] += 1
                elif preview.duplicate_strategy == DuplicateStrategy.RENAME:
                    stats["will_rename"] += 1
            else:
                stats["can_archive"] += 1

        return stats
