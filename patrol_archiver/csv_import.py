from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import Point


@dataclass
class ImportError:
    line_number: int
    column: str
    message: str

    def __str__(self) -> str:
        return f"行 {self.line_number}，列 '{self.column}': {self.message}"


@dataclass
class ImportResult:
    points: List[Point] = field(default_factory=list)
    errors: List[ImportError] = field(default_factory=list)
    notes_data: Dict = field(default_factory=dict)
    notes_path: Optional[Path] = None
    csv_path: Optional[Path] = None

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def success_count(self) -> int:
        return len(self.points)


class CsvImporter:
    REQUIRED_COLUMNS = {"id", "name"}

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()

    def import_points(
        self,
        csv_path: Path,
        notes_json_path: Optional[Path] = None,
        existing_points: Optional[Dict[str, Point]] = None,
    ) -> ImportResult:
        csv_path = Path(csv_path).resolve()
        result = ImportResult(csv_path=csv_path)

        if not csv_path.exists():
            result.errors.append(ImportError(
                line_number=0,
                column="file",
                message=f"文件不存在: {csv_path}",
            ))
            return result

        notes_data = {}
        if notes_json_path:
            notes_json_path = Path(notes_json_path).resolve()
            if notes_json_path.exists():
                try:
                    with open(notes_json_path, "r", encoding="utf-8") as f:
                        notes_data = json.load(f)
                    result.notes_data = notes_data
                    result.notes_path = notes_json_path
                except json.JSONDecodeError as e:
                    result.errors.append(ImportError(
                        line_number=0,
                        column="notes_json",
                        message=f"JSON 格式错误: {e}",
                    ))
                    return result
            else:
                result.errors.append(ImportError(
                    line_number=0,
                    column="notes_json",
                    message=f"备注文件不存在: {notes_json_path}",
                ))
                return result

        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)

                if reader.fieldnames is None:
                    result.errors.append(ImportError(
                        line_number=1,
                        column="header",
                        message="无法读取 CSV 表头",
                    ))
                    return result

                header_set = {h.strip().lower() for h in reader.fieldnames}
                missing_cols = self.REQUIRED_COLUMNS - header_set
                if missing_cols:
                    result.errors.append(ImportError(
                        line_number=1,
                        column="header",
                        message=f"缺少必需列: {', '.join(sorted(missing_cols))}",
                    ))
                    return result

                for line_idx, row in enumerate(reader, start=2):
                    point, errors = self._parse_row(row, line_idx, reader.fieldnames)
                    if errors:
                        result.errors.extend(errors)
                        continue

                    if point is None:
                        continue

                    if existing_points and point.id in existing_points:
                        existing = existing_points[point.id]
                        point = self._merge_points(existing, point)

                    if notes_data and point.id in notes_data:
                        point.description = notes_data[point.id].get(
                            "description", point.description
                        )
                        point.custom_fields.update(
                            {k: v for k, v in notes_data[point.id].items()
                             if k not in ("description",)}
                        )

                    result.points.append(point)

        except UnicodeDecodeError:
            result.errors.append(ImportError(
                line_number=0,
                column="encoding",
                message="文件编码错误，请使用 UTF-8 编码",
            ))
        except csv.Error as e:
            result.errors.append(ImportError(
                line_number=0,
                column="format",
                message=f"CSV 格式错误: {e}",
            ))

        return result

    def _parse_row(
        self,
        row: Dict[str, str],
        line_number: int,
        fieldnames: List[str],
    ) -> Tuple[Optional[Point], List[ImportError]]:
        errors: List[ImportError] = []

        normalized_row = {}
        for col_idx, (key, value) in enumerate(row.items()):
            col_name = fieldnames[col_idx] if col_idx < len(fieldnames) else f"col_{col_idx}"
            normalized_row[col_name.strip().lower()] = value.strip() if value else ""

        point_id = normalized_row.get("id", "").strip()
        name = normalized_row.get("name", "").strip()

        if not point_id:
            errors.append(ImportError(
                line_number=line_number,
                column="id",
                message="点位 ID 不能为空",
            ))
            return None, errors

        if not name:
            errors.append(ImportError(
                line_number=line_number,
                column="name",
                message="点位名称不能为空",
            ))
            return None, errors

        category = normalized_row.get("category", "")
        location = normalized_row.get("location", "")
        description = normalized_row.get("description", "")

        custom_fields = {}
        for key, value in normalized_row.items():
            if key not in ("id", "name", "category", "location", "description") and value:
                custom_fields[key] = value

        point = Point(
            id=point_id,
            name=name,
            category=category,
            location=location,
            description=description,
            custom_fields=custom_fields,
        )

        return point, errors

    def _merge_points(self, existing: Point, new: Point) -> Point:
        return Point(
            id=existing.id,
            name=new.name or existing.name,
            category=new.category or existing.category,
            location=new.location or existing.location,
            description=new.description or existing.description,
            custom_fields={**existing.custom_fields, **new.custom_fields},
        )

    @staticmethod
    def format_errors(errors: List[ImportError]) -> str:
        if not errors:
            return ""
        lines = ["发现以下错误："]
        for err in errors:
            lines.append(f"  - {err}")
        return "\n".join(lines)
