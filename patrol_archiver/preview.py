from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import (
    ArchiverConfig,
    Conflict,
    DuplicateStrategy,
    Photo,
    Point,
    PreviewItem,
)


class PreviewGenerator:
    def __init__(self, workspace: Path, config: ArchiverConfig):
        self.workspace = Path(workspace).resolve()
        self.config = config

    def generate_preview(
        self,
        photos: List[Photo],
        points: Dict[str, Point],
        existing_targets: Optional[Dict[Path, Photo]] = None,
    ) -> Tuple[List[PreviewItem], List[Conflict]]:
        previews: List[PreviewItem] = []
        conflicts: List[Conflict] = []

        existing_targets = existing_targets or {}
        target_paths: Dict[Path, Photo] = dict(existing_targets)

        temp_targets: Dict[Path, List[Photo]] = defaultdict(list)

        for photo in photos:
            point = None
            if photo.point_id and photo.point_id in points:
                point = points[photo.point_id]
            else:
                for pid, pt in points.items():
                    if pt.name in photo.file_name or pid in photo.file_name:
                        point = pt
                        photo.point_id = pid
                        break

            try:
                target_rel = self._render_template(photo, point)
                target_abs = (self.workspace / self.config.archive_dir / target_rel).resolve()
            except Exception as e:
                conflicts.append(Conflict(
                    id=f"conflict_{uuid.uuid4().hex[:8]}",
                    target_path=Path("error"),
                    new_source=photo.source_path,
                    reason=f"模板渲染失败: {e}",
                    resolved=False,
                ))
                continue

            temp_targets[target_abs].append(photo)

        for target_path, photo_list in temp_targets.items():
            if len(photo_list) > 1:
                for i, photo in enumerate(photo_list):
                    if self.config.duplicate_strategy == DuplicateStrategy.BLOCK:
                        conflicts.append(Conflict(
                            id=f"conflict_{uuid.uuid4().hex[:8]}",
                            target_path=target_path,
                            new_source=photo.source_path,
                            existing_source=photo_list[0].source_path if i > 0 else None,
                            reason=f"批次内冲突：多个照片映射到同一目标路径",
                            resolved=False,
                        ))
                        previews.append(PreviewItem(
                            photo=photo,
                            point=points.get(photo.point_id) if photo.point_id else None,
                            target_path=target_path,
                            will_conflict=True,
                            duplicate_strategy=DuplicateStrategy.BLOCK,
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.RENAME:
                        suffix = photo.source_path.suffix
                        stem = target_path.stem
                        parent = target_path.parent
                        renamed_path = parent / f"{stem}_{i+1}{suffix}"
                        target_paths[renamed_path] = photo
                        previews.append(PreviewItem(
                            photo=photo,
                            point=points.get(photo.point_id) if photo.point_id else None,
                            target_path=renamed_path,
                            will_conflict=False,
                            duplicate_strategy=DuplicateStrategy.RENAME,
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.SKIP:
                        if i == 0:
                            target_paths[target_path] = photo
                            previews.append(PreviewItem(
                                photo=photo,
                                point=points.get(photo.point_id) if photo.point_id else None,
                                target_path=target_path,
                                will_conflict=False,
                                duplicate_strategy=DuplicateStrategy.SKIP,
                            ))
                        else:
                            conflicts.append(Conflict(
                                id=f"conflict_{uuid.uuid4().hex[:8]}",
                                target_path=target_path,
                                new_source=photo.source_path,
                                existing_source=photo_list[0].source_path,
                                reason=f"批次内重复：已跳过",
                                resolved=True,
                                resolution="skip",
                            ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.OVERWRITE:
                        target_paths[target_path] = photo
                        previews.append(PreviewItem(
                            photo=photo,
                            point=points.get(photo.point_id) if photo.point_id else None,
                            target_path=target_path,
                            will_conflict=True,
                            duplicate_strategy=DuplicateStrategy.OVERWRITE,
                        ))
            else:
                photo = photo_list[0]
                point = points.get(photo.point_id) if photo.point_id else None

                if target_path in target_paths:
                    existing_photo = target_paths[target_path]
                    if existing_photo.file_hash == photo.file_hash:
                        conflicts.append(Conflict(
                            id=f"conflict_{uuid.uuid4().hex[:8]}",
                            target_path=target_path,
                            new_source=photo.source_path,
                            existing_source=existing_photo.source_path,
                            reason="目标已存在相同文件（哈希匹配）",
                            resolved=True,
                            resolution="identical_skip",
                        ))
                        previews.append(PreviewItem(
                            photo=photo,
                            point=point,
                            target_path=target_path,
                            will_conflict=False,
                            duplicate_strategy=self.config.duplicate_strategy,
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.BLOCK:
                        conflicts.append(Conflict(
                            id=f"conflict_{uuid.uuid4().hex[:8]}",
                            target_path=target_path,
                            new_source=photo.source_path,
                            existing_source=existing_photo.source_path,
                            reason="目标路径已存在不同文件",
                            resolved=False,
                        ))
                        previews.append(PreviewItem(
                            photo=photo,
                            point=point,
                            target_path=target_path,
                            will_conflict=True,
                            duplicate_strategy=DuplicateStrategy.BLOCK,
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.RENAME:
                        suffix = photo.source_path.suffix
                        stem = target_path.stem
                        parent = target_path.parent
                        counter = 1
                        while True:
                            renamed_path = parent / f"{stem}_{counter}{suffix}"
                            if renamed_path not in target_paths and not renamed_path.exists():
                                break
                            counter += 1
                        target_paths[renamed_path] = photo
                        previews.append(PreviewItem(
                            photo=photo,
                            point=point,
                            target_path=renamed_path,
                            will_conflict=False,
                            duplicate_strategy=DuplicateStrategy.RENAME,
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.SKIP:
                        conflicts.append(Conflict(
                            id=f"conflict_{uuid.uuid4().hex[:8]}",
                            target_path=target_path,
                            new_source=photo.source_path,
                            existing_source=existing_photo.source_path,
                            reason="目标已存在，已跳过",
                            resolved=True,
                            resolution="skip",
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.OVERWRITE:
                        target_paths[target_path] = photo
                        previews.append(PreviewItem(
                            photo=photo,
                            point=point,
                            target_path=target_path,
                            will_conflict=True,
                            duplicate_strategy=DuplicateStrategy.OVERWRITE,
                        ))
                elif target_path.exists():
                    existing_photo = self._try_load_existing_photo(target_path)
                    if existing_photo and existing_photo.file_hash == photo.file_hash:
                        conflicts.append(Conflict(
                            id=f"conflict_{uuid.uuid4().hex[:8]}",
                            target_path=target_path,
                            new_source=photo.source_path,
                            existing_source=target_path,
                            reason="目标已存在相同文件（哈希匹配）",
                            resolved=True,
                            resolution="identical_skip",
                        ))
                        previews.append(PreviewItem(
                            photo=photo,
                            point=point,
                            target_path=target_path,
                            will_conflict=False,
                            duplicate_strategy=self.config.duplicate_strategy,
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.BLOCK:
                        conflicts.append(Conflict(
                            id=f"conflict_{uuid.uuid4().hex[:8]}",
                            target_path=target_path,
                            new_source=photo.source_path,
                            existing_source=target_path,
                            reason="目标路径已存在不同文件",
                            resolved=False,
                        ))
                        previews.append(PreviewItem(
                            photo=photo,
                            point=point,
                            target_path=target_path,
                            will_conflict=True,
                            duplicate_strategy=DuplicateStrategy.BLOCK,
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.RENAME:
                        suffix = photo.source_path.suffix
                        stem = target_path.stem
                        parent = target_path.parent
                        counter = 1
                        while True:
                            renamed_path = parent / f"{stem}_{counter}{suffix}"
                            if not renamed_path.exists() and renamed_path not in target_paths:
                                break
                            counter += 1
                        target_paths[renamed_path] = photo
                        previews.append(PreviewItem(
                            photo=photo,
                            point=point,
                            target_path=renamed_path,
                            will_conflict=False,
                            duplicate_strategy=DuplicateStrategy.RENAME,
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.SKIP:
                        conflicts.append(Conflict(
                            id=f"conflict_{uuid.uuid4().hex[:8]}",
                            target_path=target_path,
                            new_source=photo.source_path,
                            existing_source=target_path,
                            reason="目标已存在，已跳过",
                            resolved=True,
                            resolution="skip",
                        ))
                    elif self.config.duplicate_strategy == DuplicateStrategy.OVERWRITE:
                        target_paths[target_path] = photo
                        previews.append(PreviewItem(
                            photo=photo,
                            point=point,
                            target_path=target_path,
                            will_conflict=True,
                            duplicate_strategy=DuplicateStrategy.OVERWRITE,
                        ))
                else:
                    target_paths[target_path] = photo
                    previews.append(PreviewItem(
                        photo=photo,
                        point=point,
                        target_path=target_path,
                        will_conflict=False,
                        duplicate_strategy=self.config.duplicate_strategy,
                    ))

        return previews, conflicts

    def _render_template(self, photo: Photo, point: Optional[Point]) -> Path:
        template = self.config.naming_template

        class _PhotoWrapper:
            def __init__(self, photo: Photo):
                self._photo = photo
                self.source_path = photo.source_path
                self.file_name = photo.file_name
                self.file_size = photo.file_size
                self.file_hash = photo.file_hash
                self.taken_at = photo.taken_at or datetime.now()
                self.camera = photo.camera or ""
                self.point_id = photo.point_id or ""

        class _PointWrapper:
            def __init__(self, point: Optional[Point]):
                self._point = point
                self.id = point.id if point else "UNKNOWN"
                self.name = point.name if point else "未命名点位"
                self.category = point.category if point else "未分类"
                self.location = point.location if point else ""
                self.description = point.description if point else ""

        photo_wrap = _PhotoWrapper(photo)
        point_wrap = _PointWrapper(point)

        rendered = template.format(photo=photo_wrap, point=point_wrap)
        return Path(rendered)

    def _try_load_existing_photo(self, path: Path) -> Optional[Photo]:
        try:
            if path.exists() and path.is_file():
                return Photo.from_path(path)
        except Exception:
            pass
        return None

    def scan_existing_archive(self) -> Dict[Path, Photo]:
        archive_dir = (self.workspace / self.config.archive_dir).resolve()
        result: Dict[Path, Photo] = {}

        if not archive_dir.exists():
            return result

        for path in archive_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in self.config.allowed_extensions:
                try:
                    photo = Photo.from_path(path)
                    result[path.resolve()] = photo
                except Exception:
                    continue

        return result
