from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from patrol_archiver.annotation import AnnotationManager
from patrol_archiver.config import ConfigManager
from patrol_archiver.models import (
    Annotation,
    AnnotationStatus,
    ArchiverConfig,
    Batch,
    DuplicateStrategy,
    Point,
)
from patrol_archiver.store import BatchStore


class TestUndoRestoresState:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = BatchStore(self.tmp)
        self.batch = self.store.create_batch(name="test_undo")
        self.manager = AnnotationManager(self.tmp, self.store)
        self.store.add_points(self.batch, [
            Point(id="P001", name="点位1", category="A"),
            Point(id="P002", name="点位2", category="B"),
        ])

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_confirmed_then_ignored_then_undo_restores_confirmed(self):
        self.manager.mark_point(self.batch, "P001", AnnotationStatus.CONFIRMED, note="初次确认")
        ann = self.batch.annotations["P001"]
        assert ann.status == AnnotationStatus.CONFIRMED
        assert len(ann.notes) == 1
        assert ann.notes[0].content == "初次确认"

        self.manager.mark_point(self.batch, "P001", AnnotationStatus.IGNORED, note="误标忽略")
        ann = self.batch.annotations["P001"]
        assert ann.status == AnnotationStatus.IGNORED
        assert len(ann.notes) == 2

        success, desc, error = self.manager.undo_last(self.batch)
        assert success is True
        assert error is None

        reloaded = self.store.load_batch(self.batch.id)
        restored = reloaded.annotations["P001"]
        assert restored.status == AnnotationStatus.CONFIRMED
        assert len(restored.notes) == 1
        assert restored.notes[0].content == "初次确认"

    def test_undo_add_note_removes_the_note(self):
        self.manager.mark_point(self.batch, "P001", AnnotationStatus.CONFIRMED, note="ok")
        self.manager.add_note(self.batch, "P001", "追加备注")

        ann = self.batch.annotations["P001"]
        assert len(ann.notes) == 2

        success, _, _ = self.manager.undo_last(self.batch)
        assert success is True

        reloaded = self.store.load_batch(self.batch.id)
        restored = reloaded.annotations["P001"]
        assert len(restored.notes) == 1
        assert restored.notes[0].content == "ok"

    def test_empty_undo_returns_clear_message(self):
        success, desc, error = self.manager.undo_last(self.batch)
        assert success is False
        assert error == "撤销栈为空，没有可撤销的操作"

    def test_undo_does_not_affect_other_points(self):
        self.manager.mark_point(self.batch, "P001", AnnotationStatus.CONFIRMED, note="P001确认")
        self.manager.mark_point(self.batch, "P002", AnnotationStatus.IGNORED, note="P002忽略")

        self.manager.undo_last(self.batch)

        reloaded = self.store.load_batch(self.batch.id)
        assert reloaded.annotations["P001"].status == AnnotationStatus.CONFIRMED
        assert reloaded.annotations["P002"].status == AnnotationStatus.PENDING

    def test_multiple_undo_chains_correctly(self):
        self.manager.mark_point(self.batch, "P001", AnnotationStatus.CONFIRMED, note="第一步")
        self.manager.mark_point(self.batch, "P001", AnnotationStatus.IGNORED, note="第二步")
        self.manager.add_note(self.batch, "P001", "第三步备注")

        self.manager.undo_last(self.batch)
        reloaded = self.store.load_batch(self.batch.id)
        assert reloaded.annotations["P001"].status == AnnotationStatus.IGNORED
        assert len(reloaded.annotations["P001"].notes) == 2

        self.manager.undo_last(reloaded)
        reloaded = self.store.load_batch(self.batch.id)
        assert reloaded.annotations["P001"].status == AnnotationStatus.CONFIRMED
        assert len(reloaded.annotations["P001"].notes) == 1


class TestConfigVersionPersists:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config_dir = self.tmp / ".patrol-archiver"

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_initial_version_is_1(self):
        mgr = ConfigManager(self.tmp)
        config = mgr.load()
        assert config.version == 1

    def test_set_duplicate_strategy_increments_version(self):
        mgr = ConfigManager(self.tmp)
        mgr.load()
        config = mgr.set_duplicate_strategy(DuplicateStrategy.RENAME)
        assert config.version == 2

        mgr2 = ConfigManager(self.tmp)
        config2 = mgr2.load()
        assert config2.version == 2
        assert config2.duplicate_strategy == DuplicateStrategy.RENAME

    def test_multiple_changes_increment_sequentially(self):
        mgr = ConfigManager(self.tmp)
        mgr.load()
        mgr.set_duplicate_strategy(DuplicateStrategy.SKIP)
        mgr.set_archive_dir(Path("/tmp/archive"))
        mgr.set_duplicate_strategy(DuplicateStrategy.BLOCK)

        mgr2 = ConfigManager(self.tmp)
        config = mgr2.load()
        assert config.version == 4
        assert config.duplicate_strategy == DuplicateStrategy.BLOCK

    def test_batch_config_version_synced_on_rule_change(self):
        store = BatchStore(self.tmp)
        batch = store.create_batch(name="test_version")
        assert batch.config_version == 1

        mgr = ConfigManager(self.tmp)
        config = mgr.set_duplicate_strategy(DuplicateStrategy.RENAME)
        assert config.version == 2

        store.update_config_version(batch, config.version)

        reloaded = store.load_batch(batch.id)
        assert reloaded.config_version == 2

    def test_version_survives_cli_restart(self):
        mgr = ConfigManager(self.tmp)
        mgr.load()
        mgr.set_duplicate_strategy(DuplicateStrategy.RENAME)
        mgr.set_duplicate_strategy(DuplicateStrategy.SKIP)

        mgr_restart = ConfigManager(self.tmp)
        config = mgr_restart.load()
        assert config.version == 3

        mgr_restart.set_duplicate_strategy(DuplicateStrategy.BLOCK)
        mgr_final = ConfigManager(self.tmp)
        assert mgr_final.load().version == 4

    def test_add_extension_increments_version(self):
        mgr = ConfigManager(self.tmp)
        mgr.load()
        config = mgr.add_extension(".raw")
        assert config.version == 2

        mgr2 = ConfigManager(self.tmp)
        assert mgr2.load().version == 2

    def test_no_change_no_increment(self):
        mgr = ConfigManager(self.tmp)
        mgr.load()
        config = mgr.add_extension(".jpg")
        assert config.version == 1
