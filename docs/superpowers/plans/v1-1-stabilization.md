# V1.1 Stabilization 实施计划

> **总控依据：** `docs/MASTER_DEVELOPMENT_DIRECTIVE.md`（第三条 阶段顺序、第五条 严禁 Guess-and-Fix、第二十九条 Definition of Done、第三十条 阶段禁止越权、第三十一条 当前只允许 V1.1）
> **Spec 位置：** `.superpowers/sdd/v1-1-stabilization/progress.md`（执行时新建 SDD ledger）
> **前置计划：** `docs/superpowers/plans/erp-v2-phase-0-1.md`（V1 实施计划，用于对照"声称完成 vs 实际交付"）
> **目标：** 让 V1 已交付能力可重复验证、可安全运行、可回归，并把 V2/V3 依赖的工程基础补齐到"不再有已知阻断缺陷"。**不新增业务功能。**

---

## 0. 边界声明（先读这一段）

**V1.1 只做 Stabilization，含义限定为：**

1. 保护并冻结 V1 已验证能力（594 商品同步、自动上品 Pipeline、图片 QC、人工确认门、脱敏）。
2. 修复已发现缺陷，**每条缺陷必须有：复现证据 → 根因假设 → 测试 → 修复 → 验证**（总控第五条）。
3. 补齐"已交付能力的可靠性/可观测性/安全性"：任务状态机、并发保护、属性落库、审计、自检覆盖。
4. 做**无行为变化**的分层重构（抽出 `base_client`），为总控第七条的目标结构做准备，但不改任何对外行为。

**V1.1 明确不做（属 V2/V3/V4/V5，越权禁止）：**

| 不做项 | 归属阶段 | 说明 |
| --- | --- | --- |
| 库存 / 价格 / 订单 / 财务 / 仓库同步 | V2 | 不新增任何 Ozon 写或新只读域 |
| 多租户（organizations/users/memberships/organization_id） | V3 | 本次不加租户字段与隔离逻辑 |
| 注册/登录/JWT/RBAC/SaaS 部署 | V3 | 认证仍沿用现有 LAN + 主电脑角色 |
| AI Provider / 图片存储抽象 / 商业化 / 计费 | V4 | 不引入 |
| Kaspi / Wildberries | V5 | 不引入 |

**修改铁律：** 任何重构必须 backward compatible；数据库变更必须走 Migration 且先备份；不改 `products/`、`batches/`、已有 Ozon 提交记录（总控第二条）。

---

## 1. 现状盘点（基于源码通读 + 真实运行数据实测）

### 1.1 目录与部署现状（实测）

| 项 | 实测结果 |
| --- | --- |
| 权威源码目录 | `C:\Users\PC\Documents\Codex\2026-09-10\...\work\jlc-global-ozon-auto-listing-main` |
| 部署/运行目录（含真实数据与店铺密钥） | `D:\ozon自动化 牛\ozon-auto-listing` |
| 两目录关键文件一致性 | `backend/*` 5 个文件 + `collector/local-ingest/app.py` 的 SHA256 **完全一致** |
| 版本控制 | **无 `.git`**（ZIP 交付丢失仓库元数据），因此无 diff / 无回滚 / 无 CI |
| 同步机制 | 人工复制，**无校验脚本** → 漂移风险 |
| 真实 ERP 数据库 | 部署目录 `runtime/erp.sqlite3`（源码目录无此文件） |

### 1.2 V1 已交付且必须保护的能力（真实数据证据）

来自部署目录 `runtime/erp.sqlite3` 实测：

| 指标 | 实测值 |
| --- | --- |
| stores | 1（`id=1`, `connection_status=CONNECTED`, `credential_ref=ozon-adapter/.env.1`） |
| products | **594** |
| skus | 500（`seller_sku` 去重后 500，无重复） |
| marketplace_listings | **594**（`attributes_status` 全部 `SYNCED`） |
| sync_jobs | 6（全部 `SUCCEEDED`，最近一次 `2026-09-22 17:08` → `17:12`，594/594） |
| sync_job_logs | 96（分页/详情/属性的耗时与条数留痕） |
| 数据质量 | `remote_title` 空 0 条、`remote_price` 空 0 条、`category_id` 空 0 条、无图 8 条 |

其他已验证能力（源码确认）：自动上品 Pipeline（`scripts/pipeline_runtime.py` 15 步）、图片 QC（`scripts/image_qc.py --hard-gate`）、整批人工确认门（`scripts/batch_upload_approval.py` + 自检 `batch_upload_gate`）、API Key 基础脱敏（`_safe_error` / `_safe_message` / 仅存 `credential_ref`）。

