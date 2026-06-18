from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .annotation import AnnotationManager
from .archiver import Archiver
from .config import ConfigManager
from .csv_import import CsvImporter
from .exporter import ReportExporter
from .models import (
    AnnotationStatus,
    ArchiveAction,
    ConflictType,
    DuplicateStrategy,
    SnapshotImportLog,
)
from .preview import PreviewGenerator
from .scanner import PhotoScanner
from .store import BatchStore

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

console = Console(force_terminal=True, color_system="auto", highlight=False, soft_wrap=True)


def get_workspace(ctx) -> Path:
    return Path(ctx.obj["workspace"]).resolve()


def sync_batch_config_version(workspace: Path, config_version: int) -> None:
    store = BatchStore(workspace)
    batch = store.get_current_batch()
    if batch:
        store.update_config_version(batch, config_version)


def get_or_create_batch(ctx, require_existing: bool = False):
    workspace = get_workspace(ctx)
    store = BatchStore(workspace)
    batch = store.get_current_batch()

    if batch is None:
        if require_existing:
            console.print("[red]错误：没有活动批次，请先创建或选择批次[/red]")
            sys.exit(1)
        config_mgr = ConfigManager(workspace)
        config = config_mgr.load()
        batch = store.create_batch(config_version=config.version)
        console.print(f"[green]已创建新批次:[/green] {batch.name} (ID: {batch.id})")

    ctx.obj["store"] = store
    ctx.obj["batch"] = batch
    return batch


@click.group()
@click.option(
    "--workspace",
    "-w",
    type=click.Path(path_type=Path),
    default=Path.cwd(),
    help="工作目录路径",
)
@click.pass_context
def cli(ctx, workspace: Path):
    """本地巡检照片归档 CLI 工具"""
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = Path(workspace).resolve()


@cli.group()
def batch():
    """批次管理"""
    pass


@batch.command("new")
@click.option("--name", "-n", help="批次名称")
@click.pass_context
def batch_new(ctx, name: Optional[str]):
    """创建新批次"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    store = BatchStore(workspace)
    batch = store.create_batch(name=name, config_version=config.version)
    console.print(f"[green]✓ 已创建批次:[/green] {batch.name}")
    console.print(f"  批次 ID: {batch.id}")
    console.print(f"  配置版本: v{batch.config_version}")


@batch.command("list")
@click.pass_context
def batch_list(ctx):
    """列出所有批次"""
    workspace = get_workspace(ctx)
    store = BatchStore(workspace)
    batches = store.list_batches()

    if not batches:
        console.print("[yellow]暂无批次[/yellow]")
        return

    current = store.get_current_batch()
    current_id = current.id if current else None

    table = Table(title="批次列表")
    table.add_column("当前", justify="center")
    table.add_column("批次名称")
    table.add_column("创建时间")
    table.add_column("点位", justify="right")
    table.add_column("照片", justify="right")
    table.add_column("冲突", justify="right")

    for b in batches:
        marker = "→" if b["id"] == current_id else ""
        table.add_row(
            marker,
            b["name"],
            b["created_at"],
            str(b["points_count"]),
            str(b["photos_count"]),
            str(b["conflicts_count"]),
        )

    console.print(table)


@batch.command("switch")
@click.argument("batch_id")
@click.pass_context
def batch_switch(ctx, batch_id: str):
    """切换到指定批次"""
    workspace = get_workspace(ctx)
    store = BatchStore(workspace)
    batch = store.set_current_batch(batch_id)

    if batch:
        console.print(f"[green]✓ 已切换到批次:[/green] {batch.name}")
    else:
        console.print(f"[red]✗ 批次不存在:[/red] {batch_id}")
        sys.exit(1)


@batch.command("show")
@click.pass_context
def batch_show(ctx):
    """显示当前批次信息"""
    batch = get_or_create_batch(ctx, require_existing=True)

    console.print(Panel.fit(
        f"[bold]{batch.name}[/bold]\n"
        f"ID: {batch.id}\n"
        f"创建时间: {batch.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"更新时间: {batch.updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"配置版本: v{batch.config_version}\n"
        f"点位数量: {len(batch.points)}\n"
        f"照片数量: {len(batch.photos)}\n"
        f"标注数量: {len(batch.annotations)}\n"
        f"预览项数: {len(batch.previews)}\n"
        f"冲突数量: {len(batch.conflicts)} ({sum(1 for c in batch.conflicts if not c.resolved)} 未解决)\n"
        f"撤销栈: {len(batch.undo_stack)} 条记录",
        title="当前批次信息",
    ))


@cli.command("import")
@click.option("--csv", "csv_path", type=click.Path(path_type=Path, exists=True), required=True, help="点位 CSV 文件路径")
@click.option("--notes", "notes_path", type=click.Path(path_type=Path), help="备注 JSON 文件路径")
@click.option("--new-batch/--use-current", default=True, help="是否创建新批次")
@click.option("--batch-name", help="新批次名称")
@click.pass_context
def import_points(ctx, csv_path: Path, notes_path: Optional[Path], new_batch: bool, batch_name: Optional[str]):
    """导入点位清单"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    store = BatchStore(workspace)

    if new_batch:
        batch = store.create_batch(name=batch_name, config_version=config.version)
        console.print(f"[green]✓ 已创建批次:[/green] {batch.name}")
    else:
        batch = store.get_current_batch()
        if batch is None:
            batch = store.create_batch(name=batch_name, config_version=config.version)
            console.print(f"[green]✓ 已创建新批次:[/green] {batch.name}")

    ctx.obj["batch"] = batch
    ctx.obj["store"] = store

    importer = CsvImporter(workspace)
    result = importer.import_points(csv_path, notes_path, existing_points=batch.points)

    if result.has_errors:
        console.print("[red]✗ 导入存在错误：[/red]")
        for err in result.errors:
            console.print(f"  [red]•[/red] 行 {err.line_number}, 列 '{err.column}': {err.message}")
        if result.success_count == 0:
            console.print("[red]导入失败，没有成功导入任何点位[/red]")
            sys.exit(1)
        console.print(f"[yellow]警告：部分数据导入失败，成功 {result.success_count} 条[/yellow]")
    else:
        console.print(f"[green]✓ 成功导入 {result.success_count} 个点位[/green]")

    store.add_points(batch, result.points)
    store.set_file_paths(
        batch,
        csv_path=csv_path,
        notes_json_path=notes_path,
    )

    table = Table(title="导入的点位")
    table.add_column("ID")
    table.add_column("名称")
    table.add_column("分类")
    table.add_column("位置")
    table.add_column("描述")

    for point in result.points[:10]:
        table.add_row(
            point.id,
            point.name,
            point.category or "-",
            point.location or "-",
            point.description[:30] + "..." if len(point.description) > 30 else point.description or "-",
        )

    if len(result.points) > 10:
        table.add_row("...", f"还有 {len(result.points) - 10} 条", "", "", "")

    console.print(table)


