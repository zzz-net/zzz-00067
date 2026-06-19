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
- **草稿管理**：归档方案可保存为草稿，跨重启恢复并沿用原归档动作与冲突策略
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

### 8. 草稿管理（可选，保存/恢复归档方案）
```bash
# 将当前预览方案保存为草稿
patrol draft save "2026年6月巡检方案" -d "初次预览，待领导审批"

# 列出所有草稿
patrol draft list

# 查看草稿详情（含归档动作、重复策略、预览项）
patrol draft show "2026年6月巡检方案"

# 恢复草稿到当前批次（沿用草稿中的归档动作和策略）
patrol draft restore "2026年6月巡检方案"

# 删除草稿
patrol draft delete "2026年6月巡检方案"
```

### 9. 执行归档
```bash
# 预览模式（不移动文件）
patrol archive

# 试运行（不移动文件，显示执行结果）
patrol archive --dry-run

# 确认执行（会移动/复制文件）
patrol archive --confirm
```

> **提示**：恢复草稿后执行归档时，将沿用草稿保存时的归档动作（copy/move）和重复策略，即使当前配置已修改也不会受影响。

### 10. 导出报告
```bash
# 导出 Markdown 报告
patrol export markdown -o report.md

# 导出 CSV 报告
patrol export csv -o report.csv

# 导出不含备注的报告
patrol export markdown -o report_simple.md --no-notes
```

### 11. 批次管理
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

### 12. 规则快照管理
```bash
# 导出当前规则为快照文件
patrol snapshot export -o rules_snapshot.json

# 查看快照文件内容
patrol snapshot show -f rules_snapshot.json

# 导入规则快照（有冲突时会提示确认）
patrol snapshot import -f rules_snapshot.json

# 强制导入（跳过确认，适合自动化脚本）
patrol snapshot import -f rules_snapshot.json --force

# 查看导入历史日志
patrol snapshot log

# 查看最近一次导入的快照信息
patrol snapshot show
```

## 规则快照完整流程

### 场景说明
规则快照用于跨工作区迁移完整规则集，包括：命名模板、允许扩展名、重复策略、归档方式、目录配置等。导入前会检测冲突，要求显式确认后才会应用。

### 1. 导出规则快照
```bash
# 先按需要调整规则
patrol rules set-duplicate rename
patrol rules add-ext .heic
patrol rules set-template "{point.category}/{point.id}_{point.name}_{photo.taken_at:%Y%m%d_%H%M%S}{photo.source_path.suffix}"

# 导出快照
patrol snapshot export -o ./snapshots/prod_rules.json \
    --name "生产环境规则" \
    --description "2026年6月生产环境归档规则" \
    --author "张三"
```

**预期输出：**
```
✓ 规则快照已导出
  快照ID: snapshot_20260619_xxxxxx_xxxxxxxx
  快照名称: 生产环境规则
  配置版本: v4
  导出人: 张三
  导出时间: 2026-06-19 xx:xx:xx
  文件路径: ./snapshots/prod_rules.json
```

> 注：初始配置版本为 v1，每次修改规则递增 1，三次修改后为 v4（v1→v2→v3→v4）。

### 2. 查看快照文件内容
```bash
patrol snapshot show -f ./snapshots/prod_rules.json
```

**预期输出：** 显示快照ID、名称、描述、配置版本、创建人、创建时间、命名模板、扩展名列表、重复策略、归档方式、各目录路径。

### 3. 导入规则快照
在另一个工作区或新环境中导入：

```bash
cd /path/to/new/workspace
patrol snapshot import -f ./snapshots/prod_rules.json
```

**导入时会自动检测以下冲突：**

| 冲突类型 | 说明 |
|---------|------|
| 配置存在 | 当前工作区已有配置（v1以上版本） |
| 版本差异 | 当前配置版本高于快照版本 |
| 命名模板 | 模板内容不同 |
| 策略差异 | 重复处理策略/归档方式不同 |
| 扩展名 | 允许的扩展名集合不同 |
| 批次存在 | 当前工作区已有批次（导入后这些批次仍保留，规则更新） |

**冲突确认界面会显示对比表格，并询问"是否继续？"

### 4. 确认导入
输入 `y` 或 `yes` 继续，导入成功：

**成功输出：**
```
✓ 快照导入成功
  新配置版本: v2
  已同步批次: 2 个
  操作已记录到日志
```

> 注：新配置版本 = 原版本 + 1（导入算一次变更），已同步批次数等于当前工作区中已有的批次数量。

### 5. 取消导入
输入 `n` 或 `no` 取消：

**取消输出：**
```
已取消导入
```
取消后：
- 配置不会修改
- 所有批次保持原有版本
- 操作日志**不会**记录

