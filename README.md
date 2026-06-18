# 本地巡检照片归档 CLI

一个多文件、多命令的专业巡检照片归档管理工具。读取点位 CSV、备注 JSON 和照片目录，按配置规则生成归档预览，支持复核标注和报告导出。

## 项目结构

```
zzz-00067/
├── patrol_archiver/          # 主包目录
│   ├── __init__.py           # 版本信息
│   ├── models.py             # 核心数据模型
│   ├── config.py             # 配置管理
│   ├── store.py              # 数据持久化
│   ├── csv_import.py         # CSV 导入
│   ├── scanner.py            # 照片扫描
│   ├── preview.py            # 归档预览
│   ├── annotation.py         # 标注管理
│   ├── archiver.py           # 归档执行
│   ├── exporter.py           # 报告导出
│   └── cli.py                # CLI 入口
├── sample/                   # 样例数据目录
│   ├── points.csv            # 正常点位清单
│   ├── points_bad.csv        # 坏清单（用于测试错误处理）
│   ├── notes.json            # 点位备注
│   └── photos/               # 样例照片（占位文件）
│       ├── P001_20260615_093000.jpg
│       ├── P001_20260615_093000_duplicate.jpg
│       ├── P001_20260615_093001_samecontent.jpg
│       ├── P002_20260615_101500.jpg
│       ├── P003_20260615_110000.jpg
│       ├── P004_20260615_140000.jpg
│       ├── P005_20260615_153000.jpg
│       ├── P006_20260615_160000.jpg
│       ├── IMG_20260615_163000_P007.jpg
│       └── DSC0008_P008.png
├── pyproject.toml            # 项目配置
├── .gitignore
└── README.md
```

## 核心特性

- **多命令架构**：import、rules、scan、preview、annotate、archive、export 等独立命令
- **点位管理**：CSV 导入，坏清单行号定位，不破坏旧批次
- **照片扫描**：支持 EXIF 读取、文件名时间解析、自动点位匹配
- **命名模板**：灵活的归档路径模板配置
- **重复策略**：BLOCK / SKIP / RENAME / OVERWRITE 四种策略
- **冲突阻止**：同一归档路径冲突时阻止执行
- **复核标注**：待补拍 / 已确认 / 忽略 / 已归档 四种状态
- **备注历史**：完整的备注历史记录，支持撤销
- **状态持久化**：批次、规则版本、冲突、标注全部持久化
- **报告导出**：Markdown 和 CSV 两种格式

## 安装

```bash
# 安装依赖
pip install -e .

# 或者使用 pip 安装
pip install click rich pydantic python-dateutil
```

## 快速开始

进入样例目录：
```bash
cd sample
```

### 1. 查看系统信息
```bash
patrol info
```

### 2. 导入点位清单
```bash
# 导入正常清单
patrol import --csv points.csv --notes notes.json --batch-name "2026年6月巡检"

# 测试坏清单（会显示行号错误）
patrol import --csv points_bad.csv --new-batch --batch-name "测试坏清单"
```

### 3. 查看/修改规则
```bash
# 查看当前规则
patrol rules show

# 设置命名模板
patrol rules set-template "{point.category}/{point.id}_{point.name}_{photo.taken_at:%Y%m%d_%H%M%S}{photo.source_path.suffix}"

# 设置重复策略（默认 block，可选 skip/rename/overwrite）
patrol rules set-duplicate block

# 设置归档方式（copy 或 move）
patrol rules set-action copy
```

### 4. 扫描照片
```bash
patrol scan --dir photos
```

### 5. 生成归档预览
```bash
patrol preview
```

### 6. 处理冲突
```bash
# 列出未解决的冲突
patrol conflict list

# 列出所有冲突（包括已解决）
patrol conflict list --all

# 标记冲突为已解决
patrol conflict resolve <conflict_id> "手动确认跳过"
```

### 7. 复核标注
```bash
# 标注点位状态
patrol annotate mark P001 confirmed --note "设备运行正常"
patrol annotate mark P002 to_rephoto --note "照片模糊需要重拍"
patrol annotate mark P003 ignored --note "该点位已拆除"

# 添加备注
patrol annotate note P001 "第二次巡检确认"

# 查看所有点位状态
patrol annotate status

# 按状态过滤
patrol annotate status --filter to_rephoto

# 查看点位备注历史
patrol annotate history P001

# 撤销上一条标注
patrol undo

# 再次撤销（空撤销会有明确提示）
patrol undo
```

### 8. 执行归档
```bash
# 预览模式（不移动文件）
patrol archive

# 试运行
patrol archive --dry-run

# 确认执行（会移动/复制文件）
patrol archive --confirm
```

### 9. 导出报告
```bash
# 导出 Markdown 报告
patrol export markdown -o report.md

# 导出 CSV 报告
patrol export csv -o report.csv

# 导出不含备注的报告
patrol export markdown -o report_simple.md --no-notes
```

### 10. 批次管理
```bash
# 创建新批次
patrol batch new --name "另一个批次"

# 列出所有批次
patrol batch list

# 切换批次
patrol batch switch <batch_id>

# 查看当前批次
patrol batch show
```

## 完整可复现流程

```bash
# 0. 进入样例目录
cd sample

# 1. 导入清单
patrol import --csv points.csv --notes notes.json --batch-name "2026年6月巡检"

# 2. 加载规则
patrol rules show
patrol rules set-duplicate block

# 3. 扫描照片
patrol scan --dir photos

# 4. 预览归档
patrol preview

# 5. 处理冲突（如果有）
patrol conflict list
# 如果有冲突需要解决，使用：
# patrol conflict resolve <conflict_id> "解决方案"

# 6. 标注点位
patrol annotate mark P001 confirmed --note "设备正常"
patrol annotate mark P002 confirmed --note "压力正常"
patrol annotate mark P003 to_rephoto --note "需要重拍"

# 7. 撤销标注
patrol undo

# 8. 再次标注
patrol annotate mark P003 confirmed --note "重新确认，照片可用"
patrol annotate mark P004 ignored --note "点位取消"

# 9. 导出报告
patrol export markdown -o report.md
patrol export csv -o report.csv

# 10. 执行归档
patrol archive --dry-run
# patrol archive --confirm  # 确认后才会移动文件
```

