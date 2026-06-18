from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from patrol_archiver.config import ConfigManager
from patrol_archiver.exporter import ReportExporter
from patrol_archiver.models import (
    ArchiverConfig,
    ConflictType,
    DuplicateStrategy,
    Point,
    PreviewItem,
)
from patrol_archiver.preview import PreviewGenerator
from patrol_archiver.store import BatchStore


class TestSnapshotExportImport:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace_a = self.tmp / "workspace_a"
        self.workspace_b = self.tmp / "workspace_b"
        self.workspace_a.mkdir()
        self.workspace_b.mkdir()

        self.snapshot_file = self.tmp / "rules_snapshot.json"

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_snapshot_contains_all_rules(self):
        mgr = ConfigManager(self.workspace_a)
        config = mgr.load()
        config = mgr.set_duplicate_strategy(DuplicateStrategy.RENAME)
        config = mgr.add_extension(".raw")

        snapshot = mgr.export_snapshot(
            output_path=self.snapshot_file,
            name="测试规则集",
            description="用于测试的规则快照",
            author="tester",
        )

        assert self.snapshot_file.exists()
        assert snapshot.name == "测试规则集"
        assert snapshot.description == "用于测试的规则快照"
        assert snapshot.created_by == "tester"
        assert snapshot.config_version == 3
        assert snapshot.duplicate_strategy == DuplicateStrategy.RENAME
        assert ".raw" in snapshot.allowed_extensions
        assert snapshot.naming_template == config.naming_template

        with open(self.snapshot_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["snapshot_id"] == snapshot.snapshot_id
        assert data["config_version"] == 3

    def test_import_snapshot_to_new_workspace(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.SKIP)
        mgr_a.add_extension(".heic")
        mgr_a.set_archive_dir(Path("./custom_archive"))
        mgr_a.export_snapshot(self.snapshot_file, name="迁移规则", author="user_a")

        mgr_b = ConfigManager(self.workspace_b)
        initial_config = mgr_b.load()
        assert initial_config.version == 1
        assert initial_config.duplicate_strategy == DuplicateStrategy.BLOCK

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        result = mgr_b.check_import_conflicts(snapshot)
        assert result.success is True
        assert result.applied is False
        conflict_types = {c.type for c in result.conflicts}
        assert ConflictType.STRATEGY_CONFLICT in conflict_types
        assert ConflictType.EXTENSION_CONFLICT in conflict_types

        applied = mgr_b.apply_snapshot(
            snapshot=snapshot,
            source_path=self.snapshot_file,
            author="user_b",
        )

        assert applied.version == 2
        assert applied.duplicate_strategy == DuplicateStrategy.SKIP
        assert ".heic" in applied.allowed_extensions
        assert applied.archive_dir == Path("./custom_archive")
        assert applied.last_snapshot is not None
        assert applied.last_snapshot.snapshot_name == "迁移规则"
        assert applied.last_snapshot.imported_by == "user_b"
        assert applied.last_snapshot.source_path == self.snapshot_file.resolve()

    def test_import_detects_all_conflict_types(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.add_extension(".raw")
        mgr_a.export_snapshot(self.snapshot_file, name="旧规则")

        mgr_b = ConfigManager(self.workspace_b)
        mgr_b.load()
        mgr_b.set_duplicate_strategy(DuplicateStrategy.BLOCK)
        mgr_b.set_archive_dir(Path("./new_archive"))
        mgr_b.add_extension(".heic")
        store_b = BatchStore(self.workspace_b)
        store_b.create_batch(name="existing_batch")

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        result = mgr_b.check_import_conflicts(snapshot, batch_store=store_b)

        conflict_types = {c.type for c in result.conflicts}
        assert ConflictType.VERSION_MISMATCH in conflict_types
        assert ConflictType.STRATEGY_CONFLICT in conflict_types
        assert ConflictType.EXTENSION_CONFLICT in conflict_types
        assert ConflictType.BATCH_EXISTS in conflict_types

    def test_import_conflict_fields_are_correct(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.export_snapshot(self.snapshot_file)

        mgr_b = ConfigManager(self.workspace_b)
        mgr_b.load()
        mgr_b.set_duplicate_strategy(DuplicateStrategy.BLOCK)

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        result = mgr_b.check_import_conflicts(snapshot)

        strategy_conflict = next(
            (c for c in result.conflicts if c.type == ConflictType.STRATEGY_CONFLICT and c.field == "duplicate_strategy"),
            None,
        )
        assert strategy_conflict is not None
        assert strategy_conflict.existing_value == "block"
        assert strategy_conflict.incoming_value == "rename"

    def test_operation_log_records_import(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.SKIP)
        mgr_a.export_snapshot(self.snapshot_file, name="log_test")

        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)
        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        result = mgr_b.check_import_conflicts(snapshot)

        config_before = mgr_b.load()
        applied = mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file, author="logger")

        from patrol_archiver.models import SnapshotImportLog
        log = SnapshotImportLog(
            id=f"log_{snapshot.snapshot_id}",
            operation="snapshot_import",
            author="logger",
            status="success",
            message=f"成功导入快照 {snapshot.name}",
            snapshot_id=snapshot.snapshot_id,
            snapshot_name=snapshot.name,
            snapshot_version=snapshot.config_version,
            source_path=self.snapshot_file.resolve(),
            conflicts_resolved=[c.type.value for c in result.conflicts],
            config_version_before=config_before.version,
            config_version_after=applied.version,
        )
        store_b.add_snapshot_import_log(log)

        logs = store_b.list_snapshot_import_logs()
        assert len(logs) == 1
        assert logs[0].snapshot_name == "log_test"
        assert logs[0].author == "logger"
        assert logs[0].config_version_before == 1
        assert logs[0].config_version_after == 2

    def test_snapshot_survives_cli_restart(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.set_archive_dir(Path("./persistent_archive"))
        mgr_a.export_snapshot(self.snapshot_file, name="持久化测试")

        mgr_b = ConfigManager(self.workspace_b)
        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file, author="user1")

        mgr_restart = ConfigManager(self.workspace_b)
        config = mgr_restart.load()

        assert config.duplicate_strategy == DuplicateStrategy.RENAME
        assert config.archive_dir == Path("./persistent_archive")
        assert config.last_snapshot is not None
        assert config.last_snapshot.snapshot_name == "持久化测试"
        assert config.last_snapshot.imported_by == "user1"

        mgr_restart2 = ConfigManager(self.workspace_b)
        config2 = mgr_restart2.load()
        assert config2.last_snapshot.snapshot_name == "持久化测试"

    def test_imported_rules_work_with_preview(self):
        mgr_a = ConfigManager(self.workspace_a)
        config_a = mgr_a.load()
        custom_template = "custom/{point.id}_{photo.taken_at:%Y%m%d}{photo.source_path.suffix}"
        config_a = mgr_a.update_naming_template(custom_template)
        config_a = mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.export_snapshot(self.snapshot_file, name="预览测试规则")

        mgr_b = ConfigManager(self.workspace_b)
        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file)

        config_b = mgr_b.load()
        assert config_b.naming_template == custom_template
        assert config_b.duplicate_strategy == DuplicateStrategy.RENAME

        store_b = BatchStore(self.workspace_b)
        batch = store_b.create_batch(name="preview_test", config_version=config_b.version)
        store_b.add_points(batch, [
            Point(id="P001", name="测试点位", category="测试"),
        ])

        (self.workspace_b / "photos").mkdir()
        test_photo = self.workspace_b / "photos" / "P001_test.jpg"
        test_photo.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")

        from patrol_archiver.scanner import PhotoScanner
        scanner = PhotoScanner(self.workspace_b, config_b.allowed_extensions)
        photos, _ = scanner.scan_photos(self.workspace_b / "photos")
        store_b.add_photos(batch, photos)

        preview_gen = PreviewGenerator(self.workspace_b, config_b)
        previews, conflicts = preview_gen.generate_preview(
            list(batch.photos.values()),
            batch.points,
            {},
        )

        assert len(previews) == 1
        target_path = str(previews[0].target_path).replace("\\", "/")
        assert "custom/P001_" in target_path

    def test_imported_rules_work_with_export(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.OVERWRITE)
        mgr_a.export_snapshot(self.snapshot_file, name="导出测试规则")

        mgr_b = ConfigManager(self.workspace_b)
        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file)

        config_b = mgr_b.load()
        store_b = BatchStore(self.workspace_b)
        batch = store_b.create_batch(name="export_test", config_version=config_b.version)
        store_b.add_points(batch, [
            Point(id="P001", name="测试点位1", category="A"),
            Point(id="P002", name="测试点位2", category="B"),
        ])

        exporter = ReportExporter(self.workspace_b)
        md_path = self.workspace_b / "report.md"
        exporter.export_markdown(batch, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "**配置版本**: v2" in content
        assert "P001" in content
        assert "P002" in content

    def test_multiple_imports_record_separate_logs(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.SKIP)
        mgr_a.export_snapshot(self.snapshot_file, name="第一次导入")

        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)

        snapshot1 = mgr_b.load_snapshot(self.snapshot_file)
        result1 = mgr_b.check_import_conflicts(snapshot1)
        mgr_b.apply_snapshot(snapshot1, source_path=self.snapshot_file, author="user1")

        from patrol_archiver.models import SnapshotImportLog
        log1 = SnapshotImportLog(
            id=f"log_{snapshot1.snapshot_id}",
            operation="snapshot_import",
            author="user1",
            status="success",
            message="第一次导入",
            snapshot_id=snapshot1.snapshot_id,
            snapshot_name="第一次导入",
            snapshot_version=snapshot1.config_version,
            source_path=self.snapshot_file.resolve(),
            conflicts_resolved=[c.type.value for c in result1.conflicts],
            config_version_before=1,
            config_version_after=2,
        )
        store_b.add_snapshot_import_log(log1)

        mgr_a2 = ConfigManager(self.workspace_a)
        mgr_a2.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a2.export_snapshot(self.snapshot_file, name="第二次导入")

        snapshot2 = mgr_b.load_snapshot(self.snapshot_file)
        result2 = mgr_b.check_import_conflicts(snapshot2)
        mgr_b.apply_snapshot(snapshot2, source_path=self.snapshot_file, author="user2")

        log2 = SnapshotImportLog(
            id=f"log_{snapshot2.snapshot_id}",
            operation="snapshot_import",
            author="user2",
            status="success",
            message="第二次导入",
            snapshot_id=snapshot2.snapshot_id,
            snapshot_name="第二次导入",
            snapshot_version=snapshot2.config_version,
            source_path=self.snapshot_file.resolve(),
            conflicts_resolved=[c.type.value for c in result2.conflicts],
            config_version_before=2,
            config_version_after=3,
        )
        store_b.add_snapshot_import_log(log2)

        logs = store_b.list_snapshot_import_logs()
        assert len(logs) == 2
        assert logs[0].snapshot_name == "第二次导入"
        assert logs[0].author == "user2"
        assert logs[1].snapshot_name == "第一次导入"
        assert logs[1].author == "user1"

        config = mgr_b.load()
        assert config.last_snapshot.snapshot_name == "第二次导入"
        assert config.version == 3

    def test_import_to_workspace_with_existing_batches(self):
        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)
        batch1 = store_b.create_batch(name="已存在批次1")
        batch2 = store_b.create_batch(name="已存在批次2")

        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.export_snapshot(self.snapshot_file, name="新规则")

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        result = mgr_b.check_import_conflicts(snapshot, batch_store=store_b)

        batch_conflict = next(
            (c for c in result.conflicts if c.type == ConflictType.BATCH_EXISTS),
            None,
        )
        assert batch_conflict is not None
        assert "2 个批次" in batch_conflict.existing_value

        mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file)

        batches_after = store_b.list_batches()
        assert len(batches_after) == 2

        reloaded1 = store_b.load_batch(batch1.id)
        reloaded2 = store_b.load_batch(batch2.id)
        assert reloaded1 is not None
        assert reloaded2 is not None

    def test_snapshot_export_version_matches_config(self):
        mgr = ConfigManager(self.workspace_a)
        mgr.load()
        assert mgr.get_config_version() == 1

        mgr.set_duplicate_strategy(DuplicateStrategy.RENAME)
        assert mgr.get_config_version() == 2

        mgr.add_extension(".raw")
        assert mgr.get_config_version() == 3

        snapshot = mgr.export_snapshot(self.snapshot_file)
        assert snapshot.config_version == 3

        mgr_restart = ConfigManager(self.workspace_a)
        assert mgr_restart.get_config_version() == 3

    def test_no_change_does_not_create_conflict(self):
        mgr_a = ConfigManager(self.workspace_a)
        config_a = mgr_a.load()
        mgr_a.export_snapshot(self.snapshot_file)

        mgr_b = ConfigManager(self.workspace_b)
        mgr_b.load()

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        result = mgr_b.check_import_conflicts(snapshot)

        assert len(result.conflicts) == 0
        assert "无冲突" in result.message

    def test_import_updates_all_batch_versions_immediately(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.export_snapshot(self.snapshot_file, name="全量更新测试")

        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)
        batch1 = store_b.create_batch(name="batch_old_1", config_version=1)
        batch2 = store_b.create_batch(name="batch_old_2", config_version=1)
        batch3 = store_b.create_batch(name="batch_old_3", config_version=1)
        store_b.set_current_batch(batch3.id)

        assert batch1.config_version == 1
        assert batch2.config_version == 1
        assert batch3.config_version == 1

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        applied = mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file, author="tester")

        updated_count = store_b.update_all_batches_config_version(applied.version)
        assert updated_count == 3

        reloaded1 = store_b.load_batch(batch1.id)
        reloaded2 = store_b.load_batch(batch2.id)
        reloaded3 = store_b.load_batch(batch3.id)
        assert reloaded1.config_version == applied.version
        assert reloaded2.config_version == applied.version
        assert reloaded3.config_version == applied.version

    def test_import_then_export_without_preview(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.export_snapshot(self.snapshot_file, name="导出测试")

        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)
        batch = store_b.create_batch(name="export_test", config_version=1)
        store_b.add_points(batch, [
            Point(id="P001", name="点位1", category="A"),
            Point(id="P002", name="点位2", category="B"),
        ])

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        applied = mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file, author="tester")
        store_b.update_all_batches_config_version(applied.version)

        exporter = ReportExporter(self.workspace_b)
        md_path = self.workspace_b / "test_export.md"
        exporter.export_markdown(batch, md_path, config_version=applied.version)

        content = md_path.read_text(encoding="utf-8")
        assert f"**配置版本**: v{applied.version}" in content
        assert "v1" not in content.split("**配置版本**:")[1].split("\n")[0]

        csv_path = self.workspace_b / "test_export.csv"
        exporter.export_csv(batch, csv_path, config_version=applied.version)
        csv_content = csv_path.read_text(encoding="utf-8-sig")
        assert f"v{applied.version}" in csv_content

    def test_switch_old_batch_then_export(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.OVERWRITE)
        mgr_a.export_snapshot(self.snapshot_file, name="切换批次测试")

        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)
        batch_old = store_b.create_batch(name="old_batch", config_version=1)
        store_b.add_points(batch_old, [Point(id="P001", name="旧点位", category="A")])
        batch_new = store_b.create_batch(name="new_batch", config_version=1)
        store_b.set_current_batch(batch_new.id)

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        applied = mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file, author="tester")
        store_b.update_all_batches_config_version(applied.version)

        store_b.set_current_batch(batch_old.id)
        reloaded_old = store_b.load_batch(batch_old.id)
        assert reloaded_old.config_version == applied.version

        exporter = ReportExporter(self.workspace_b)
        md_path = self.workspace_b / "old_batch_export.md"
        exporter.export_markdown(reloaded_old, md_path, config_version=applied.version)

        content = md_path.read_text(encoding="utf-8")
        assert f"**配置版本**: v{applied.version}" in content
        assert "P001" in content

    def test_import_cancel_does_not_affect_config(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.export_snapshot(self.snapshot_file, name="取消测试")

        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)
        batch = store_b.create_batch(name="test_batch", config_version=1)

        config_before = mgr_b.load()
        assert config_before.version == 1
        assert config_before.duplicate_strategy == DuplicateStrategy.BLOCK
        assert batch.config_version == 1

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        result = mgr_b.check_import_conflicts(snapshot)
        assert len(result.conflicts) > 0

        config_after_cancel = mgr_b.load()
        assert config_after_cancel.version == 1
        assert config_after_cancel.duplicate_strategy == DuplicateStrategy.BLOCK

        reloaded_batch = store_b.load_batch(batch.id)
        assert reloaded_batch.config_version == 1

    def test_exporter_uses_provided_config_version(self):
        mgr = ConfigManager(self.workspace_a)
        mgr.load()
        store = BatchStore(self.workspace_a)
        batch = store.create_batch(name="test_batch", config_version=1)
        store.add_points(batch, [Point(id="P001", name="测试", category="A")])

        exporter = ReportExporter(self.workspace_a)

        md_path_1 = self.workspace_a / "export_v1.md"
        exporter.export_markdown(batch, md_path_1, config_version=1)
        assert "**配置版本**: v1" in md_path_1.read_text(encoding="utf-8")

        md_path_99 = self.workspace_a / "export_v99.md"
        exporter.export_markdown(batch, md_path_99, config_version=99)
        assert "**配置版本**: v99" in md_path_99.read_text(encoding="utf-8")

        csv_path_5 = self.workspace_a / "export_v5.csv"
        exporter.export_csv(batch, csv_path_5, config_version=5)
        assert "v5" in csv_path_5.read_text(encoding="utf-8-sig")

    def test_import_updates_batch_show_version(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.SKIP)
        mgr_a.export_snapshot(self.snapshot_file, name="batch show 测试")

        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)
        batch1 = store_b.create_batch(name="b1", config_version=1)
        batch2 = store_b.create_batch(name="b2", config_version=1)
        batch3 = store_b.create_batch(name="b3", config_version=1)

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        applied = mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file)
        updated = store_b.update_all_batches_config_version(applied.version)
        assert updated == 3

        for batch_id in [batch1.id, batch2.id, batch3.id]:
            b = store_b.load_batch(batch_id)
            assert b.config_version == applied.version

    def test_no_batches_still_works(self):
        mgr_a = ConfigManager(self.workspace_a)
        mgr_a.load()
        mgr_a.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr_a.export_snapshot(self.snapshot_file)

        mgr_b = ConfigManager(self.workspace_b)
        store_b = BatchStore(self.workspace_b)

        snapshot = mgr_b.load_snapshot(self.snapshot_file)
        applied = mgr_b.apply_snapshot(snapshot, source_path=self.snapshot_file)

        updated = store_b.update_all_batches_config_version(applied.version)
        assert updated == 0

        new_batch = store_b.create_batch(name="first_batch", config_version=applied.version)
        assert new_batch.config_version == applied.version
