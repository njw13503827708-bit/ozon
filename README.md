# JLC Cross-border AI ERP

当前唯一开发阶段是 `V1.1 Stabilization`，总控规则见 [`docs/MASTER_DEVELOPMENT_DIRECTIVE.md`](docs/MASTER_DEVELOPMENT_DIRECTIVE.md)。本地 AI Factory 是 ERP 的 V1 1688 → Ozon 商品生产模块；V2+ 能力均为 `NOT IMPLEMENTED`。

`auto_upload` 只作为旧数据兼容字段保留，不能绕过整批 `PREVIEW -> CONFIRM -> EXECUTE` 写入门禁。任何失败、未完成、过期或未审核商品都会阻断整批 Ozon CREATE/UPDATE。

## Windows 10/11：直接通过 Codex 安装

Windows 用户可以把仓库地址交给 Codex，让 Codex 克隆仓库后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

脚本会使用 `winget` 自动检查 Git、Python、Node.js 和 Codex CLI，安装项目依赖，构建当前工作台界面与 Edge 采集插件，然后启动工作台。完整说明见 [WINDOWS.md](WINDOWS.md)。不要直接跳过构建运行 GitHub 源码，否则被 `.gitignore` 排除的前端 `dist` 不存在，服务会显示旧版备用采集界面。

## 当前唯一主流程

真实执行顺序来自 `scripts/pipeline_runtime.py` 的 `PIPELINE_STEPS`（阶段 A 7 步 + 阶段 B 8 步，共 15 步）。除标注外，每步都是本地子进程或进程内函数，由 `scripts/run_batch.py` 编排；只有 `ecommerce_design` 和 `image_generation` 两步会把子任务委托给 Codex（对应 `.agents/skills/` 下的 skill）。

阶段 A（采集与可行性）：

1. `validate_source` — 校验 input/source.json 等采集输入。
2. `product_analysis` — `scripts/product_analysis_fast.py` 写 `output/product-analysis.json`。
3. `category_match` — `scripts/ozon_metadata_matcher.py --write` 写 `ozon-category.json` / `ozon-attributes.json`（阶段A预览）；`ozon-adapter/cli.py --fetch` 拉取实时类目元数据写 `ozon-category-attributes.json` / `ozon-category-tree.json`。
4. `variant_rules` — `variant-compatibility-checker/cli.py` 写 `output/platform-grouping-result.json`。
5. `measurements` — `pricing-engine/cli.py --write` 写 `cost-analysis.json` / `pricing-result.json`。
6. `offer_exists_check` — 进程内，写 `output/offer-id-precheck.json`。
7. `upload_feasibility` — 进程内，写 `output/upload-feasibility.json`（读 `ozon-attributes-final.json`，早期预检回退读 `ozon-attributes.json`）。

阶段 B（内容与提交）：

8. `product_positioning` — `scripts/product_positioning_agent.py --write` 写 `output/product-positioning.json`。
9. `ecommerce_design` — 先跑 `product_fact_merger` + `attribute_fill_input` + `image_source_preflight`（本地），再委托 Codex `$ozon-ecommerce-designer` 写 `ozon-ecommerce-design.json`。
10. `russian_copy` — `ozon_ecommerce_designer_contract.py --materialize` 投影出 `copy-ru.json` / `title-ru.json` / `ozon-tags.json` 等。
11. `field_completion` — `ozon-field-completion/cli.py` 生成最终 `ozon-tags.json` / `ozon-attributes-final.json` / `attribute-coverage-report.json`。
12. `image_plan` — materialize + `scripts/image_planner.py --write` 写 `image-plan.json`。
13. `image_generation` — 逐图位委托 Codex `$image-generator`，产出 `generated-images/` 与 `image-slot-results/`。
14. `image_qc` — `scripts/image_qc.py --hard-gate` 写 `image-qc-report.json`；通过后 field-completion 收尾 `rich-content.json` / `ozon-upload-config.json`。
15. `ozon_upload` — `scripts/multi_store_upload.py --execute` 按店铺隔离构建并提交，写 `store-publications.json`（SQLite 权威层）与 `output/store-runs/<店铺>/`。

用户点击“运行任务”后按上述步骤生成资料，但真实 Ozon 提交前必须经过一次整批人工确认：