@cli.group()
def rules():
    """规则管理（命名模板、扩展名、重复策略等）"""
    pass


@rules.command("show")
@click.pass_context
def rules_show(ctx):
    """显示当前规则配置"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    content = (
        f"[bold]配置版本:[/bold] v{config.version}\n\n"
        f"[bold]命名模板:[/bold]\n  {config.naming_template}\n\n"
        f"[bold]允许的扩展名:[/bold] {', '.join(config.allowed_extensions)}\n\n"
        f"[bold]重复策略:[/bold] {config.duplicate_strategy.value}\n"
        f"[bold]归档方式:[/bold] {config.archive_action.value}\n\n"
        f"[bold]归档目录:[/bold] {config.archive_dir}\n"
        f"[bold]照片目录:[/bold] {config.photo_dir}\n"
        f"[bold]点位 CSV:[/bold] {config.points_csv}\n"
        f"[bold]备注 JSON:[/bold] {config.notes_json}"
    )

    if config.last_snapshot and config.last_snapshot.imported_at:
        ls = config.last_snapshot
        content += (
            f"\n\n[bold]最近快照导入:[/bold]\n"
            f"  快照名称: {ls.snapshot_name}\n"
            f"  快照版本: v{ls.snapshot_version}\n"
            f"  导入时间: {ls.imported_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"  导入人: {ls.imported_by}\n"
            f"  来源文件: {ls.source_path}"
        )

    console.print(Panel.fit(content, title="当前规则配置"))


@rules.command("set-template")
@click.argument("template")
@click.pass_context
def rules_set_template(ctx, template: str):
    """设置命名模板

    可用变量：
    - {point.id}: 点位ID
    - {point.name}: 点位名称
    - {point.category}: 点位分类
    - {point.location}: 点位位置
    - {photo.taken_at:%Y%m%d_%H%M%S}: 拍摄时间
    - {photo.source_path.suffix}: 源文件扩展名
    - {photo.file_name}: 源文件名
    """
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.update_naming_template(template)
    sync_batch_config_version(workspace, config.version)
    console.print(f"[green]✓ 命名模板已更新[/green]")
    console.print(f"  新版本: v{config.version}")
    console.print(f"  模板: {config.naming_template}")


@rules.command("add-ext")
@click.argument("extension")
@click.pass_context
def rules_add_ext(ctx, extension: str):
    """添加允许的文件扩展名"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.add_extension(extension)
    sync_batch_config_version(workspace, config.version)
    console.print(f"[green]✓ 已添加扩展名[/green]")
    console.print(f"  当前允许: {', '.join(config.allowed_extensions)}")