### 1.3 后端分层现状 vs 总控第七条要求

| 总控第七条建议 | 实际 | 结论 |
| --- | --- | --- |
| `backend/integrations/ozon/base_client.py` | **不存在**（重试/脱敏/日志内联在 `product_client.py`） | V1.1 补（纯重构） |
| `backend/integrations/ozon/account_client.py` | **不存在** | 待裁决（见 §6） |
| `backend/integrations/ozon/product_client.py` | 存在（149 行） | 保留 |
| `inventory/price/warehouse/order/finance_client.py` | 不存在 | V2 |
| `backend/services/*` | 仅 `ozon_catalog_sync_service.py` | `store_service` / `product_service` 缺失（待裁决） |
| `backend/api/*` | 仅 `ozon_products.py` | 缺 `/api/erp/stores`（待裁决） |
| `collector/local-ingest/app.py` | **3243 行单体**，承载全部工作台路由 | 渐进迁移，禁止继续堆 |

### 1.4 回归基线（2026-09-22 实测，部署目录）

| 项 | 实测结果 |
| --- | --- |
| 全量测试（逐模块方式，65 个模块） | **Ran 774 tests / failures=14 / errors=5 / skipped=4 / 144s / RC=1** |
| 全量测试（`unittest discover` 方式） | **直接报错退出**（见 P0-2） |
| ERP 相关模块（5 个抽样） | 全部 OK |
| 失败聚类 ① | Windows 路径分隔符（`\` vs `/`）约 10 个，如 `test_ozon_ecommerce_designer_contract.py:595`、`test_image_workflow_fix.py:415` → **P1-12** |
| 失败聚类 ② | 密钥文件权限断言 2 个：`test_p0_task_database.py:115`、`test_workbench_gap_fill.py:103`（`438 != 384`）→ **P0-1** |
| 失败聚类 ③ | error 5 个已全部归档：`test_async_image_channels`（subprocess 用 POSIX 脚本路径）／`test_image_host_recovery`（`Popen` → `WinError 2`）／`test_designer_directed_overlay` + `test_image_workflow_fix`（PIL 字体加载失败）→ **P1-13** |
| 失败聚类 ④ | `test_workbench_gap_fill.test_41_failed_store_retry_targets_only_that_store`：`app.py:2825` 抛 409，测试期望与当前安全门语义不一致 → **待裁决 F** |

> 基线日志必须在 Task 0 落盘留档，**禁止引用"据上次记录"的数字**。

---

## 2. 缺陷清单（按优先级）

> 每条均给出**实测证据**、**根因假设**、**修复方向**、**验收方式**。未经复现的猜测不进入本计划。

### P0-1 Ozon 密钥明文文件在 Windows 上权限为 `0666`（全员可读）

- **证据：** `os.stat("D:\ozon自动化 牛\ozon-auto-listing\ozon-adapter\.env.1").st_mode = 0o100666`（79 字节，含真实 Client-Id / Api-Key）。
  **并且已有两个测试正在红：** `tests/test_p0_task_database.py:115` 与 `tests/test_workbench_gap_fill.py:103` 断言 `mode == 0o600`，实测 `438 != 384`。即这不是"新发现的风险"，而是**当前基线里已存在的失败测试**。
- **根因假设：** `scripts/workbench_stores.py:141` 与 `backend/db/erp.py:179` 使用 `path.chmod(0o600)`；Windows 的 `os.chmod` 只映射"只读位"，不设置 ACL，因此收紧无效。
- **修复方向：** Windows 下改用 `icacls` 显式 ACL（仅当前用户 + SYSTEM，移除 `Everyone`/`Users`）；抽出 `CredentialStore` 接口（`LocalEnvCredentialStore` 现在实现，DPAPI 实现留 V3）；密钥文件内容绝不进日志/导出/报告。
- **验收：** 新测试断言 Windows 下密钥文件 ACL 仅当前用户可读；`project_self_check` 新增权限检查项；自检报告不包含任何密钥内容。
- **关联总控：** 第十二条。

### P0-2 全量自动测试在本机无法运行

- **证据：** 实测 `python -m unittest discover -s tests -p "test*.py"` → `ImportError: Start directory is not importable: '...\tests'`（RC=1）；`tests/__init__.py` **不存在**；Python 3.11 起 `unittest` 移除了对命名空间包的发现支持。逐个模块运行则通过（实测 65 个模块 **774 用例**可执行，见 §1.4）。
  同时 `scripts/project_self_check.py:384` 硬编码 `.venv/bin/python`（Windows 实际为 `.venv\Scripts\python.exe`），`check_javascript` 硬编码 `/opt/homebrew/bin/node` 等 POSIX 路径 → Windows 上永远降级为 warning。
- **根因假设：** 缺少 `tests/__init__.py`（单一根因），叠加两处 Windows 路径硬编码。
- **修复方向：** 补 `tests/__init__.py`；`run_full_tests` 按平台选择解释器；`check_javascript` 用 `shutil.which("node")`。
- **验收：** `python -m unittest discover -s tests -p "test*.py"` 实际跑完并输出真实用例数；`project_self_check.py --full-tests` 决策为 PASS；报告中 `javascript_syntax` 不再因"找不到 node"降级。
- **关联总控：** 第二十九条（第 4 条：所有测试实际运行）、第二十七条。

### P0-3 ERP API 未接入权限门

- **证据：** `backend/api/ozon_products.py` 三个端点**均未调用** `require_owner_role`；`app.py:1659` 的 `require_owner_role` 只用于店铺/系统设置类路由。`POST /api/erp/stores/{store_id}/products/sync` 可被局域网内任意已授权设备触发（消耗真实店铺 API 配额）。
- **根因假设（单一）：** ERP router 挂载时未附加任何 dependencies（`app.py:3243` 仅 `app.include_router(...)`）。
- **修复方向：** 拆分读/写 router —— `router`（商品列表、任务查询）与 `write_router`（触发同步）；`app.include_router(write_router, dependencies=[Depends(require_owner_role)])`。不改 `backend` 内部实现，不产生循环导入。
- **验收：** 非主电脑触发同步 → 403；主电脑 → 202；读取列表仍可用。
- **关联总控：** 第九条（权限边界）、第十三条（危险/远程操作）。

### P0-4 同步任务状态机缺失 + 无并发/陈旧任务保护

- **证据：**
  - 真实库 6 个 job 全部 `SUCCEEDED`，其中 2 个 message 含"**100 个商品属性待重试**"但 `failure_count=0`（状态误报成功）。
  - 16:04–17:12 内针对同一店铺重复触发 6 次全量同步 → `start_product_sync` 无并发/去重判断。
  - 代码中只在异常时落 `FAILED/PARTIAL`，无 `RETRYING / INTERRUPTED / CANCELLED / WAITING_USER`（总控第十四条要求 9 态）。
  - 进程重启后 `RUNNING` 任务永久悬挂；前端 `workbenchApi.ts:394-402` 为 `while(true)` 轮询（750ms，**无超时**）→ 任务卡住则界面永久转圈。
- **根因假设：** 任务系统只实现了"最简两态 + 异常兜底"，缺生命周期管理层。
- **修复方向：**
  1. 补齐 `RETRYING / INTERRUPTED / CANCELLED`（`WAITING_USER` 本次不实现，留到需要人工确认的任务类型，标记 `NOT IMPLEMENTED`）。
  2. 同店铺已有 `QUEUED/RUNNING` 的 `OZON_PRODUCT_SYNC` 时返回 409 并回传现有 `job_id`（幂等去重）。
  3. 服务启动时 stale recovery：`RUNNING/QUEUED` → `INTERRUPTED`。
  4. 新增 `POST /api/erp/sync-jobs/{job_id}/cancel`（置 `CANCELLED`，worker 在批次边界协作检查）。
  5. 属性失败计入 `failure_count`，且 `attribute_failures > 0` 时终态降为 `PARTIAL`。
  6. 前端轮询加超时（10 分钟）与"取消"入口，超时给出可读文案（总控第十八条：不暴露内部 ID/堆栈）。
- **验收：** 每项一个测试（mock transport），含"同一店铺并发两次 → 第二次 409"。
- **关联总控：** 第十四条。

### P1-5 属性数据读取后未落库

- **证据：** `OzonCatalogSyncService.run()` 的 ATTRIBUTES 阶段调用 `/v4/product/info/attributes` 后**只更新 `attributes_status`**，响应内容被丢弃；`marketplace_listings` 无属性列 → 总控第二条"Attribute读取"实际只留下状态标记。
- **修复方向：** Migration `005` 增加 `marketplace_listings.attributes_json TEXT` 与 `attributes_synced_at TEXT`；service 落库；保留 `attributes_status` 语义不变。
- **验收：** mock transport 下属性可回读；重复同步幂等；旧库升级后旧行 `attributes_json` 为空但不报错。
- **关联总控：** 第二条、第八条。

### P1-6 `sync_job_items` / `audit_logs` 建表但零写入

- **证据：** 全仓 grep 仅命中 `backend/db/erp.py` 的建表语句，无任何 INSERT。
- **影响：** 单个商品失败无法定位（违反总控第十四条"一个 SKU 失败不能让整个批次丢失"的可追踪性）。
- **修复方向：** 详情/属性阶段对失败批次逐项写 `sync_job_items`；对"触发同步 / 取消 / 凭证变更"写 `audit_logs`。
- **验收：** 测试断言失败批次产生对应条目数与错误摘要。

### P1-7 每次仓储调用都重跑全量建表脚本

- **证据：** `backend/db/erp.py:204-212` 的 `database()` 每次都调用 `initialize(root)` → `executescript(SCHEMA)` + 逐列 `PRAGMA table_info`。594 商品多批写入时重复开销与锁竞争。
- **修复方向：** 进程内一次性初始化（按数据库路径缓存 + 锁），保留 `initialize()` 公开幂等入口供显式调用与测试。
- **验收：** 新库首次调用建表、二次调用不重复执行；并发调用不产生 "table already exists" 或死锁。

### P1-8 商品列表聚合在 JOIN 膨胀下失真

- **证据：** `backend/repositories/erp.py:350` `SUM(COALESCE(l.remote_stock, 0))` 未做去重，而 `products/skus/marketplace_listings` 三表 LEFT JOIN 会按 sku×listing 交叉膨胀（`sku_count/listing_count` 已用 `COUNT(DISTINCT)`，仅 `SUM` 遗漏）。
- **修复方向：** 改为子查询预聚合后 JOIN。
- **验收：** 多 SKU + 多 listing 场景的仓库测试断言库存数值正确。

### P1-9 缺少 `base_client` 分层（无行为变化的重构）

- **证据：** 重试、退避、脱敏、日志全部内联在 `product_client.py`；`account_client.py` 缺失，后续客户端无法复用。
- **修复方向：** 新建 `backend/integrations/ozon/base_client.py`（`_request_once` / `_post_json` / `_safe_message` / 结构化日志 / 重试），`OzonProductClient` 继承并**保持全部公开方法、端点常量、异常类型不变**。
- **验收：** 现有 8 个 Ozon 同步测试与 3 个仓库测试全部保持 GREEN；`OzonProductClient` 对外签名零变化。
- **关联总控：** 第七条。

### P1-10 无源码/部署一致性校验

- **证据：** 两目录当前一致，但靠人工复制，无校验；无 `.git`。
- **修复方向：** 新增 `scripts/verify_deploy_sync.py`：按配置（参数或环境变量）比对权威目录与部署目录关键文件 SHA256，输出 PASS/FAIL 与差异清单；**只读，不复制**。
- **验收：** 人为改动部署目录某文件后脚本返回 FAIL 并列出文件。

### P1-11 项目自检不覆盖 ERP

- **证据：** `scripts/project_self_check.py` 无任何 ERP 检查；`REQUIRED_FILES` 不含 `backend/`；V1 计划 Task 8 的"新增 ERP schema / 脱敏 / 零远程写断言"**未交付**。
- **修复方向：** 新增只读检查项：ERP schema 版本一致、`credential_ref` 非明文、密钥文件权限、最近同步 job 的远程写计数状态。
- **验收：** 自检报告新增 4 项且默认 PASS。

### P1-12 Windows 路径分隔符不一致（`\` vs `/`）

- **证据：** 基线中约 10 个失败全部为同类，例如
  `tests/test_ozon_ecommerce_designer_contract.py:595` 期望 `products/P000931/input/sku-images/sku-1.png`，实际 `products\P000931\input\sku-images\sku-1.png`；
  `tests/test_image_workflow_fix.py:415` 同类。
- **根因假设：** 代码里同时存在两种路径序列化：`app.py:47` 的 `project_relative()`（`str(Path)` → Windows 上为 `\`）与产物 JSON 中长期使用的 POSIX 风格 `/` 字面量；测试断言写死 `/`，Windows 下必然红。
- **修复方向：** 统一由一个"路径序列化"函数产出（建议统一 `as_posix()`），在边界处转换；测试改为平台无关断言。**必须逐项核对受影响产物字段**（`ozon-ecommerce-design.json`、`image-plan.json` 等），确认除分隔符外内容不变，避免破坏已验证能力（总控第二条）。
- **验收：** 基线中这类失败全部转绿；改动前后关键产物字段 diff 仅分隔符差异。

### P1-13 Windows 基线兼容失败（字体 / 子进程路径）

- **证据（全部实测）：**
  - `tests/test_async_image_channels.py:18` → `subprocess.run(['...python.exe', 'scripts/performance_regression_20.py'])` 返回码 1（POSIX 风格脚本路径）。
  - `tests/test_image_host_recovery.py:129` → `subprocess.Popen` 抛 `FileNotFoundError: [WinError 2]`。
  - `tests/test_designer_directed_overlay.py:30`、`tests/test_image_workflow_fix.py:568` → PIL `ImageFont.truetype` 字体加载失败（本机字体资源缺失或路径硬编码）。
- **根因假设：** 测试与脚本调用沿用 macOS 假设（POSIX 路径、系统字体），Windows 上两类资源均不可用。
- **修复方向：** 脚本调用统一用 `Path`/`sys.executable` 构造；字体改为随仓库提供或按平台探测系统字体目录，探测不到时测试显式 skip 并注明原因（**不允许静默跳过**）。
- **验收：** 5 个 error 归零；无法修复的必须有书面说明并标记 `NOT VERIFIED`。

### P1-14 `test_41` 业务语义不一致（转待裁决 F）

- **证据：** `tests/test_workbench_gap_fill.py:527` 期望"仅重试指定失败店铺"成功，实际 `app.py:2825` 抛 409（内容已变更需重新整批确认）。
- **判断：** 这不是平台兼容问题，而是**测试期望与当前安全门语义不一致**——要么测试过期，要么安全门太严。属于产品决策，**不在 Stabilization 默认修复范围**（见 §6 待裁决 F）。

### P2-13 日志非结构化、无 request/job 关联

- **证据：** `product_client.py:105` 输出 `OZON API endpoint=%s duration_ms=%d ...` 文本日志，无 JSON、无 job_id。
- **修复方向：** 统一为单行 JSON（含 `job_id` / `endpoint` / `duration_ms` / `status` / `attempt`），保持脱敏。
- **验收：** 测试断言异常与日志文本中不含 Client-Id / Api-Key。

### P2-14 缺少 `/ready` 与 ERP 数据库健康探针

- **证据：** 仅 `app.py:1753` 的 `/health`（不检查数据库）。
- **修复方向：** 新建 `backend/api/erp_health.py`（router，`/ready` 检查 SQLite 可读 + schema 版本），`app.py` 只增加一行 `include_router`（禁止往 app.py 堆逻辑）。
- **验收：** 数据库不可用时 `/ready` 返回 503，`/health` 仍 200。

### P2-15 Windows 路径硬编码（并入 P0-2 修复）

### P2-16 无 CI/CD

- **证据：** 无 `.github/`；无 `.git`。
- **处理：** 列为待裁决项 D（见 §6），V1.1 默认不建立仓库。

---

## 3. 任务拆分（TDD：每个任务先 RED 后 GREEN）

> 执行约定：每个 Task 结束立即运行指定命令；失败不进入下一 Task。所有 Ozon 调用使用注入 transport，**禁止测试写真实 Ozon**（总控第二十七条）。

### Task 0：基线冻结（先保护，再动手）

**文件：** 新建 `scripts/verify_deploy_sync.py`；新建 `docs/superpowers/plans/v1-1-stabilization-baseline.md`（记录基线数字）
**动作：**
- [ ] 备份部署目录 `runtime/erp.sqlite3` 到 `runtime/backups/erp-20260922-before-v1.1.sqlite3`（真实 594 数据，禁止覆盖）。
- [ ] 运行并**记录真实全量测试基线**（用例数 / 跳过 / 失败清单），不得使用"据上次记录"。
- [ ] 记录 `project_self_check.py` 基线（含 `--full-tests` 的实际结果）。
- [ ] 记录前端 `npm run typecheck` / `npm run build` 真实结果。
**验收：** 基线文档含 4 组真实命令输出与时间戳。

### Task 1：P0-2 测试可运行性 + Windows 路径

**文件：** 新建 `tests/__init__.py`；修改 `scripts/project_self_check.py`
- [ ] RED：确认 `unittest discover` 失败；新增测试断言 `tests` 为可导入包。
- [ ] GREEN：补 `tests/__init__.py`；`run_full_tests` 按平台选解释器；`check_javascript` 用 `shutil.which("node")`。
- [ ] 运行：`python -m unittest discover -s tests -p "test*.py"`、`python scripts/project_self_check.py --full-tests`。

### Task 2：P0-1 密钥文件权限（Windows ACL）

**文件：** 新建 `scripts/credential_store.py`；修改 `scripts/workbench_stores.py`（`save_credentials`）、`backend/db/erp.py`（移除无效 chmod 或改为调用统一实现）
- [ ] RED：测试断言 Windows 下密钥文件 ACL 不含 `Everyone`/`BUILTIN\Users`。
- [ ] GREEN：实现 `LocalEnvCredentialStore.secure(path)`（Windows 走 `icacls`，POSIX 走 chmod 0600），保留文件读写格式不变（backward compatible）。
- [ ] 校验：对部署目录现有 `.env.1` 执行一次收紧并复核 `st_mode` / ACL。

### Task 3：P0-3 ERP 写操作权限门

**文件：** 修改 `backend/api/ozon_products.py`、`collector/local-ingest/app.py`
- [ ] RED：新增 `tests/test_erp_api_guard.py` —— 非主电脑 `POST .../products/sync` 期望 403。
- [ ] GREEN：拆分 `write_router`，`include_router(write_router, dependencies=[Depends(require_owner_role)])`。
- [ ] 回归：主电脑仍 202；商品列表/任务查询不受影响。

### Task 4：P0-4 任务状态机（建议拆 4a–4d 顺序执行）

**文件：** 修改 `backend/repositories/erp.py`、`backend/services/ozon_catalog_sync_service.py`、`backend/api/ozon_products.py`、`collector/workbench-command-center/src/services/workbenchApi.ts`
- **4a** 并发去重：RED（同店铺二次触发期望 409 且回传 `job_id`）→ GREEN。
- **4b** 状态补全：RED（属性失败时期望 `PARTIAL` 且 `failure_count>0`）→ GREEN。
- **4c** 陈旧恢复 + 取消：RED（启动 recovery 把遗留 `RUNNING` 置 `INTERRUPTED`；`cancel` 后 worker 在批次边界退出并置 `CANCELLED`）→ GREEN。
- **4d** 前端轮询：RED（超时/取消后有可读提示，不无限等待）→ GREEN。
- [ ] `WAITING_USER` 标记 `NOT IMPLEMENTED` 并写入 Known Issues。

### Task 5：P1-5 属性落库（含 Migration）

**文件：** 新建 `migrations/005_erp_attributes.sql`；修改 `backend/db/erp.py`（SCHEMA + MIGRATION_COLUMNS）、`backend/repositories/erp.py`、`backend/services/ozon_catalog_sync_service.py`
- [ ] RED：mock transport 下断言属性内容可回读、重复同步幂等。
- [ ] GREEN：加列 + 落库 + 回填策略（旧行 `attributes_json` 为 NULL）。
- [ ] 迁移前备份（Task 0 已完成），迁移后校验 594 行不丢失。

### Task 6：P1-6 任务明细与审计

**文件：** 修改 `backend/repositories/erp.py`、`backend/services/ozon_catalog_sync_service.py`、`backend/api/ozon_products.py`
- [ ] RED：断言失败批次写入 `sync_job_items`、关键动作写入 `audit_logs`。
- [ ] GREEN：实现，保证失败不影响整批（总控第十四条）。

### Task 7：P1-7 初始化一次化

**文件：** 修改 `backend/db/erp.py`
- [ ] RED：断言同一进程内第二次仓储调用不重复执行建表脚本（计数器/探针）。
- [ ] GREEN：进程内缓存 + 锁；保留 `initialize()` 幂等公开入口。

### Task 8：P1-8 聚合修正

**文件：** 修改 `backend/repositories/erp.py`
- [ ] RED：多 SKU + 多 listing 场景断言 `remote_stock` 汇总值正确。
- [ ] GREEN：子查询预聚合。

### Task 9：P1-9 `base_client` 抽取（无行为变化）

**文件：** 新建 `backend/integrations/ozon/base_client.py`；修改 `product_client.py`
- [ ] RED/GREEN：以"现有测试全绿 + 签名不变"为验收；新增 `test_ozon_base_client.py` 覆盖重试/退避/脱敏。

### Task 10：P1-10 / P1-11 部署校验 + 自检 ERP 项

**文件：** 新建 `scripts/verify_deploy_sync.py`（Task 0 已建，此处完善）；修改 `scripts/project_self_check.py`
- [ ] RED：人为改动部署目录文件 → 期望 FAIL；自检缺 ERP 项 → 期望 FAIL。
- [ ] GREEN：实现只读校验与 4 项 ERP 自检。

### Task 11：P1-12 / P1-13 Windows 兼容基线归零

**文件：** 路径序列化入口（`app.py:47` `project_relative()` 及产物写入处）、`scripts/performance_regression_20.py` 调用处、`scripts/run_batch.py:303` 的 `Popen`、字体加载（`scripts/designer_directed_overlay.py:75`、`scripts/image_text_overlay.py:61`）及相关测试
- [ ] RED：基线中这些失败/错误已是红色项（见 §1.4 聚类 ①③），无需新造失败用例。
- [ ] GREEN：统一路径序列化（建议 `as_posix()`）+ 脚本调用改用 `sys.executable` 与 `Path` + 字体按平台探测（探测不到时**显式 skip 并写明原因**，禁止静默跳过）。
- [ ] 核对：改动前后 `ozon-ecommerce-design.json` / `image-plan.json` 等产物字段 diff 仅分隔符变化（保护已验证能力）。
- [ ] 验收：聚类 ①③ 全部归零。

### Task 12：P2-13 / P2-14 日志与就绪探针

**文件：** 新建 `backend/api/erp_health.py`；修改 `backend/integrations/ozon/base_client.py`、`collector/local-ingest/app.py`（仅增加一行 include）
- [ ] RED：日志含密钥 → 失败；`/ready` 在 DB 不可用时非 200 → 失败。
- [ ] GREEN：结构化 JSON 日志 + `/ready`。

### Task 13：回归、文档与交付（完成后 STOP）

- [ ] 全量测试实际运行并输出真实数字（与 Task 0 基线对比：基线 774 用例 / 14 failures / 5 errors，目标 failures=0、errors=0 或逐条列入 Known Issues）。
- [ ] `project_self_check.py --full-tests` PASS。
- [ ] 前端 `npm run typecheck` + `npm run build` PASS。
- [ ] 真实只读同步回归一次（**需用户明确批准**，目标：594/594，Ozon 写计数 0、库存写计数 0）。
- [ ] 更新 `README.md` / `PROJECT_HANDOFF.md` / `.superpowers/sdd/v1-1-stabilization/progress.md`。
- [ ] 输出：修改文件清单、Migration、API 变化、UI 变化、测试结果、Known Issues。

---

## 4. 数据库迁移策略

- **现状：** `migrations/004_erp_v2_phase_0_1.sql` 是注释式占位，真实 DDL 在 `backend/db/erp.py` 的 `SCHEMA` + `MIGRATION_COLUMNS`（ALTER TABLE 增量）。SQLite 的 `ALTER TABLE ADD COLUMN` 不可回滚。
- **V1.1 规则：**
  1. 任何迁移前先备份 `runtime/erp.sqlite3`（Task 0 已备份，Task 5 前再备份一次）。
  2. 新增 `migrations/005_erp_attributes.sql` 作为**文档化说明**，并在 `backend/db/erp.py` 中同步实现（`SCHEMA` 加列 + `MIGRATION_COLUMNS` 增量），两者必须一致并由测试校验。
  3. 迁移必须幂等（重复启动不报错、不重复加列）、不删除列、不改现有列语义。
  4. Alembic 引入**不在 V1.1**（SQLite→PostgreSQL 双库切换属 V3 范围）；V1.1 只保证"迁移可重复执行 + 可备份恢复"。

---

## 5. 测试与验证策略

| 层级 | 范围 | 约束 |
| --- | --- | --- |
| Unit | service / client / 状态机 | 注入 transport，禁止网络 |
| Repository | SQLite 临时库 | 临时 root，断言字面量 |
| API | FastAPI TestClient | 覆盖权限门与 409/403/202 |
| Marketplace Mock | Ozon 响应夹具 | 默认 mock，禁止写真实 Ozon |
| E2E/手工 | 工作台真实同步 | 需用户批准，写计数必须为 0 |

**Windows 命令（部署目录）：**

```powershell
cd "D:\ozon自动化 牛\ozon-auto-listing"
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py"
.\.venv\Scripts\python.exe scripts\project_self_check.py --full-tests
.\.venv\Scripts\python.exe scripts\verify_deploy_sync.py --target "D:\ozon自动化 牛\ozon-auto-listing"
```

```powershell
cd "D:\ozon自动化 牛\ozon-auto-listing\collector\workbench-command-center"
npm run typecheck
npm run build
```

---

## 6. 待裁决项（**默认不执行**，需用户明确批准才进入 V1.1 或后续阶段）

| 编号 | 事项 | 现状 | 建议 |
| --- | --- | --- | --- |
| A | 店铺中心 API `/api/erp/stores*` 与 Store Center UI | V1 计划 Task 6/7 未交付；当前店铺管理走 legacy `workbench_stores.py` | 建议放入 V2 起，或作为 V1.1 收尾（需批准） |
| B | 商品中心 UI（`components/erp/*`、`types/erp.ts`） | 未交付；594 商品当前挂在"搜索可见度"流程展示 | 建议 V2 前做（需批准） |
| C | Legacy 商品索引（`ProductService.index_legacy_products`、`scripts/migrate_erp_v2.py`） | 未交付 | 建议与 A/B 同批处理 |
| D | 建立 Git 仓库与 CI | 无 `.git`，无法 diff/回滚/CR | **强烈建议优先级最高**，但会改变项目现状，需批准 |
| E | DPAPI / Windows Credential Manager 全面替换 | V1.1 只做文件 ACL + `CredentialStore` 抽象 | V3 随认证一起做 |
| F | `test_41_failed_store_retry_targets_only_that_store` 语义裁定 | 测试期望"只重试指定失败店铺"，实际 `app.py:2825` 抛 409（内容变更需重新整批确认） | 需产品决策：更新测试 or 放宽安全门。**默认不改**（安全门从严优先） |

---

## 7. Definition of Done（逐条映射总控第二十九条）

| # | 要求 | V1.1 落实方式 |
| --- | --- | --- |
| 1 | 前后端真实接通 | 商品列表/同步/任务进度沿用真实 API，无 mock 数据进入生产路径 |
| 2 | Production 不使用 Mock Data | mock 仅存在于测试中 |
| 3 | 有自动测试 | Task 1–12 每个缺陷至少 1 个新测试 |
| 4 | 所有测试实际运行 | Task 13 输出真实 `Ran N tests` |
| 5 | Build 实际通过 | `npm run typecheck` + `npm run build` 实际运行 |
| 6 | Secret 不泄漏 | Task 2 + 自检项 + 日志脱敏测试 |
| 7 | Migration 正确 | §4 备份 + 幂等 + 一致性测试 |
| 8 | 旧功能没有回归 | Task 13 与 Task 0 基线对比 |
| 9 | 用户可以实际操作 | 真实只读同步回归（批准后） |
| 10–13 | 无 Placeholder / TODO；未完成标 `NOT IMPLEMENTED`；未验证标 `NOT VERIFIED` | `WAITING_USER` 等标记 `NOT IMPLEMENTED`；未跑的验证标记 `NOT VERIFIED` |
| 14–19 | 输出修改文件 / Migration / API 变化 / UI 变化 / 测试结果 / Known Issues | Task 13 统一输出 |
| 20 | 阶段结束停止等待审核 | Task 13 结束后 STOP |

---

## 8. Known Issues（当前已知，V1.1 不承诺全部修复）

1. **SKU 数量 500 < 商品 594**：94 个商品在 `/v3/product/info/list` 中无 `sku` 值（或为空），当前不建 SKU 行。V1.1 只增加可见性统计，不改业务逻辑（**NOT VERIFIED**：需一次真实响应抽样确认字段缺失原因）。
2. **8 个 listing 无图片**：远端无图，非同步缺陷。
3. **属性接口不稳定**：历史 job 出现过"594 个属性全失败 → 100 个 → 0 个"，疑似限流；V1.1 通过状态/重试可见性处理，不做速率限制改造（V2）。
4. **无 Git 仓库**：无法 diff/回滚/CR，见待裁决项 D。
5. **前端 npm high-severity 依赖告警**（来自 V1 基线）：V1.1 记录，不升级依赖。
6. **`app.py` 3243 行单体**：渐进迁移，V1.1 仅保证"新增代码不进 app.py"。
7. **真实数据与源码分离**：真实 DB 与密钥只在部署目录，源码目录无数据；见 Task 0 / Task 10。
8. **Windows 基线 14 failures + 5 errors**（2026-09-22 实测）：本计划承担修复 ①②③ 聚类；聚类 ④（`test_41`）待裁决，若维持不改，则最终基线允许保留 1 条已知失败并书面说明（**不允许悄悄从测试列表里删除**）。
9. **字体/子进程脚本的 Windows 可用性**：修复后仍需一次真实 Windows 手工验证（图片叠加与 20 商品性能回归），**NOT VERIFIED** 直到验证完成。

---

## 9. 停止点

本计划执行到 **Task 13 结束即 STOP**，输出交付报告并等待审核。未经批准不进入 V2。