### 6. 验证导入结果
```bash
# 方式1：查看规则及导入来源
patrol rules show
# 面板底部会显示"最近快照导入"区块，包含快照名称、版本、导入时间、导入人、来源文件

# 方式2：查看系统信息
patrol info
# 同样会显示最近快照导入信息

# 方式3：查看当前快照信息
patrol snapshot show
# 显示最近一次导入的快照详情

# 方式4：查看导入历史
patrol snapshot log -n 10
# 表格形式展示最近N次导入，包含时间、操作人、快照名、版本变化、状态、冲突数

# 方式5：直接导出报告验证版本
patrol export markdown -o verify_report.md
# 报告中的配置版本应为导入后的新版本

# 方式6：切换旧批次后导出
patrol batch switch <旧批次ID>
patrol export csv -o old_batch.csv
# 旧批次导出的报告也使用新配置版本
```

### 7. 跨工作区迁移完整示例
```bash
# === 工作区A：导出
cd workspace_a
patrol rules set-duplicate rename
patrol rules add-ext .heic
patrol rules set-template "custom/{point.id}_{photo.taken_at:%Y%m%d}{photo.source_path.suffix}"
patrol snapshot export -o rules.json --name "标准规则集" --author "admin"

# === 工作区B：导入
cd ../workspace_b
patrol batch new --name "旧批次1"
patrol batch new --name "旧批次2"
patrol snapshot import -f ../workspace_a/rules.json
# 看到冲突列表后输入 y 确认

# === 验证
patrol rules show          # 显示新规则和导入来源
patrol snapshot log        # 看到导入记录
patrol batch switch 旧批次1
patrol export markdown -o test.md  # 报告显示新版本
```

## 草稿管理完整流程

### 场景说明
草稿用于将当前批次的预览方案（含归档动作、重复策略、冲突状态、预览项）持久化保存，便于跨重启恢复、或在调整配置后切换回已审批的方案。恢复后归档将**沿用草稿保存时的动作与策略**，不受当前配置变化影响。

### 主流程：save → list/show → restore → archive --dry-run/--confirm

#### 1. 准备数据并生成预览
```bash
cd sample
patrol import --csv points.csv --notes notes.json --batch-name "2026年6月巡检"
patrol rules set-action copy
patrol rules set-duplicate rename
patrol scan --dir photos
patrol preview
```

#### 2. 保存草稿（draft save）
```bash
patrol draft save "6月巡检-审批版" -d "rename策略+copy动作，已通过组长复核"
```

**预期输出：**
```
✓ 草稿已保存
  草稿 ID: draft_20260619_xxxxxx_xxxxxxxx
  草稿名称: 6月巡检-审批版
  创建时间: 2026-06-19 xx:xx:xx
  来源批次: 2026年6月巡检
  规则版本: v2
  预览项数: 9
  冲突: 0 (未解决 0)
```

#### 3. 列出草稿（draft list）
```bash
patrol draft list
```

**提示**：当没有草稿时，`draft list` 会输出「暂无草稿」而不是空表格，便于脚本和 CI 判断。

**预期输出有草稿时**：显示草稿列表表格，包含 ID、名称、创建时间、来源批次、规则版本、预览项、冲突(未解决)。

**预期输出无草稿时**：
```
暂无草稿
```

#### 4. 查看草稿详情（draft show）
```bash
patrol draft show "6月巡检-审批版"
```

**预期输出包含：**
- 草稿基本信息（ID、名称、描述、创建时间）
- 来源批次信息（批次ID、名称、创建时间、点位数、照片数）
- **规则信息（关键）**：规则版本、重复策略、归档动作、命名模板
- 内容摘要：预览项数、冲突总数、未解决冲突数、冲突分布
- 预览项表格（前10项）

> **验证要点**：确认"重复策略"为 `rename`、"归档动作"为 `copy`，与保存时一致。

---

### 场景一：恢复后沿用草稿动作（当前配置已变更）

#### 5. 修改当前配置（模拟配置被改动）
```bash
patrol rules set-action move
patrol rules set-duplicate block
patrol rules show
```

此时当前配置：归档动作为 `move`，重复策略为 `block`，与草稿中保存的 `copy` + `rename` 不一致。

#### 6. 恢复草稿（draft restore）
```bash
patrol draft restore "6月巡检-审批版"
```

**存在差异时会先显示警告并要求确认：**
```
⚠ 检测到以下差异：
  • 规则版本不匹配：草稿基于 v2，当前为 v4
  • 重复策略不匹配：草稿为 rename，当前为 block
  • 归档动作不匹配：草稿为 copy，当前为 move

检测到草稿与当前状态存在差异，恢复后将覆盖当前批次的预览和冲突数据。是否继续？ [y/N]:
```

输入 `n` 取消恢复：
```
已取消恢复
```

输入 `y` 确认，或使用 `--force` 跳过确认：
```bash
patrol draft restore "6月巡检-审批版" --force
```

**恢复成功输出：**
```
✓ 草稿已恢复到当前批次
  草稿: 6月巡检-审批版
  恢复预览项: 9
  恢复冲突: 0 (未解决 0)
  归档动作: copy
  重复策略: rename

⚠ 注意：草稿中保存的规则与当前配置不一致
  • 归档动作: 草稿为 copy，当前配置为 move
    → 将沿用草稿中的 copy 动作执行归档
  • 重复策略: 草稿为 rename，当前配置为 block
    → 将沿用草稿中的 rename 策略处理冲突

提示：可以运行 'archive --dry-run' 验证恢复后的归档方案
```