@rules.command("remove-ext")
@click.argument("extension")
@click.pass_context
def rules_remove_ext(ctx, extension: str):
    """移除允许的文件扩展名"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.remove_extension(extension)
    sync_batch_config_version(workspace, config.version)
    console.print(f"[green]✓ 已移除扩展名[/green]")
    console.print(f"  当前允许: {', '.join(config.allowed_extensions)}")


@rules.command("set-duplicate")
@click.argument(
    "strategy",
    type=click.Choice(["skip", "rename", "overwrite", "block"], case_sensitive=False),
)
@click.pass_context
def rules_set_duplicate(ctx, strategy: str):
    """设置重复照片策略

    - skip: 跳过重复
    - rename: 自动重命名
    - overwrite: 覆盖
    - block: 阻止执行（默认）
    """
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    strategy_enum = DuplicateStrategy(strategy.lower())
    config = config_mgr.set_duplicate_strategy(strategy_enum)
    sync_batch_config_version(workspace, config.version)
    console.print(f"[green]✓ 重复策略已设置为: {config.duplicate_strategy.value}[/green]")
    console.print(f"  配置版本: v{config.version}")


@rules.command("set-action")
@click.argument(
    "action",
    type=click.Choice(["move", "copy"], case_sensitive=False),
)
@click.pass_context
def rules_set_action(ctx, action: str):
    """设置归档操作方式

    - move: 移动文件
    - copy: 复制文件（默认）
    """
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    action_enum = ArchiveAction(action.lower())
    config = config_mgr.set_archive_action(action_enum)
    sync_batch_config_version(workspace, config.version)
    console.print(f"[green]✓ 归档方式已设置为: {config.archive_action.value}[/green]")
    console.print(f"  配置版本: v{config.version}")


@rules.command("set-archive-dir")
@click.argument("dir_path", type=click.Path(path_type=Path))
@click.pass_context
def rules_set_archive_dir(ctx, dir_path: Path):
    """设置归档输出目录"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.set_archive_dir(dir_path)
    sync_batch_config_version(workspace, config.version)
    console.print(f"[green]✓ 归档目录已设置为: {config.archive_dir}[/green]")
    console.print(f"  配置版本: v{config.version}")


@cli.group()
def snapshot():
    """规则快照管理（导出、导入、查看日志）"""
    pass


@snapshot.command("export")
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True, help="快照输出文件路径")
@click.option("--name", "-n", help="快照名称")
@click.option("--description", "-d", help="快照描述")
@click.option("--author", "-a", help="导出人")
@click.pass_context
def snapshot_export(ctx, output: Path, name: Optional[str], description: Optional[str], author: Optional[str]):
    """导出当前规则为快照文件"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    if author is None:
        author = config.default_author

    snapshot = config_mgr.export_snapshot(
        output_path=output,
        name=name or "",
        description=description or "",
        author=author,
    )

    console.print(f"[green]✓ 规则快照已导出[/green]")
    console.print(f"  快照ID: {snapshot.snapshot_id}")
    console.print(f"  快照名称: {snapshot.name}")
    console.print(f"  配置版本: v{snapshot.config_version}")
    console.print(f"  导出人: {snapshot.created_by}")
    console.print(f"  导出时间: {snapshot.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"  文件路径: {output.resolve()}")


@snapshot.command("import")
@click.option("--file", "-f", "snapshot_file", type=click.Path(path_type=Path, exists=True), required=True, help="快照文件路径")
@click.option("--author", "-a", help="导入人")
@click.option("--force", is_flag=True, help="强制导入（跳过冲突确认）")
@click.pass_context
def snapshot_import(ctx, snapshot_file: Path, author: Optional[str], force: bool):
    """导入规则快照（存在冲突时需确认）"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    store = BatchStore(workspace)

    try:
        snapshot = config_mgr.load_snapshot(snapshot_file)
    except Exception as e:
        console.print(f"[red]✗ 加载快照失败: {e}[/red]")
        sys.exit(1)

    result = config_mgr.check_import_conflicts(snapshot, batch_store=store)

    console.print(Panel.fit(
        f"[bold]快照信息[/bold]\n"
        f"  ID: {snapshot.snapshot_id}\n"
        f"  名称: {snapshot.name}\n"
        f"  描述: {snapshot.description or '(无)'}\n"
        f"  配置版本: v{snapshot.config_version}\n"
        f"  创建人: {snapshot.created_by}\n"
        f"  创建时间: {snapshot.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{result.message}",
        title="导入规则快照",
    ))

    if result.conflicts:
        console.print("\n[yellow]检测到以下冲突：[/yellow]")
        conflict_table = Table(title="冲突列表")
        conflict_table.add_column("#", justify="right")
        conflict_table.add_column("类型")
        conflict_table.add_column("字段")
        conflict_table.add_column("当前值")
        conflict_table.add_column("快照值")
        conflict_table.add_column("说明")

        type_labels = {
            ConflictType.CONFIG_EXISTS: "配置存在",
            ConflictType.BATCH_EXISTS: "批次存在",
            ConflictType.VERSION_MISMATCH: "版本差异",
            ConflictType.EXTENSION_CONFLICT: "扩展名",
            ConflictType.TEMPLATE_CONFLICT: "命名模板",
            ConflictType.STRATEGY_CONFLICT: "策略差异",
        }

        for i, conflict in enumerate(result.conflicts, 1):
            conflict_table.add_row(
                str(i),
                type_labels.get(conflict.type, conflict.type.value),
                conflict.field,
                conflict.existing_value[:30] + "..." if len(conflict.existing_value) > 30 else conflict.existing_value,
                conflict.incoming_value[:30] + "..." if len(conflict.incoming_value) > 30 else conflict.incoming_value,
                conflict.message,
            )

        console.print(conflict_table)

        if not force:
            console.print("\n[yellow]请确认是否要继续导入？导入后将覆盖当前配置。[/yellow]")
            confirm = click.confirm("是否继续？", default=False)
            if not confirm:
                console.print("[yellow]已取消导入[/yellow]")
                sys.exit(0)

    config = config_mgr.load()
    version_before = config.version

    if author is None:
        author = config.default_author

    applied_config = config_mgr.apply_snapshot(
        snapshot=snapshot,
        source_path=snapshot_file.resolve(),
        author=author,
    )

    sync_batch_config_version(workspace, applied_config.version)

    import_log = SnapshotImportLog(
        id=f"log_{snapshot.snapshot_id}",
        operation="snapshot_import",
        author=author,
        status="success",
        message=f"成功导入快照 {snapshot.name}",
        snapshot_id=snapshot.snapshot_id,
        snapshot_name=snapshot.name,
        snapshot_version=snapshot.config_version,
        source_path=snapshot_file.resolve(),
        conflicts_resolved=[c.type.value for c in result.conflicts],
        config_version_before=version_before,
        config_version_after=applied_config.version,
    )
    store.add_snapshot_import_log(import_log)

    console.print(f"\n[green]✓ 快照导入成功[/green]")
    console.print(f"  新配置版本: v{applied_config.version}")
    console.print(f"  操作已记录到日志")


