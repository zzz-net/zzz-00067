from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import ArchiverConfig, DuplicateStrategy, ArchiveAction


class ConfigManager:
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
            self.save()

        return self._config

    def save(self, config: Optional[ArchiverConfig] = None) -> None:
        if config is not None:
            self._config = config
            config.version += 1

        if self._config is None:
            self._config = ArchiverConfig()

        self.ensure_dirs()

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self._config.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

    def update_naming_template(self, template: str) -> ArchiverConfig:
        config = self.load()
        config.naming_template = template
        self.save(config)
        return config

    def add_extension(self, ext: str) -> ArchiverConfig:
        config = self.load()
        ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        if ext not in config.allowed_extensions:
            config.allowed_extensions.append(ext)
            self.save(config)
        return config

    def remove_extension(self, ext: str) -> ArchiverConfig:
        config = self.load()
        ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        if ext in config.allowed_extensions:
            config.allowed_extensions.remove(ext)
            self.save(config)
        return config

    def set_duplicate_strategy(self, strategy: DuplicateStrategy) -> ArchiverConfig:
        config = self.load()
        config.duplicate_strategy = strategy
        self.save(config)
        return config

    def set_archive_action(self, action: ArchiveAction) -> ArchiverConfig:
        config = self.load()
        config.archive_action = action
        self.save(config)
        return config

    def set_archive_dir(self, path: Path) -> ArchiverConfig:
        config = self.load()
        config.archive_dir = Path(path)
        self.save(config)
        return config

    def set_photo_dir(self, path: Path) -> ArchiverConfig:
        config = self.load()
        config.photo_dir = Path(path)
        self.save(config)
        return config

    def set_notes_json(self, path: Path) -> ArchiverConfig:
        config = self.load()
        config.notes_json = Path(path)
        self.save(config)
        return config

    def set_points_csv(self, path: Path) -> ArchiverConfig:
        config = self.load()
        config.points_csv = Path(path)
        self.save(config)
        return config

    def get_config_version(self) -> int:
        config = self.load()
        return config.version