#### 7. 验证归档动作——dry-run
```bash
patrol archive --dry-run
```

**关键输出验证：**
```
当前归档动作: copy
⚠ 预览中保存的动作与当前配置不一致，将沿用预览中的动作
=== 归档试运行 ===
总计: 9 项
  成功: 9
  失败: 0
  跳过: 0
```

> **验证要点**：输出必须显示 `当前归档动作: copy`（草稿值）而非当前配置的 `move`，并出现"预览中保存的动作与当前配置不一致"的提示。

#### 8. 确认执行归档（沿用草稿动作）
```bash
patrol archive --confirm
```

执行后源照片文件**仍存在**（因为草稿保存的是 `copy` 动作）。

---

### 场景二：跨重启后再次恢复仍可验证

#### 9. 保存草稿后模拟 CLI 重启
草稿数据持久化在 `.patrol-archiver/drafts/` 目录下，重启后依然存在：

```bash
# 修改当前配置为 move + skip（模拟另一个人改了配置）
patrol rules set-action move
patrol rules set-duplicate skip

# 清除当前批次的预览（模拟新批次状态）
patrol batch new --name "新批次-重启后"
patrol import --csv points.csv --batch-name "新批次-重启后"
patrol scan --dir photos
```

此时当前批次没有预览数据，且配置为 `move` + `skip`。

#### 10. 跨重启后列出草稿
```bash
patrol draft list
```

草稿依然存在，说明持久化正常。

#### 11. 跨重启后查看草稿详情
```bash
patrol draft show "6月巡检-审批版"
```

确认规则信息仍然是：归档动作 `copy`、重复策略 `rename`。

#### 12. 跨重启后恢复草稿
```bash
patrol draft restore "6月巡检-审批版" --force
```

**恢复输出中应包含：**
```
归档动作: copy
重复策略: rename

⚠ 注意：草稿中保存的规则与当前配置不一致
  • 归档动作: 草稿为 copy，当前配置为 move
    → 将沿用草稿中的 copy 动作执行归档
```

#### 13. 跨重启后 dry-run 验证
```bash
patrol archive --dry-run
```

**关键验证：**
```
当前归档动作: copy
⚠ 预览中保存的动作与当前配置不一致，将沿用预览中的动作
```

> **跨重启验证通过**：即使 CLI 重启、当前批次被切换、配置被多次修改，草稿仍可被完整恢复，归档动作和重复策略始终沿用草稿保存时的值。

---

### 冲突策略优先级说明

恢复草稿后，归档时的规则优先级如下（从高到低）：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1（最高） | 草稿中每个 PreviewItem 保存的 `archive_action` / `duplicate_strategy` | 实际执行归档时读取此字段 |
| 2 | 草稿级别的 `archive_action` / `duplicate_strategy`（`draft show` 可见） | 保存草稿时的配置快照，用于恢复时提示 |
| 3（最低） | 当前工作区配置（`rules show` 可见） | 草稿恢复后不会影响此字段 |

**验证方法**：
- `patrol draft show <草稿名>` → 查看草稿级别的动作和策略
- `patrol archive --dry-run` → 输出顶部会显示"当前归档动作"以及是否与配置不一致
- 执行 `--confirm` 后检查源文件是否存在（`copy` 应存在，`move` 应不存在）

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
| `{photo.name}` | 源文件名（别名，等价于 `{photo.file_name}`，仅用于兼容误用，推荐使用 `{photo.file_name}`） |

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
  annotate  点位标注
    history  查看点位备注历史
    mark     标注点位状态
    note     为点位添加备注
    status   查看点位标注状态

  archive  执行归档（确认前不移动或复制源照片）

  batch  批次管理
    list    列出所有批次
    new     创建新批次
    show    显示当前批次信息
    switch  切换到指定批次

  conflict  冲突处理
    list     列出冲突
    resolve  标记冲突为已解决

  draft  归档方案草稿管理（保存、查看、恢复预览方案）
    delete   删除指定草稿
    list     列出所有草稿
    restore  恢复草稿到当前批次
    save     将当前批次的预览结果保存为草稿
    show     查看草稿详情

  export  导出报告
    csv       导出 CSV 报告
    markdown  导出 Markdown 报告

  import  导入点位清单

  info  显示系统信息

  preview  生成归档预览（确认前不移动或复制源照片）

  rules  规则管理（命名模板、扩展名、重复策略等）
    add-ext          添加允许的文件扩展名
    remove-ext       移除允许的文件扩展名
    set-action       设置归档操作方式
    set-archive-dir  设置归档输出目录
    set-duplicate    设置重复照片策略
    set-template     设置命名模板
    show             显示当前规则配置

  scan  扫描照片目录

  snapshot  规则快照管理（导出、导入、查看日志）
    export  导出当前规则为快照文件
    import  导入规则快照（存在冲突时需确认）
    log     查看快照导入操作日志
    show    查看当前快照信息或指定快照文件内容

  undo  撤销上一条标注操作
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