@snapshot.command("log")
@click.option("--limit", "-n", type=int, default=20, help="显示最近N条记录")
@click.pass_context
def snapshot_log(ctx, limit: int):
    """查看快照导入操作日志"""
    workspace = get_workspace(ctx)
    store = BatchStore(workspace)

    logs = store.list_snapshot_import_logs(limit=limit)

    if not logs:
        console.print("[yellow]暂无快照导入记录[/yellow]")
        return

    table = Table(title=f"快照导入日志（最近 {len(logs)} 条）")
    table.add_column("时间")
    table.add_column("操作人")
    table.add_column("快照名称")
    table.add_column("快照版本")
    table.add_column("配置版本(前→后)")
    table.add_column("状态")
    table.add_column("冲突数")

    for log in logs:
        status_style = "green" if log.status == "success" else "red"
        table.add_row(
            log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            log.author,
            log.snapshot_name,
            f"v{log.snapshot_version}",
            f"v{log.config_version_before} → v{log.config_version_after}",
            Text(log.status, style=status_style),
            str(len(log.conflicts_resolved)),
        )

    console.print(table)


@snapshot.command("show")
@click.option("--file", "-f", "snapshot_file", type=click.Path(path_type=Path, exists=True), help="查看指定快照文件内容")
@click.pass_context
def snapshot_show(ctx, snapshot_file: Optional[Path]):
    """查看当前快照信息或指定快照文件内容"""
    workspace = get_workspace(ctx)

    if snapshot_file:
        config_mgr = ConfigManager(workspace)
        try:
            snapshot = config_mgr.load_snapshot(snapshot_file)
        except Exception as e:
            console.print(f"[red]✗ 加载快照失败: {e}[/red]")
            sys.exit(1)

        console.print(Panel.fit(
            f"[bold]快照ID:[/bold] {snapshot.snapshot_id}\n"
            f"[bold]名称:[/bold] {snapshot.name}\n"
            f"[bold]描述:[/bold] {snapshot.description or '(无)'}\n"
            f"[bold]配置版本:[/bold] v{snapshot.config_version}\n"
            f"[bold]创建人:[/bold] {snapshot.created_by}\n"
            f"[bold]创建时间:[/bold] {snapshot.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"[bold]命名模板:[/bold]\n  {snapshot.naming_template}\n\n"
            f"[bold]允许的扩展名:[/bold] {', '.join(snapshot.allowed_extensions)}\n\n"
            f"[bold]重复策略:[/bold] {snapshot.duplicate_strategy.value}\n"
            f"[bold]归档方式:[/bold] {snapshot.archive_action.value}\n\n"
            f"[bold]归档目录:[/bold] {snapshot.archive_dir}\n"
            f"[bold]照片目录:[/bold] {snapshot.photo_dir}\n"
            f"[bold]点位 CSV:[/bold] {snapshot.points_csv}\n"
            f"[bold]备注 JSON:[/bold] {snapshot.notes_json}",
            title=f"快照内容: {snapshot.name}",
        ))
    else:
        config_mgr = ConfigManager(workspace)
        config = config_mgr.load()

        if config.last_snapshot and config.last_snapshot.imported_at:
            ls = config.last_snapshot
            console.print(Panel.fit(
                f"[bold]快照名称:[/bold] {ls.snapshot_name}\n"
                f"[bold]快照版本:[/bold] v{ls.snapshot_version}\n"
                f"[bold]来源文件:[/bold] {ls.source_path}\n"
                f"[bold]导入时间:[/bold] {ls.imported_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"[bold]导入人:[/bold] {ls.imported_by}",
                title="最近导入的快照",
            ))
        else:
            console.print("[yellow]当前工作区尚未导入过规则快照[/yellow]")
            console.print("使用 'snapshot import' 命令导入快照，或使用 'snapshot show -f <文件>' 查看快照内容")