```text
运行批次 → 等待全部商品生成与质检 → 审阅整批上传 →
若阻断则返回工作台修改 → 重新生成 → 确认整批上传 → 查看多店提交结果
```

确认页只读，不允许在那里临时修改商品。任何一个商品仍有测试/占位内容、俄文文案或富内容缺失、类目未确认、必填属性/尺寸/重量/价格/SKU 不完整、图片不足或质检未通过时，整批都不能确认。修改必须返回工作台完成，重新生成后再审阅；批准一次只绑定当时整批内容的哈希，之后任一受保护内容变化都会使批准失效。完整操作与故障处理见 [docs/BATCH_UPLOAD_APPROVAL.md](docs/BATCH_UPLOAD_APPROVAL.md)。

## 当前正式产物

每个正式商品只读取自己的 `products/<product_id>/` 目录：

```text
products/<product_id>/
├── input/
│   ├── source.json
│   ├── sku-images/
│   ├── main-images/
│   └── detail-images/
├── output/
│   ├── product-analysis.json
│   ├── attribute-fill-input.json / attribute-fill-input.compact.json
│   ├── ozon-ecommerce-design.json
│   ├── copy-ru.json
│   ├── image-plan.json
│   ├── generated-images/
│   │   ├── variant-main/
│   │   └── detail/
│   ├── rejected-generation/
│   ├── image-qc-report.json
│   ├── ozon-attributes-final.json
│   ├── ozon-upload-config.json
│   └── ozon-draft.json
└── status.json
```

（实际产物不止这些；完整清单见 `scripts/pipeline_observability.py` 的 STEP_INPUTS / STEP_OUTPUTS。）

三个属性文件的职责，不要混淆：

- `output/ozon-attributes.json` — 阶段 A matcher 的匹配预览（旧格式）。
- `output/ozon-category-attributes.json` — `ozon-adapter` 拉取的实时类目元数据（raw）。
- `output/ozon-attributes-final.json` — 字段编译器输出的最终属性，**上传与校验一律读它**。

正式商品输入只允许来自工作台本次采集。对话附件、测试图片、旧审计目录、其他商品目录和生成输出都不能自动补入当前商品。

## 关键业务边界

- 不提交库存字段。
- 不调用库存、仓库、激活接口。
- 已收到 Ozon `task_id` 的店铺不重复创建。
- 每家店铺独立保存 offer、任务号、请求哈希、状态和错误。
- 单店失败不阻塞其他店。
- 单商品最多 10 个已选 SKU。
- 标题、描述、标签、颜色和属性远端字段必须使用自然俄文，不混中文取证说明。
- 商品尺寸和重量优先使用本次采集结构化数据；采集不到才估算。
- 重量和包装尺寸进入 Ozon payload 前必须编译成正确数字类型，整数要求向上取整。
- 整批确认前写请求计数必须为 0；任何商品不通过都会阻止整批上传。
- 确认页只展示汇总与风险；编辑入口返回工作台对应商品和问题区域。
- 店铺凭证只在本机配置，禁止写入导出包、自检报告或日志。

## 当前代码职责

- `scripts/product_fact_merger.py`：商品事实合并。
- `scripts/attribute_fill_input.py`：生成属性填值输入。
- `scripts/ozon_ecommerce_designer_contract.py`：电商设计方案合同与校验。
- `scripts/ozon_attribute_compiler.py`：Ozon 属性确定性编译。
- `scripts/image_planner.py`：把电商设计方案编译成图片执行计划。
- `scripts/image_slot_scheduler.py` / `scripts/image_wave_executor.py`：图片槽位调度与并发执行。
- `scripts/run_batch.py`：批次流水线。
- `ozon-field-completion/`：资料包投影和字段补全。
- `ozon-uploader/`：上传前安全构建和多店提交。
- `collector/local-ingest/`：本地工作台和采集入口。
- `collector/edge-extension/`：1688 采集插件。

## 测试原则

开发和测试不得调用 Ozon 创建、更新、只读回查或库存接口。真实提交只能由用户点击上传或明确开启自动上传触发。

推荐验证：

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe scripts\project_self_check.py
```

默认离线自检必须为 `PASS`，且 Ozon 与库存写入计数必须均为 0。`--full-tests` 仅用于诊断导入项目中尚未修复的 Windows 兼容基线；当前结果会失败，不作为上传确认条件。