## 坏清单测试

```bash
cd sample

# 导入坏清单，会显示具体行号的错误
patrol import --csv points_bad.csv --batch-name "坏清单测试"

# 输出示例：
# ✗ 导入存在错误：
#   • 行 3, 列 'id': 点位 ID 不能为空
#   • 行 4, 列 'name': 点位名称不能为空
#   • 行 7, 列 'id': 点位 ID 不能为空
#   • 行 7, 列 'name': 点位名称不能为空
# 警告：部分数据导入失败，成功 3 条
```

## 空撤销测试

```bash
# 在新批次中尝试撤销
patrol batch new --name "空撤销测试"
patrol undo

# 输出：
# ℹ 撤销栈为空，没有可撤销的操作
```

## 冲突阻止测试

```bash
# 1. 先归档一次
patrol batch new --name "冲突测试批次1"
patrol import --csv points.csv --batch-name "冲突测试"
patrol scan --dir photos
patrol rules set-duplicate block
patrol preview
patrol archive --confirm

# 2. 再次尝试归档相同照片（会被阻止）
patrol batch new --name "冲突测试批次2"
patrol import --csv points.csv --batch-name "冲突测试2"
patrol scan --dir photos
patrol rules set-duplicate block
patrol preview  # 会显示冲突

# 3. 尝试执行归档（会被阻止）
patrol archive --confirm
# 输出：
# ✗ 归档验证失败：
#   • 存在 X 个未解决的冲突，归档已被阻止
```

## 重新打开 CLI 后的状态一致性

所有数据都存储在工作目录下的 `.patrol-archiver/` 目录中：

```
.patrol-archiver/
├── config.json          # 配置（含版本号）
├── current_batch.json   # 当前批次 ID
└── batches/             # 所有批次数据
    ├── batch_<id1>.json
    ├── batch_<id2>.json
    └── ...
```

重新打开 CLI 后，运行 `patrol info` 或 `patrol batch list` 可以看到所有批次、规则版本、冲突列表、标注和导出结果都保持一致。

## 命名模板变量

在命名模板中可以使用以下变量：

| 变量 | 说明 |
|------|------|
| `{point.id}` | 点位 ID |
| `{point.name}` | 点位名称 |
| `{point.category}` | 点位分类 |
| `{point.location}` | 点位位置 |
| `{photo.taken_at:%Y%m%d_%H%M%S}` | 拍摄时间（可自定义格式） |
| `{photo.source_path.suffix}` | 源文件扩展名 |
| `{photo.file_name}` | 源文件名 |

示例：
- `"{point.category}/{point.id}_{point.name}_{photo.taken_at:%Y%m%d_%H%M%S}{photo.source_path.suffix}"`
- `"巡检照片/{point.id}/{photo.taken_at:%Y-%m-%d}/{photo.file_name}"`

## 重复策略说明

| 策略 | 说明 |
|------|------|
| `block` | **默认**。遇到冲突时阻止执行，必须手动解决 |
| `skip` | 跳过冲突的文件，不进行归档 |
| `rename` | 自动重命名冲突文件（添加 _1, _2 后缀） |
| `overwrite` | 覆盖已存在的文件（谨慎使用） |

## 标注状态说明

| 状态 | 说明 |
|------|------|
| `pending` | 待处理（默认） |
| `to_rephoto` | 待补拍，照片需要重新拍摄 |
| `confirmed` | 已确认，照片合格 |
| `ignored` | 忽略，该点位不需要归档 |
| `archived` | 已归档，照片已完成归档 |

## 命令参考

```
patrol [OPTIONS] COMMAND [ARGS]...

Commands:
  batch      批次管理
    new      创建新批次
    list     列出所有批次
    switch   切换到指定批次
    show     显示当前批次信息

  import     导入点位清单
  rules      规则管理
    show     显示当前规则配置
    set-template  设置命名模板
    add-ext  添加允许的扩展名
    remove-ext  移除允许的扩展名
    set-duplicate  设置重复策略
    set-action  设置归档方式

  scan       扫描照片目录
  preview    生成归档预览

  annotate   点位标注
    mark     标注点位状态
    note     为点位添加备注
    status   查看点位标注状态
    history  查看点位备注历史

  undo       撤销上一条标注操作

  archive    执行归档
  conflict   冲突处理
    list     列出冲突
    resolve  标记冲突为已解决

  export     导出报告
    markdown 导出 Markdown 报告
    csv      导出 CSV 报告

  info       显示系统信息
```

## 开发说明

### 模块职责

- **models.py**: 纯数据模型，无业务逻辑
- **config.py**: 配置加载/保存，版本管理
- **store.py**: JSON 持久化，批次 CRUD
- **csv_import.py**: CSV 解析，错误定位
- **scanner.py**: 文件系统扫描，EXIF 解析
- **preview.py**: 模板渲染，冲突检测
- **annotation.py**: 状态流转，撤销栈
- **archiver.py**: 文件操作，冲突阻止
- **exporter.py**: 报告生成
- **cli.py**: 命令组织，用户交互

### 数据流向

```
CSV/JSON → csv_import → store → batch
照片目录 → scanner → store → batch
batch → preview → conflicts → annotate → store
batch → archiver → archive_dir
batch → exporter → report.md / report.csv
```