@cli.command("scan")
@click.option("--dir", "photo_dir", type=click.Path(path_type=Path, exists=True, file_okay=False), help="照片目录路径")
@click.option("--recursive/--no-recursive", default=True, help="是否递归扫描子目录")
@click.pass_context
def scan_photos(ctx, photo_dir: Optional[Path], recursive: bool):
    """扫描照片目录"""
    batch = get_or_create_batch(ctx, require_existing=True)
    store = ctx.obj["store"]
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    if photo_dir is None:
        photo_dir = config.photo_dir

    photo_dir = Path(photo_dir).resolve()

    scanner = PhotoScanner(workspace, config.allowed_extensions)
    photos, skipped = scanner.scan_photos(photo_dir, recursive=recursive)

    if not photos and not skipped:
        console.print("[yellow]未找到任何照片[/yellow]")
        return

    store.add_photos(batch, photos)
    store.set_file_paths(batch, photo_dir=photo_dir)
    store.update_config_version(batch, config.version)

    console.print(f"[green]✓ 扫描完成[/green]")
    console.print(f"  找到照片: {len(photos)} 张")

    if skipped:
        console.print(f"[yellow]  跳过: {len(skipped)} 项[/yellow]")
        for msg in skipped[:5]:
            console.print(f"    - {msg}")
        if len(skipped) > 5:
            console.print(f"    ... 还有 {len(skipped) - 5} 条")

    duplicates = scanner.find_duplicates(photos)
    if duplicates:
        console.print(f"[yellow]  发现重复照片: {len(duplicates)} 组[/yellow]")

    table = Table(title="扫描的照片（前10张）")
    table.add_column("文件名")
    table.add_column("大小", justify="right")
    table.add_column("拍摄时间")
    table.add_column("点位ID")

    for photo in photos[:10]:
        size_mb = photo.file_size / 1024 / 1024
        table.add_row(
            photo.file_name,
            f"{size_mb:.1f} MB",
            photo.taken_at.strftime("%Y-%m-%d %H:%M:%S") if photo.taken_at else "-",
            photo.point_id or "-",
        )

    if len(photos) > 10:
        table.add_row(f"... 还有 {len(photos) - 10} 张", "", "", "")

    console.print(table)


@cli.command("preview")
@click.option("--check-existing/--no-check-existing", default=True, help="是否检查归档目录现有文件")
@click.pass_context
def generate_preview(ctx, check_existing: bool):
    """生成归档预览（确认前不移动或复制源照片）"""
    batch = get_or_create_batch(ctx, require_existing=True)
    store = ctx.obj["store"]
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    if not batch.photos:
        console.print("[red]✗ 没有照片数据，请先扫描照片[/red]")
        sys.exit(1)

    if not batch.points:
        console.print("[yellow]警告：没有点位数据，照片将按 UNKNOWN 分类[/yellow]")

    generator = PreviewGenerator(workspace, config)

    existing_targets = {}
    if check_existing:
        existing_targets = generator.scan_existing_archive()
        if existing_targets:
            console.print(f"[cyan]已扫描归档目录，发现 {len(existing_targets)} 个现有文件[/cyan]")

    photos_list = list(batch.photos.values())
    previews, conflicts = generator.generate_preview(
        photos_list,
        batch.points,
        existing_targets,
    )

    store.set_previews(batch, previews)
    store.clear_conflicts(batch)
    for conflict in conflicts:
        store.add_conflict(batch, conflict)

    store.update_config_version(batch, config.version)

    console.print(f"[green]✓ 预览生成完成[/green]")
    console.print(f"  预览项: {len(previews)}")
    console.print(f"  冲突: {len(conflicts)} ({sum(1 for c in conflicts if not c.resolved)} 未解决)")

    archiver = Archiver(workspace, config, store)
    stats = archiver.get_archive_stats(batch)

    stats_table = Table(title="归档统计")
    stats_table.add_column("项目")
    stats_table.add_column("数量", justify="right")
    for key, value in stats.items():
        stats_table.add_row(key, str(value))
    console.print(stats_table)

    preview_table = Table(title="归档预览（前15项）")
    preview_table.add_column("#", justify="right")
    preview_table.add_column("点位")
    preview_table.add_column("源文件")
    preview_table.add_column("目标路径")
    preview_table.add_column("状态")

    for i, preview in enumerate(previews[:15], 1):
        point_name = preview.point.name if preview.point else "未匹配"
        status_style = "red" if preview.will_conflict else "green"
        status = Text("冲突" if preview.will_conflict else "正常", style=status_style)
        if preview.duplicate_strategy == DuplicateStrategy.RENAME and preview.will_conflict:
            status = Text("自动重命名", style="yellow")
        elif preview.duplicate_strategy == DuplicateStrategy.SKIP and preview.will_conflict:
            status = Text("跳过", style="yellow")
        elif preview.duplicate_strategy == DuplicateStrategy.OVERWRITE and preview.will_conflict:
            status = Text("覆盖", style="magenta")

        try:
            target_rel = preview.target_path.relative_to(workspace / config.archive_dir)
        except ValueError:
            target_rel = preview.target_path.name

        preview_table.add_row(
            str(i),
            point_name,
            preview.photo.file_name,
            str(target_rel),
            status,
        )

    if len(previews) > 15:
        preview_table.add_row("...", f"还有 {len(previews) - 15} 项", "", "", "")

    console.print(preview_table)

    unresolved = [c for c in conflicts if not c.resolved]
    if unresolved:
        console.print("\n[red]未解决的冲突：[/red]")
        for conflict in unresolved[:5]:
            console.print(f"  • 目标: {conflict.target_path}")
            console.print(f"    新源: {conflict.new_source}")
            if conflict.existing_source:
                console.print(f"    已有: {conflict.existing_source}")
            console.print(f"    原因: {conflict.reason}")
            console.print()


