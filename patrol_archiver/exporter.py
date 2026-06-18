from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import (
    Annotation,
    AnnotationStatus,
    Batch,
    Conflict,
    NoteEntry,
    Point,
    PreviewItem,
)


class ReportExporter:
    STATUS_LABELS = {
        AnnotationStatus.PENDING: "待处理",
        AnnotationStatus.TO_REPHOTO: "待补拍",
        AnnotationStatus.CONFIRMED: "已确认",
        AnnotationStatus.IGNORED: "忽略",
        AnnotationStatus.ARCHIVED: "已归档",
    }

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()

    def export_markdown(
        self,
        batch: Batch,
        output_path: Path,
        include_notes: bool = True,
        include_conflicts: bool = True,
        config_version: Optional[int] = None,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        display_version = config_version if config_version is not None else batch.config_version

        lines = []
        lines.append(f"# 巡检照片归档报告")
        lines.append("")
        lines.append(f"**批次名称**: {batch.name}")
        lines.append(f"**批次 ID**: {batch.id}")
        lines.append(f"**创建时间**: {batch.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**更新时间**: {batch.updated_at.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**配置版本**: v{display_version}")
        lines.append("")

        summary = self._get_summary(batch)
        lines.append("## 概览")
        lines.append("")
        lines.append("| 统计项 | 数量 |")
        lines.append("|--------|------|")
        for key, value in summary.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")

        lines.append("## 点位状态统计")
        lines.append("")
        lines.append("| 状态 | 数量 | 比例 |")
        lines.append("|------|------|------|")
        status_stats = self._get_status_stats(batch)
        total = len(batch.points) if batch.points else 1
        for status, count in status_stats.items():
            label = self.STATUS_LABELS.get(status, status.value)
            pct = (count / total * 100) if total > 0 else 0
            lines.append(f"| {label} | {count} | {pct:.1f}% |")
        lines.append("")

        if batch.previews:
            lines.append("## 归档预览")
            lines.append("")
            lines.append("| 序号 | 点位 ID | 点位名称 | 源文件 | 目标路径 | 状态 |")
            lines.append("|------|---------|----------|--------|----------|------|")
            for i, preview in enumerate(batch.previews, 1):
                point_id = preview.point.id if preview.point else "N/A"
                point_name = preview.point.name if preview.point else "未知"
                status = "冲突" if preview.will_conflict else "正常"
                lines.append(
                    f"| {i} | {point_id} | {point_name} | "
                    f"`{preview.photo.file_name}` | `{preview.target_path.name}` | {status} |"
                )
            lines.append("")

        lines.append("## 点位详情")
        lines.append("")
        for point_id in sorted(batch.points.keys()):
            point = batch.points[point_id]
            annotation = batch.annotations.get(point_id)
            status_label = self.STATUS_LABELS.get(
                annotation.status if annotation else AnnotationStatus.PENDING,
                "未知",
            )

            lines.append(f"### {point.id} - {point.name}")
            lines.append("")
            lines.append(f"- **状态**: {status_label}")
            if point.category:
                lines.append(f"- **分类**: {point.category}")
            if point.location:
                lines.append(f"- **位置**: {point.location}")
            if point.description:
                lines.append(f"- **描述**: {point.description}")
            if point.custom_fields:
                for key, value in point.custom_fields.items():
                    lines.append(f"- **{key}**: {value}")

            if include_notes and annotation and annotation.notes:
                lines.append("")
                lines.append("#### 备注历史")
                lines.append("")
                for i, note in enumerate(annotation.notes, 1):
                    lines.append(
                        f"{i}. `{note.timestamp.strftime('%Y-%m-%d %H:%M:%S')}` "
                        f"({note.author}): {note.content}"
                    )

            lines.append("")

        if include_conflicts and batch.conflicts:
            lines.append("## 冲突列表")
            lines.append("")
            unresolved = [c for c in batch.conflicts if not c.resolved]
            resolved = [c for c in batch.conflicts if c.resolved]

            if unresolved:
                lines.append("### 未解决的冲突")
                lines.append("")
                for conflict in unresolved:
                    lines.append(f"- **目标**: `{conflict.target_path}`")
                    lines.append(f"  - **新源**: `{conflict.new_source}`")
                    if conflict.existing_source:
                        lines.append(f"  - **已有**: `{conflict.existing_source}`")
                    lines.append(f"  - **原因**: {conflict.reason}")
                    lines.append("")

            if resolved:
                lines.append("### 已解决的冲突")
                lines.append("")
                for conflict in resolved:
                    lines.append(f"- **目标**: `{conflict.target_path}`")
                    lines.append(f"  - **解决方案**: {conflict.resolution}")
                    lines.append(f"  - **原因**: {conflict.reason}")
                    lines.append("")

        lines.append("---")
        lines.append(f"*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return output_path

    def export_csv(
        self,
        batch: Batch,
        output_path: Path,
        include_notes: bool = True,
        config_version: Optional[int] = None,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        display_version = config_version if config_version is not None else batch.config_version

        custom_field_keys = set()
        for point in batch.points.values():
            custom_field_keys.update(point.custom_fields.keys())

        fieldnames = [
            "config_version",
            "point_id",
            "point_name",
            "category",
            "location",
            "description",
            "status",
            "status_label",
            "photo_count",
            "has_notes",
            "last_updated",
        ]
        fieldnames.extend(sorted(custom_field_keys))
        if include_notes:
            fieldnames.extend(["notes_count", "notes"])

        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for point_id in sorted(batch.points.keys()):
                point = batch.points[point_id]
                annotation = batch.annotations.get(point_id)

                status = annotation.status.value if annotation else AnnotationStatus.PENDING.value
                status_label = self.STATUS_LABELS.get(
                    annotation.status if annotation else AnnotationStatus.PENDING,
                    status,
                )

                photo_count = sum(
                    1 for p in batch.photos.values() if p.point_id == point_id
                )

                row = {
                    "config_version": f"v{display_version}",
                    "point_id": point.id,
                    "point_name": point.name,
                    "category": point.category,
                    "location": point.location,
                    "description": point.description,
                    "status": status,
                    "status_label": status_label,
                    "photo_count": photo_count,
                    "has_notes": "是" if annotation and annotation.notes else "否",
                    "last_updated": (
                        annotation.updated_at.strftime("%Y-%m-%d %H:%M:%S")
                        if annotation else ""
                    ),
                }

                for key in sorted(custom_field_keys):
                    row[key] = point.custom_fields.get(key, "")

                if include_notes:
                    if annotation and annotation.notes:
                        row["notes_count"] = len(annotation.notes)
                        notes_text = " | ".join(
                            f"[{n.timestamp.strftime('%Y-%m-%d %H:%M')}] ({n.author}) {n.content}"
                            for n in annotation.notes
                        )
                        row["notes"] = notes_text
                    else:
                        row["notes_count"] = 0
                        row["notes"] = ""

                writer.writerow(row)

        return output_path

    def _get_summary(self, batch: Batch) -> Dict[str, int]:
        return {
            "点位总数": len(batch.points),
            "照片总数": len(batch.photos),
            "标注数量": len(batch.annotations),
            "预览项数": len(batch.previews),
            "冲突总数": len(batch.conflicts),
            "未解决冲突": sum(1 for c in batch.conflicts if not c.resolved),
        }

    def _get_status_stats(self, batch: Batch) -> Dict[AnnotationStatus, int]:
        stats: Dict[AnnotationStatus, int] = {
            AnnotationStatus.PENDING: 0,
            AnnotationStatus.TO_REPHOTO: 0,
            AnnotationStatus.CONFIRMED: 0,
            AnnotationStatus.IGNORED: 0,
            AnnotationStatus.ARCHIVED: 0,
        }
        for annotation in batch.annotations.values():
            if annotation.status in stats:
                stats[annotation.status] += 1

        for point_id in batch.points:
            if point_id not in batch.annotations:
                stats[AnnotationStatus.PENDING] += 1

        return stats
