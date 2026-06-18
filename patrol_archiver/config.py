from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .models import (
    ArchiverConfig,
    ArchiveAction,
    ConflictType,
    DuplicateStrategy,
    ImportResult,
    InvalidNamingTemplateError,
    LastSnapshotInfo,
    RuleSnapshot,
    SnapshotConflict,
)


class ConfigManager:
    VALID_POINT_ATTRS = {"id", "name", "category", "location", "description"}
    VALID_PHOTO_ATTRS = {
        "source_path", "file_name", "name", "file_size",
        "file_hash", "taken_at", "camera", "point_id",
    }
    PHOTO_ATTR_ALIASES = {"name": "file_name"}

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.config_dir = self.workspace / ".patrol-archiver"
        self.config_file = self.config_dir / "config.json"
        self._config: Optional[ArchiverConfig] = None

    def ensure_dirs(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> ArchiverConfig:
        if self._config is not None:
            return self._config

        self.ensure_dirs()

        if self.config_file.exists():
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._config = ArchiverConfig.model_validate(data)
        else:
            self._config = ArchiverConfig()
            self.ensure_dirs()
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._config.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

        return self._config

    def save(self, config: Optional[ArchiverConfig] = None) -> None:
        if config is not None:
            self._config = config.model_copy(deep=True)
            self._config.version += 1

        if self._config is None:
            self._config = ArchiverConfig()

        self.ensure_dirs()

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self._config.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

    def update_naming_template(self, template: str) -> ArchiverConfig:
        _, warnings = self.validate_naming_template(template)
        errors = [w for w in warnings if w.startswith("未知")]
        if errors:
            raise InvalidNamingTemplateError(template, errors)
        config = self.load()
        config.naming_template = template
        self.save(config)
        return self._config

    def add_extension(self, ext: str) -> ArchiverConfig:
        config = self.load()
        ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        if ext not in config.allowed_extensions:
            config.allowed_extensions.append(ext)
            self.save(config)
            return self._config
        return self._config

    def remove_extension(self, ext: str) -> ArchiverConfig:
        config = self.load()
        ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        if ext in config.allowed_extensions:
            config.allowed_extensions.remove(ext)
            self.save(config)
            return self._config
        return self._config

    def set_duplicate_strategy(self, strategy: DuplicateStrategy) -> ArchiverConfig:
        config = self.load()
        config.duplicate_strategy = strategy
        self.save(config)
        return self._config

    @classmethod
    def validate_naming_template(cls, template: str) -> Tuple[bool, List[str]]:
        """校验命名模板中使用的变量是否合法。

        返回 (是否合法, 警告列表)。
        注：photo.name 视为合法别名（等价于 photo.file_name），但会提示。
        """
        warnings: List[str] = []
        photo_refs = re.findall(r"\{photo\.([a-zA-Z_][a-zA-Z0-9_]*)", template)
        point_refs = re.findall(r"\{point\.([a-zA-Z_][a-zA-Z0-9_]*)", template)

        for attr in photo_refs:
            base_attr = attr.split(".")[0]
            if base_attr not in cls.VALID_PHOTO_ATTRS:
                warnings.append(
                    f"未知模板变量 {{photo.{attr}}}，"
                    f"photo 可用变量: {sorted(cls.VALID_PHOTO_ATTRS)}"
                )
            elif base_attr in cls.PHOTO_ATTR_ALIASES:
                canonical = cls.PHOTO_ATTR_ALIASES[base_attr]
                warnings.append(
                    f"变量 {{photo.{attr}}} 是别名，推荐使用 {{photo.{canonical}}}（两者等价）"
                )

        for attr in point_refs:
            base_attr = attr.split(".")[0]
            if base_attr not in cls.VALID_POINT_ATTRS:
                warnings.append(
                    f"未知模板变量 {{point.{attr}}}，"
                    f"point 可用变量: {sorted(cls.VALID_POINT_ATTRS)}"
                )

        return (len([w for w in warnings if w.startswith("未知")]) == 0, warnings)

    def set_archive_action(self, action: ArchiveAction) -> ArchiverConfig:
        config = self.load()
        config.archive_action = action
        self.save(config)
        return self._config

    def set_archive_dir(self, path: Path) -> ArchiverConfig:
        config = self.load()
        config.archive_dir = Path(path)
        self.save(config)
        return self._config

    def set_photo_dir(self, path: Path) -> ArchiverConfig:
        config = self.load()
        config.photo_dir = Path(path)
        self.save(config)
        return self._config

    def set_notes_json(self, path: Path) -> ArchiverConfig:
        config = self.load()
        config.notes_json = Path(path)
        self.save(config)
        return self._config

    def set_points_csv(self, path: Path) -> ArchiverConfig:
        config = self.load()
        config.points_csv = Path(path)
        self.save(config)
        return self._config

    def get_config_version(self) -> int:
        config = self.load()
        return config.version

    def export_snapshot(
        self,
        output_path: Path,
        name: str = "",
        description: str = "",
        author: str = "user",
    ) -> RuleSnapshot:
        config = self.load()

        snapshot = RuleSnapshot(
            snapshot_id=f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            created_at=datetime.now(),
            created_by=author,
            name=name or f"rules_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            description=description,
            config_version=config.version,
            naming_template=config.naming_template,
            allowed_extensions=list(config.allowed_extensions),
            duplicate_strategy=config.duplicate_strategy,
            archive_action=config.archive_action,
            archive_dir=config.archive_dir,
            photo_dir=config.photo_dir,
            notes_json=config.notes_json,
            points_csv=config.points_csv,
            default_author=config.default_author,
        )

        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(snapshot.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

        return snapshot

    def load_snapshot(self, snapshot_path: Path) -> RuleSnapshot:
        snapshot_path = Path(snapshot_path).resolve()
        if not snapshot_path.exists():
            raise FileNotFoundError(f"快照文件不存在: {snapshot_path}")

        with open(snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return RuleSnapshot.model_validate(data)

    def check_import_conflicts(
        self,
        snapshot: RuleSnapshot,
        batch_store=None,
    ) -> ImportResult:
        conflicts: List[SnapshotConflict] = []
        current_config = self.load()

        if current_config.version > snapshot.config_version:
            conflicts.append(SnapshotConflict(
                type=ConflictType.VERSION_MISMATCH,
                field="version",
                existing_value=f"v{current_config.version}",
                incoming_value=f"v{snapshot.config_version}",
                message=f"当前配置版本(v{current_config.version})高于快照版本(v{snapshot.config_version})，导入可能覆盖更新的配置",
            ))

        if current_config.naming_template != snapshot.naming_template:
            conflicts.append(SnapshotConflict(
                type=ConflictType.TEMPLATE_CONFLICT,
                field="naming_template",
                existing_value=current_config.naming_template,
                incoming_value=snapshot.naming_template,
                message="命名模板与当前配置不同",
            ))

        if current_config.duplicate_strategy != snapshot.duplicate_strategy:
            conflicts.append(SnapshotConflict(
                type=ConflictType.STRATEGY_CONFLICT,
                field="duplicate_strategy",
                existing_value=current_config.duplicate_strategy.value,
                incoming_value=snapshot.duplicate_strategy.value,
                message="重复处理策略与当前配置不同",
            ))

        if current_config.archive_action != snapshot.archive_action:
            conflicts.append(SnapshotConflict(
                type=ConflictType.STRATEGY_CONFLICT,
                field="archive_action",
                existing_value=current_config.archive_action.value,
                incoming_value=snapshot.archive_action.value,
                message="归档操作方式与当前配置不同",
            ))

        existing_exts = set(current_config.allowed_extensions)
        snapshot_exts = set(snapshot.allowed_extensions)
        if existing_exts != snapshot_exts:
            conflicts.append(SnapshotConflict(
                type=ConflictType.EXTENSION_CONFLICT,
                field="allowed_extensions",
                existing_value=", ".join(sorted(existing_exts)),
                incoming_value=", ".join(sorted(snapshot_exts)),
                message="允许的文件扩展名与当前配置不同",
            ))

        if current_config.version > 1 and not conflicts:
            conflicts.append(SnapshotConflict(
                type=ConflictType.CONFIG_EXISTS,
                field="config",
                existing_value=f"v{current_config.version}",
                incoming_value=f"v{snapshot.config_version}",
                message="当前工作区已有配置，导入将覆盖现有配置",
            ))

        if batch_store is not None:
            batches = batch_store.list_batches()
            if batches:
                conflicts.append(SnapshotConflict(
                    type=ConflictType.BATCH_EXISTS,
                    field="batches",
                    existing_value=f"{len(batches)} 个批次",
                    incoming_value="无",
                    message=f"当前工作区已有 {len(batches)} 个批次，这些批次仍会保留，但将使用新导入的规则",
                ))

        return ImportResult(
            success=True,
            applied=False,
            conflicts=conflicts,
            message=f"检测到 {len(conflicts)} 个冲突" if conflicts else "无冲突，可直接导入",
            snapshot=snapshot,
        )

    def apply_snapshot(
        self,
        snapshot: RuleSnapshot,
        source_path: Optional[Path] = None,
        author: str = "user",
    ) -> ArchiverConfig:
        _, warnings = self.validate_naming_template(snapshot.naming_template)
        errors = [w for w in warnings if w.startswith("未知")]
        if errors:
            raise InvalidNamingTemplateError(snapshot.naming_template, errors)

        config = self.load()

        config.naming_template = snapshot.naming_template
        config.allowed_extensions = list(snapshot.allowed_extensions)
        config.duplicate_strategy = snapshot.duplicate_strategy
        config.archive_action = snapshot.archive_action
        config.archive_dir = snapshot.archive_dir
        config.photo_dir = snapshot.photo_dir
        config.notes_json = snapshot.notes_json
        config.points_csv = snapshot.points_csv
        config.default_author = snapshot.default_author

        config.last_snapshot = LastSnapshotInfo(
            source_path=source_path,
            snapshot_version=snapshot.config_version,
            imported_at=datetime.now(),
            imported_by=author,
            snapshot_name=snapshot.name,
        )

        self.save(config)

        return self._config