@cli.group()
def annotate():
    """点位标注"""
    pass


@annotate.command("mark")
@click.argument("point_id")
@click.argument(
    "status",
    type=click.Choice(["pending", "to_rephoto", "confirmed", "ignored", "archived"], case_sensitive=False),
)
@click.option("--note", "-n", help="备注信息")
@click.option("--author", "-a", help="标注作者")
@click.pass_context
def annotate_mark(ctx, point_id: str, status: str, note: Optional[str], author: Optional[str]):
    """标注点位状态

    状态: pending(待处理), to_rephoto(待补拍), confirmed(已确认), ignored(忽略), archived(已归档)
    """
    batch = get_or_create_batch(ctx, require_existing=True)
    store = ctx.obj["store"]
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    status_enum = AnnotationStatus(status.lower())
    manager = AnnotationManager(workspace, store)

    if author is None:
        author = config.default_author

    annotation, error = manager.mark_point(batch, point_id, status_enum, note, author)

    if error:
        console.print(f"[red]✗ {error}[/red]")
        sys.exit(1)

    status_labels = {
        AnnotationStatus.PENDING: "待处理",
        AnnotationStatus.TO_REPHOTO: "待补拍",
        AnnotationStatus.CONFIRMED: "已确认",
        AnnotationStatus.IGNORED: "忽略",
        AnnotationStatus.ARCHIVED: "已归档",
    }

    console.print(f"[green]✓ 点位 {point_id} 已标注为: {status_labels[status_enum]}[/green]")
    if note:
        console.print(f"  备注: {note}")
    if annotation:
        console.print(manager.format_note_history(annotation))


@annotate.command("note")
@click.argument("point_id")
@click.argument("note_text")
@click.option("--author", "-a", help="备注作者")
@click.pass_context
def annotate_note(ctx, point_id: str, note_text: str, author: Optional[str]):
    """为点位添加备注"""
    batch = get_or_create_batch(ctx, require_existing=True)
    store = ctx.obj["store"]
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    if author is None:
        author = config.default_author

    manager = AnnotationManager(workspace, store)
    annotation, error = manager.add_note(batch, point_id, note_text, author)

    if error:
        console.print(f"[red]✗ {error}[/red]")
        sys.exit(1)

    console.print(f"[green]✓ 已为点位 {point_id} 添加备注[/green]")
    if annotation:
        console.print(manager.format_note_history(annotation))


