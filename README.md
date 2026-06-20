# night-audit

连锁门店夜间巡检材料自动归档、校验、生成交接包的 Python 命令行工具。

纯本地运行，无需联网，不依赖外部服务。

## 安装

```bash
# 进入项目目录
cd night-audit

# 安装（开发模式，可编辑）
pip install -e .

# 验证安装
night-audit --help
```

## 快速开始

```bash
# 1. 初始化样例数据
night-audit init -o sample_input

# 2. 扫描输入目录，查看文件解析结果
night-audit scan -i sample_input

# 3. 查看异常清单
night-audit exceptions -i sample_input

# 4. 整理材料到输出目录（有阻塞异常时需 --force 强制执行）
night-audit organize -i sample_input -o output --force

# 5. 生成交接包
night-audit package -i sample_input -o output

# 6. 查看交接报告
cat output/handover_package/report.md

# 7. 如果需要回滚
night-audit rollback -o output
```

## 命令详解

### `night-audit init`

生成一套包含正常和异常情况的样例数据。

```bash
night-audit init -o sample_input
```

样例数据包含：
- 3 家门店 (S001、S002、S003)、2 天 (2026-06-15、2026-06-16) 的巡检材料
- 照片 (jpg/png)、传感器 CSV、门禁日志 txt、值班备注 txt、异常登记表 json
- 故意制造的麻烦情况：重复上传、缺门店号、冰柜超温、门禁异常、电表倒退、设备编号冲突

### `night-audit scan`

扫描输入目录，解析文件名和内容，推断门店编号、日期、材料类型。

```bash
night-audit scan -i sample_input
night-audit scan -i sample_input --cutoff 5   # 夜班切分时间改为 05:00
```

输出包含：文件名、门店编号、日期、材料类型、内容哈希。

### `night-audit organize`

按 `门店/日期/材料类型` 层级整理文件到输出目录。

```bash
night-audit organize -i sample_input -o output
night-audit organize -i sample_input -o output --force  # 忽略阻塞异常强制执行
night-audit organize -i sample_input -o output --cutoff 5
```

整理流程：
1. 扫描输入目录
2. 运行所有规则，生成异常清单
3. 建立清单 (manifest.json)，包含源文件、目标路径、哈希值
4. 内容哈希去重，相同内容只保留一份
5. 文件名统一改为 `门店_日期_类型_序号.扩展名` 格式
6. 生成 `name_mapping.json` 保留原始文件名映射
7. 确认无阻塞冲突后复制文件（原文件不移动）

### `night-audit exceptions`

列出所有检测到的异常，按严重程度排序（阻塞 → 需复核 → 提醒）。

```bash
night-audit exceptions -i sample_input
```

### `night-audit explain`

解释某个文件或某条异常为什么被这样归类，列出命中的规则、相关文件和建议处理动作。

```bash
# 解释文件
night-audit explain -i sample_input -f "photo_night_2026-06-16_0100.jpg"

# 解释规则
night-audit explain -i sample_input -r R005
```

### `night-audit package`

生成交接包目录，包含：

| 文件 | 说明 |
|------|------|
| `summary.csv` | 每家店每天的完成度、异常数量、最高风险项、缺失材料 |
| `exceptions.json` | 完整异常清单 |
| `manifest.json` | 文件整理清单 |
| `report.md` | Markdown 版巡检报告，含完成度、异常详情、传感器超阈值片段 |

```bash
night-audit package -i sample_input -o output
```

### `night-audit rollback`

根据清单撤回上次整理结果，不会删除手动后来放入的文件。

```bash
night-audit rollback -o output
```

## 规则引擎

### 异常严重级别

| 级别 | 说明 | 示例 |
|------|------|------|
| 🔴 阻塞 | 必须处理才能完成交接 | 缺少必交材料、冰柜连续超温、设备编号冲突 |
| 🟡 需复核 | 需人工确认但不阻塞交接 | 文件名缺门店号但能从内容推断、传感器超阈值、电表异常 |
| 🟢 提醒 | 仅供参考，不影响交接 | 照片晚传但仍在容忍窗口内、内容重复文件 |

### 规则列表

| ID | 规则 | 级别 |
|----|------|------|
| R001 | 缺少门店编号 | 阻塞/复核 |
| R002 | 缺少日期 | 阻塞/复核 |
| R003 | 缺少必交材料 | 阻塞 |
| R004 | 传感器超阈值 | 复核 |
| R005 | 冰柜连续超温 (连续 ≥2 条) | 阻塞 |
| R006 | 闭店后门禁异常 | 阻塞/复核 |
| R007 | 电表读数异常 (倒退或突增) | 复核 |
| R008 | 照片与传感器时间不一致 | 提醒 |
| R009 | 异常登记表与传感器不一致 | 复核 |
| R010 | 设备编号冲突 (同一设备出现在多家店) | 阻塞 |
| R011 | 内容重复文件 | 提醒 |

### 阈值配置

| 设备类型 | 参数 | 默认值 |
|----------|------|--------|
| 默认 | 温度范围 | 16~30°C |
| 默认 | 湿度范围 | 30~80% |
| 冰柜 | 温度范围 | -25~-15°C |
| 冰柜 | 连续超温判定 | ≥2 条 |
| 电表 | 突增比率 | >3 倍 |

### 夜班日期归属

默认规则：凌晨 00:00~05:59 的材料归属前一天夜班。

通过 `--cutoff` 参数可修改切分时间：
- `--cutoff 6`：06:00 前算前一天（默认）
- `--cutoff 5`：05:00 前算前一天

### 必交材料

每家店每天应提交：

1. 门头照 (door_photo)
2. 收银台照 (counter_photo)
3. 仓库照 (warehouse_photo)
4. 温湿度 CSV (sensor_csv)
5. 门禁日志 (access_log)
6. 值班备注 (duty_note)

缺少任一项即触发 R003 阻塞级异常。

## 测试

```bash
pip install pytest
python -m pytest tests/test_core.py -v -p no:asyncio
```

测试覆盖：
- 跨天归属（含自定义切分时间）
- 哈希去重
- 必交材料缺失
- 冰柜连续超温
- 门禁异常关联（有无异常登记表）
- 设备编号冲突
- 清单回滚安全（不误删手工文件）
- 异常登记表与传感器不一致
- 电表读数异常（倒退和突增）
- 端到端完整流程

## 输出目录结构

```
output/
├── S001/
│   ├── 2026-06-15/
│   │   ├── door_photo/
│   │   │   └── S001_2026-06-15_door_photo_001.jpg
│   │   ├── counter_photo/
│   │   ├── warehouse_photo/
│   │   ├── sensor_csv/
│   │   ├── access_log/
│   │   └── duty_note/
│   └── 2026-06-16/
│       └── ...
├── S002/
│   └── ...
├── S003/
│   └── ...
├── handover_package/
│   ├── summary.csv
│   ├── exceptions.json
│   ├── manifest.json
│   └── report.md
├── manifest.json
├── name_mapping.json
└── exceptions.json
```

## 离线使用

本项目不依赖任何外部网络服务，所有操作均在本地完成。安装后可完全离线运行。
