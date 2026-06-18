from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import List

from click.testing import CliRunner

from patrol_archiver.cli import cli
from patrol_archiver.store import BatchStore


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestDraftCliEndToEnd:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.runner = CliRunner()
        self.project = Path(__file__).resolve().parent.parent
        self.sample = self.project / "sample"

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args: List[str], input_text: str = ""):
        cmd_args = ["--workspace", str(self.workspace)] + args
        result = self.runner.invoke(cli, cmd_args, input=input_text)
        result.clean_output = _strip_ansi(result.output)
        return result

    def _setup_sample_data(self):
        self._run([
            "import", "--csv", str(self.sample / "points.csv"),
            "--notes", str(self.sample / "notes.json"), "--batch-name", "测试批次"
        ])
        self._run(["scan", "--dir", str(self.sample / "photos")])
        self._run(["preview"])

    def _get_draft_count(self) -> int:
        drafts_dir = self.workspace / ".patrol-archiver" / "drafts"
        if not drafts_dir.exists():
            return 0
        return len(list(drafts_dir.glob("draft_*.json")))

    def _get_batch_previews_count(self) -> int:
        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        return len(batch.previews) if batch else 0

    def _get_batch_conflicts_count(self) -> int:
        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        return len(batch.conflicts) if batch else 0

    def test_draft_save_requires_preview(self):
        """没有预览时保存草稿应该失败"""
        self._run([
            "import", "--csv", str(self.sample / "points.csv"),
            "--batch-name", "测试批次"
        ])
        result = self._run(["draft", "save", "草稿1"])
        assert result.exit_code != 0
        assert "没有预览数据" in result.output

    def test_draft_save_success(self):
        """正常保存草稿"""
        self._setup_sample_data()

        previews_before = self._get_batch_previews_count()
        conflicts_before = self._get_batch_conflicts_count()

        result = self._run(["draft", "save", "测试草稿", "-d", "测试描述"])
        assert result.exit_code == 0
        assert "草稿已保存" in result.output
        assert "测试草稿" in result.output
        assert "测试批次" in result.output
        assert str(previews_before) in result.output

        assert self._get_draft_count() == 1

    def test_draft_list_empty(self):
        """没有草稿时列表显示为空"""
        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        assert "暂无草稿" in result.output

    def test_draft_list_with_drafts(self):
        """列出草稿"""
        self._setup_sample_data()
        self._run(["draft", "save", "草稿1", "-d", "第一个草稿"])
        self._run(["draft", "save", "草稿2", "-d", "第二个草稿"])

        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        assert "草稿列表" in result.output
        assert "草稿1" in result.output
        assert "草稿2" in result.output
        assert "草稿列表" in result.output

    def test_draft_show_detail(self):
        """查看草稿详情"""
        self._setup_sample_data()
        self._run(["draft", "save", "详情测试草稿", "-d", "测试详情"])

        result = self._run(["draft", "show", "详情测试草稿"])
        assert result.exit_code == 0
        assert "草稿详情" in result.output
        assert "详情测试草稿" in result.output
        assert "测试详情" in result.output
        assert "规则版本" in result.output
        assert "重复策略" in result.output
        assert "预览项数" in result.output

    def test_draft_show_not_found(self):
        """查看不存在的草稿"""
        result = self._run(["draft", "show", "不存在的草稿"])
        assert result.exit_code != 0
        assert "草稿不存在" in result.output

    def test_draft_delete_success(self):
        """删除草稿"""
        self._setup_sample_data()
        self._run(["draft", "save", "待删除草稿"])
        assert self._get_draft_count() == 1

        result = self._run(["draft", "delete", "待删除草稿", "--force"])
        assert result.exit_code == 0
        assert "草稿已删除" in result.output
        assert "待删除草稿" in result.output
        assert self._get_draft_count() == 0

    def test_draft_delete_with_confirm(self):
        """删除草稿需要确认"""
        self._setup_sample_data()
        self._run(["draft", "save", "确认删除草稿"])

        result = self._run(["draft", "delete", "确认删除草稿"], input_text="n\n")
        assert result.exit_code == 0
        assert "已取消删除" in result.output
        assert self._get_draft_count() == 1

        result = self._run(["draft", "delete", "确认删除草稿"], input_text="y\n")
        assert result.exit_code == 0
        assert "草稿已删除" in result.output
        assert self._get_draft_count() == 0

    def test_draft_delete_not_found(self):
        """删除不存在的草稿"""
        result = self._run(["draft", "delete", "不存在", "--force"])
        assert result.exit_code != 0
        assert "草稿不存在" in result.output

    def test_draft_persists_across_restart(self):
        """跨重启草稿仍可读取"""
        self._setup_sample_data()
        self._run(["draft", "save", "持久化测试草稿", "-d", "测试跨重启"])

        drafts_dir = self.workspace / ".patrol-archiver" / "drafts"
        draft_files = list(drafts_dir.glob("draft_*.json"))
        assert len(draft_files) == 1

        with open(draft_files[0], "r", encoding="utf-8") as f:
            draft_data = json.load(f)
        assert draft_data["name"] == "持久化测试草稿"
        assert draft_data["description"] == "测试跨重启"

        store = BatchStore(self.workspace)
        store._current_batch = None
        loaded = store.load_draft(draft_data["id"])
        assert loaded is not None
        assert loaded.name == "持久化测试草稿"
        assert len(loaded.previews) > 0

    def test_draft_restore_version_mismatch_requires_confirm(self):
        """恢复时规则版本变化需要确认"""
        self._setup_sample_data()
        self._run(["draft", "save", "版本冲突草稿"])

        self._run(["rules", "set-duplicate", "rename"])

        result = self._run(["draft", "restore", "版本冲突草稿"], input_text="n\n")
        assert result.exit_code == 0
        assert "检测到以下差异" in result.output
        assert "规则版本不匹配" in result.output
        assert "已取消恢复" in result.output

    def test_draft_restore_force_skip_confirm(self):
        """强制恢复跳过确认"""
        self._setup_sample_data()
        previews_before = self._get_batch_previews_count()
        self._run(["draft", "save", "强制恢复草稿"])

        self._run(["rules", "set-duplicate", "skip"])
        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        store.set_previews(batch, [])
        store.clear_conflicts(batch)
        assert self._get_batch_previews_count() == 0

        result = self._run(["draft", "restore", "强制恢复草稿", "--force"])
        assert result.exit_code == 0
        assert "草稿已恢复" in result.output
        assert self._get_batch_previews_count() == previews_before

    def test_draft_restore_then_dry_run_archive(self):
        """恢复草稿后可以进行 dry-run 归档"""
        self._setup_sample_data()
        self._run(["rules", "set-duplicate", "rename"])
        self._run(["draft", "save", "归档测试草稿"])

        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        store.set_previews(batch, [])
        store.clear_conflicts(batch)

        result = self._run(["draft", "restore", "归档测试草稿", "--force"])
        assert result.exit_code == 0
        assert "草稿已恢复" in result.output

        result = self._run(["archive", "--dry-run"])
        assert result.exit_code == 0
        assert "试运行" in result.output or "dry-run" in result.output or "预览" in result.output
        assert "总计" in result.output
        assert "成功" in result.output

    def test_draft_no_residue_after_delete(self):
        """删除后不会残留"""
        self._setup_sample_data()
        self._run(["draft", "save", "残留测试草稿1"])
        self._run(["draft", "save", "残留测试草稿2"])
        assert self._get_draft_count() == 2

        drafts_dir = self.workspace / ".patrol-archiver" / "drafts"
        draft_files_before = set(f.name for f in drafts_dir.glob("draft_*.json"))

        self._run(["draft", "delete", "残留测试草稿1", "--force"])
        assert self._get_draft_count() == 1

        draft_files_after = set(f.name for f in drafts_dir.glob("draft_*.json"))
        deleted_files = draft_files_before - draft_files_after
        assert len(deleted_files) == 1

        store = BatchStore(self.workspace)
        drafts = store.list_drafts()
        assert len(drafts) == 1
        assert drafts[0]["name"] == "残留测试草稿2"

    def test_draft_restore_points_change_detected(self):
        """恢复时点位变化会被检测到"""
        self._setup_sample_data()
        self._run(["draft", "save", "点位变化草稿"])

        result = self._run([
            "import", "--csv", str(self.sample / "points.csv"),
            "--use-current", "--batch-name", "同批次"
        ])

        result = self._run(["draft", "restore", "点位变化草稿"], input_text="n\n")
        assert result.exit_code == 0
        assert "点位内容已变化" in result.output
        assert "已取消恢复" in result.output

    def test_draft_restore_photos_change_detected(self):
        """恢复时照片变化会被检测到"""
        self._setup_sample_data()
        self._run(["draft", "save", "照片变化草稿"])

        alt_photos_dir = self.tmp / "alt_photos"
        alt_photos_dir.mkdir()
        src_photo = self.sample / "photos" / "P001_20260615_093000.jpg"
        import shutil
        shutil.copy2(src_photo, alt_photos_dir / "only_one_photo.jpg")

        self._run(["scan", "--dir", str(alt_photos_dir)])

        result = self._run(["draft", "restore", "照片变化草稿"], input_text="n\n")
        assert result.exit_code == 0
        assert "照片扫描结果已变化" in result.output
        assert "已取消恢复" in result.output

    def test_draft_restore_strategy_mismatch_warning(self):
        """恢复时重复策略不匹配给出警告"""
        self._setup_sample_data()
        self._run(["draft", "save", "策略草稿"])

        self._run(["rules", "set-duplicate", "skip"])

        result = self._run(["draft", "restore", "策略草稿", "--force"])
        assert result.exit_code == 0
        assert "重复策略不匹配" in result.output

    def test_draft_save_with_description(self):
        """保存草稿带描述"""
        self._setup_sample_data()
        result = self._run(["draft", "save", "带描述草稿", "-d", "这是一个详细描述"])
        assert result.exit_code == 0
        assert "草稿已保存" in result.output

        result = self._run(["draft", "show", "带描述草稿"])
        assert result.exit_code == 0
        assert "这是一个详细描述" in result.output

    def test_draft_list_shows_correct_counts(self):
        """草稿列表显示正确的计数"""
        self._setup_sample_data()
        previews_count = self._get_batch_previews_count()
        conflicts_count = self._get_batch_conflicts_count()

        self._run(["draft", "save", "计数测试草稿"])

        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        assert str(previews_count) in result.output
        assert str(conflicts_count) in result.output

    def test_draft_restore_overwrites_current_state(self):
        """恢复草稿会覆盖当前状态"""
        self._setup_sample_data()
        self._run(["rules", "set-duplicate", "rename"])
        self._run(["preview"])
        self._run(["draft", "save", "恢复覆盖草稿"])

        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        previews_saved = len(batch.previews)
        conflicts_saved = len(batch.conflicts)

        store.set_previews(batch, [])
        store.clear_conflicts(batch)
        assert self._get_batch_previews_count() == 0
        assert self._get_batch_conflicts_count() == 0

        result = self._run(["draft", "restore", "恢复覆盖草稿", "--force"])
        assert result.exit_code == 0
        assert self._get_batch_previews_count() == previews_saved
        assert self._get_batch_conflicts_count() == conflicts_saved

    def test_draft_multiple_saves(self):
        """保存多个草稿"""
        self._setup_sample_data()
        for i in range(5):
            result = self._run(["draft", "save", f"草稿{i}"])
            assert result.exit_code == 0

        assert self._get_draft_count() == 5

        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        for i in range(5):
            assert f"草稿{i}" in result.output

    def test_draft_save_copy_then_change_to_move_restore_confirm_stays_copy(self):
        """save copy 草稿 -> 改成 move -> restore -> confirm 仍为 copy（源文件不被移动）"""
        self._setup_sample_data()

        self._run(["rules", "set-action", "copy"])
        self._run(["preview"])
        self._run(["draft", "save", "copy草稿"])

        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        source_paths_before = [p.photo.source_path for p in batch.previews]
        for sp in source_paths_before:
            assert Path(sp).exists(), f"归档前源文件应存在: {sp}"

        self._run(["rules", "set-action", "move"])

        result = self._run(["draft", "restore", "copy草稿", "--force"])
        assert result.exit_code == 0
        assert "copy" in result.clean_output.lower()
        assert "沿用草稿中的 copy" in result.clean_output or "归档动作: copy" in result.clean_output

        result = self._run(["archive", "--dry-run"])
        assert result.exit_code == 0
        assert "当前归档动作: copy" in result.clean_output
        assert "预览中保存的动作与当前配置不一致" in result.clean_output

        result = self._run(["archive", "--confirm"])
        assert result.exit_code == 0
        assert "当前归档动作: copy" in result.clean_output

        for sp in source_paths_before:
            assert Path(sp).exists(), f"copy 动作归档后源文件应仍然存在，但被移动了: {sp}"

    def test_draft_save_move_then_change_to_copy_restore_confirm_stays_move(self):
        """save move 草稿 -> 改成 copy -> restore -> confirm 仍为 move（源文件被移动）"""
        import shutil

        alt_photos_dir = self.tmp / "move_test_photos"
        alt_photos_dir.mkdir()
        for f in (self.sample / "photos").glob("*.jpg"):
            shutil.copy2(f, alt_photos_dir / f.name)

        self._run([
            "import", "--csv", str(self.sample / "points.csv"),
            "--batch-name", "move测试批次"
        ])
        self._run(["scan", "--dir", str(alt_photos_dir)])

        self._run(["rules", "set-action", "move"])
        self._run(["preview"])
        self._run(["draft", "save", "move草稿"])

        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        source_paths_before = [Path(p.photo.source_path) for p in batch.previews]
        for sp in source_paths_before:
            assert sp.exists(), f"归档前源文件应存在: {sp}"

        self._run(["rules", "set-action", "copy"])

        result = self._run(["draft", "restore", "move草稿", "--force"])
        assert result.exit_code == 0
        assert "move" in result.clean_output.lower()

        result = self._run(["archive", "--confirm"])
        assert result.exit_code == 0
        assert "当前归档动作: move" in result.clean_output

        for sp in source_paths_before:
            assert not sp.exists(), f"move 动作归档后源文件应被移动，但仍然存在: {sp}"

    def test_draft_persist_then_restore_dry_run_uses_draft_action(self):
        """跨重启后恢复草稿再 dry-run 沿用草稿动作"""
        self._setup_sample_data()

        self._run(["rules", "set-action", "copy"])
        self._run(["preview"])
        self._run(["draft", "save", "持久化copy草稿"])

        drafts_dir = self.workspace / ".patrol-archiver" / "drafts"
        draft_files = list(drafts_dir.glob("draft_*.json"))
        assert len(draft_files) == 1

        with open(draft_files[0], "r", encoding="utf-8") as f:
            draft_data = json.load(f)
        assert draft_data["archive_action"] == "copy"
        for preview in draft_data["previews"]:
            assert preview["archive_action"] == "copy"

        self._run(["rules", "set-action", "move"])

        new_store = BatchStore(self.workspace)
        new_store._current_batch = None
        loaded_draft = new_store.load_draft(draft_data["id"])
        assert loaded_draft is not None
        assert loaded_draft.archive_action.value == "copy"

        result = self._run(["draft", "restore", "持久化copy草稿", "--force"])
        assert result.exit_code == 0
        assert "归档动作: copy" in result.clean_output

        result = self._run(["archive", "--dry-run"])
        assert result.exit_code == 0
        assert "当前归档动作: copy" in result.clean_output
        assert "预览中保存的动作与当前配置不一致" in result.clean_output

    def test_draft_persist_then_restore_confirm_uses_draft_action(self):
        """跨重启后恢复草稿再 confirm 沿用草稿动作"""
        self._setup_sample_data()

        self._run(["rules", "set-action", "copy"])
        self._run(["preview"])
        self._run(["draft", "save", "重启后确认草稿"])

        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        source_paths_before = [Path(p.photo.source_path) for p in batch.previews]

        drafts_dir = self.workspace / ".patrol-archiver" / "drafts"
        draft_files = list(drafts_dir.glob("draft_*.json"))
        assert len(draft_files) == 1
        draft_id = draft_files[0].stem

        self._run(["rules", "set-action", "move"])

        store2 = BatchStore(self.workspace)
        store2._current_batch = None

        result = self._run(["draft", "restore", draft_id, "--force"])
        assert result.exit_code == 0

        result = self._run(["archive", "--confirm"])
        assert result.exit_code == 0
        assert "当前归档动作: copy" in result.clean_output

        for sp in source_paths_before:
            assert sp.exists(), f"copy 草稿归档后源文件应仍然存在，但被移动了: {sp}"

    def test_draft_restore_output_shows_action_discrepancy(self):
        """恢复草稿时如果动作与当前配置不一致，明确提示用户"""
        self._setup_sample_data()

        self._run(["rules", "set-action", "copy"])
        self._run(["rules", "set-duplicate", "block"])
        self._run(["preview"])
        self._run(["draft", "save", "差异提示草稿"])

        self._run(["rules", "set-action", "move"])
        self._run(["rules", "set-duplicate", "rename"])

        result = self._run(["draft", "restore", "差异提示草稿", "--force"])
        assert result.exit_code == 0
        assert "草稿中保存的规则与当前配置不一致" in result.clean_output
        assert "归档动作: 草稿为 copy" in result.clean_output
        assert "将沿用草稿中的 copy" in result.clean_output
        assert "重复策略: 草稿为 block" in result.clean_output
        assert "将沿用草稿中的 block" in result.clean_output