@annotate.command("status")
@click.option(
    "--filter",
    "status_filter",
    type=click.Choice(["all", "pending", "to_rephoto", "confirmed", "ignored", "archived"], case_sensitive=False),
    default="all",
    help="按状态过滤",
)
@click.pass_context
def annotate_status(ctx, status_filter: str):
    """查看点位标注状态"""
    batch = get_or_create_batch(ctx, require_existing=True)
    workspace = get_workspace(ctx)
    store = ctx.obj["store"]
    manager = AnnotationManager(workspace, store)

    summary = manager.get_status_summary(batch)
    status_labels = {
        AnnotationStatus.PENDING: "待处理",
        AnnotationStatus.TO_REPHOTO: "待补拍",
        AnnotationStatus.CONFIRMED: "已确认",
        AnnotationStatus.IGNORED: "忽略",
        AnnotationStatus.ARCHIVED: "已归档",
    }

    summary_table = Table(title="标注统计")
    summary_table.add_column("状态")
    summary_table.add_column("数量", justify="right")
    for status, count in summary.items():
        summary_table.add_row(status_labels[status], str(count))
    console.print(summary_table)

    if status_filter == "all":
        points_to_show = [(batch.points[pid], ann) for pid, ann in batch.annotations.items() if pid in batch.points]
    else:
        status_enum = AnnotationStatus(status_filter.lower())
        points_to_show = manager.get_annotations_by_status(batch, status_enum)

    if not points_to_show:
        console.print("[yellow]没有符合条件的点位[/yellow]")
        return

    table = Table(title=f"点位列表 - {status_labels.get(AnnotationStatus(status_filter.lower()), '全部') if status_filter != 'all' else '全部'}")
    table.add_column("点位ID")
    table.add_column("名称")
    table.add_column("状态")
    table.add_column("备注数", justify="right")
    table.add_column("更新时间")

    for point, annotation in sorted(points_to_show, key=lambda x: x[0].id):
        table.add_row(
            point.id,
            point.name,
            status_labels.get(annotation.status, annotation.status.value),
            str(len(annotation.notes)),
            annotation.updated_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@annotate.command("history")
@click.argument("point_id")
@click.pass_context
def annotate_history(ctx, point_id: str):
    """查看点位备注历史"""
    batch = get_or_create_batch(ctx, require_existing=True)
    workspace = get_workspace(ctx)
    store = ctx.obj["store"]
    manager = AnnotationManager(workspace, store)

    annotation = manager.get_annotation(batch, point_id)
    if not annotation:
        console.print(f"[red]✗ 点位 {point_id} 不存在或暂无标注[/red]")
        sys.exit(1)

    point = batch.points.get(point_id)
    console.print(f"[bold]{point_id} - {point.name if point else '未知点位'}[/bold]")
    console.print(manager.format_note_history(annotation))


@cli.command("undo")
@click.pass_context
def undo_last(ctx):
    """撤销上一条标注操作"""
    batch = get_or_create_batch(ctx, require_existing=True)
    workspace = get_workspace(ctx)
    store = ctx.obj["store"]
    manager = AnnotationManager(workspace, store)

    success, description, error = manager.undo_last(batch)

    if error:
        console.print(f"[yellow]ℹ {error}[/yellow]")
        sys.exit(1)

    console.print(f"[green]✓ 已撤销: {description}[/green]")
    console.print(f"  剩余可撤销操作: {len(batch.undo_stack)}")


@cli.command("archive")
@click.option("--confirm", is_flag=True, help="确认执行归档（不加此选项仅显示预览）")
@click.option("--dry-run", is_flag=True, help="试运行，不实际移动文件")
@click.pass_context
def execute_archive(ctx, confirm: bool, dry_run: bool):
    """执行归档（确认前不移动或复制源照片）"""
    batch = get_or_create_batch(ctx, require_existing=True)
    store = ctx.obj["store"]
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    config = config_mgr.load()

    if not batch.previews:
        console.print("[red]✗ 没有归档预览，请先运行 preview 命令[/red]")
        sys.exit(1)

    archiver = Archiver(workspace, config, store)

    is_valid, errors = archiver.validate_previews(batch)
    if not is_valid:
        console.print("[red]✗ 归档验证失败：[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
        console.print("\n[yellow]提示：请先解决冲突，或使用 'rules set-duplicate' 修改重复策略[/yellow]")
        sys.exit(1)

    if dry_run:
        result = archiver.dry_run(batch)
        console.print("[cyan]=== 归档试运行 ===[/cyan]")
    elif confirm:
        result = archiver.execute_archive(batch, confirmed=True, author=config.default_author)
        console.print("[green]=== 归档执行完成 ===[/green]")
    else:
        result = archiver.dry_run(batch)
        console.print("[yellow]=== 归档预览 ===[/yellow]")
        console.print("[yellow]这只是预览，照片不会被移动或复制[/yellow]")
        console.print("[yellow]要实际执行，请添加 --confirm 选项[/yellow]")
        console.print()

    console.print(f"总计: {result.total} 项")
    console.print(f"  成功: [green]{result.success_count}[/green]")
    console.print(f"  失败: [red]{result.failed_count}[/red]")
    console.print(f"  跳过: [yellow]{result.skipped_count}[/yellow]")

    if result.successes and (confirm or dry_run):
        table = Table(title="操作详情（前10项）")
        table.add_column("源文件")
        table.add_column("目标路径")
        table.add_column("状态")

        for src, dst in result.successes[:10]:
            try:
                dst_rel = dst.relative_to(workspace)
            except ValueError:
                dst_rel = dst
            table.add_row(
                src.name,
                str(dst_rel),
                Text("成功" if confirm else "将成功", style="green"),
            )

        if len(result.successes) > 10:
            table.add_row(f"... 还有 {len(result.successes) - 10} 项", "", "")

        console.print(table)

    if result.failures:
        console.print("\n[red]失败项：[/red]")
        for path, msg in result.failures[:5]:
            console.print(f"  • {path}: {msg}")
        if len(result.failures) > 5:
            console.print(f"  ... 还有 {len(result.failures) - 5} 条")

    if confirm and result.success_count > 0:
        console.print(f"\n[green]✓ 已成功归档 {result.success_count} 个文件[/green]")


@cli.group()
def conflict():
    """冲突处理"""
    pass


@conflict.command("list")
@click.option("--all/--unresolved-only", default=False, help="显示所有冲突或仅未解决的")
@click.pass_context
def conflict_list(ctx, all: bool):
    """列出冲突"""
    batch = get_or_create_batch(ctx, require_existing=True)

    if all:
        conflicts = batch.conflicts
    else:
        conflicts = [c for c in batch.conflicts if not c.resolved]

    if not conflicts:
        console.print("[green]✓ 没有冲突[/green]")
        return

    table = Table(title=f"冲突列表 ({len(conflicts)} 项)")
    table.add_column("ID")
    table.add_column("状态")
    table.add_column("目标路径")
    table.add_column("原因")

    for c in conflicts:
        status = Text("已解决", style="green") if c.resolved else Text("未解决", style="red")
        table.add_row(
            c.id[:8],
            status,
            str(c.target_path.name),
            c.reason[:50],
        )

    console.print(table)


@conflict.command("resolve")
@click.argument("conflict_id")
@click.argument("resolution")
@click.pass_context
def conflict_resolve(ctx, conflict_id: str, resolution: str):
    """标记冲突为已解决"""
    batch = get_or_create_batch(ctx, require_existing=True)
    store = ctx.obj["store"]

    conflict = store.resolve_conflict(batch, conflict_id, resolution)
    if conflict:
        console.print(f"[green]✓ 冲突 {conflict_id[:8]} 已标记为已解决[/green]")
        console.print(f"  解决方案: {resolution}")
    else:
        console.print(f"[red]✗ 冲突 {conflict_id} 不存在[/red]")
        sys.exit(1)


@cli.group()
def export():
    """导出报告"""
    pass


@export.command("markdown")
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True, help="输出文件路径")
@click.option("--no-notes", is_flag=True, help="不包含备注历史")
@click.option("--no-conflicts", is_flag=True, help="不包含冲突列表")
@click.pass_context
def export_markdown(ctx, output: Path, no_notes: bool, no_conflicts: bool):
    """导出 Markdown 报告"""
    batch = get_or_create_batch(ctx, require_existing=True)
    workspace = get_workspace(ctx)

    exporter = ReportExporter(workspace)
    output_path = exporter.export_markdown(
        batch,
        output,
        include_notes=not no_notes,
        include_conflicts=not no_conflicts,
    )

    console.print(f"[green]✓ Markdown 报告已导出到: {output_path}[/green]")


@export.command("csv")
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True, help="输出文件路径")
@click.option("--no-notes", is_flag=True, help="不包含备注历史")
@click.pass_context
def export_csv(ctx, output: Path, no_notes: bool):
    """导出 CSV 报告"""
    batch = get_or_create_batch(ctx, require_existing=True)
    workspace = get_workspace(ctx)

    exporter = ReportExporter(workspace)
    output_path = exporter.export_csv(
        batch,
        output,
        include_notes=not no_notes,
    )

    console.print(f"[green]✓ CSV 报告已导出到: {output_path}[/green]")


@cli.command("info")
@click.pass_context
def show_info(ctx):
    """显示系统信息"""
    workspace = get_workspace(ctx)
    config_mgr = ConfigManager(workspace)
    store = BatchStore(workspace)
    config = config_mgr.load()

    batches = store.list_batches()
    current = store.get_current_batch()

    info_content = (
        f"[bold]工作目录:[/bold] {workspace}\n"
        f"[bold]配置版本:[/bold] v{config.version}\n"
        f"[bold]命名模板:[/bold] {config.naming_template}\n"
        f"[bold]允许扩展名:[/bold] {', '.join(config.allowed_extensions)}\n"
        f"[bold]重复策略:[/bold] {config.duplicate_strategy.value}\n"
        f"[bold]归档方式:[/bold] {config.archive_action.value}\n\n"
        f"[bold]批次总数:[/bold] {len(batches)}\n"
        f"[bold]当前批次:[/bold] {current.name if current else '(无)'}\n"
        f"[bold]数据目录:[/bold] {workspace / '.patrol-archiver'}"
    )

    if config.last_snapshot and config.last_snapshot.imported_at:
        ls = config.last_snapshot
        info_content += (
            f"\n\n[bold]最近快照导入:[/bold]\n"
            f"  快照名称: {ls.snapshot_name}\n"
            f"  快照版本: v{ls.snapshot_version}\n"
            f"  导入时间: {ls.imported_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"  导入人: {ls.imported_by}\n"
            f"  来源: {ls.source_path}"
        )

    console.print(Panel.fit(info_content, title="系统信息"))


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
