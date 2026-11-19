# ERP 店铺、完整 SKU 与运营分析实施计划

> 使用 superpowers:executing-plans 逐项实施；每项先失败测试再最小实现。

**Goal:** 按用户规划交付可实际操作的店铺中心、SKU 商品中心和六个运营分析页面。
**Architecture:** 复用 FastAPI、SQLite、已有 Repository/Job/Ozon transport；分析独立于商品同步。页面只读取真实持久化数据。
**Tech Stack:** Python 标准库、FastAPI、React/Vite、现有 MV3 扩展工具链。
**Spec:** `docs/superpowers/specs/2026-10-02-store-analytics-product-center-design.md`。

## 全局约束

- 2026-10-02 用户“继续按照我的规划完成erp的开发”授权本设计的计划与实现；不再按旧设计稿的“只写文档”状态停工。
- 在既有 v1.1b-development worktree 工作，保留全部既有改动，冻结 demo-v1.1a。
- 不制造 seller ID、销量、SKU 变体或查询 endpoint；未知=null；跨店、组织、币种不混合。
- 不执行 Ozon 商品/价格/库存/评价写入；整批预览确认门禁保留。
- 自动测试无真实凭证、无外部网络。Cookie 协议未核实的部分必须失败关闭。
- 运行目录 D:\下载文件 与开发目录不同；实际应用前备份、核对改动，单独验证用户入口。

## Review Focus

- 顶层与 sources 重复 SKU 不虚增 Product/本地 SKU，跨店不覆盖。
- SKU/货号检索覆盖平台 SKU，不丢失已有搜索语义。
- 分析失败保留上次成功快照，分页未完成不称完整，金额用 Decimal。
- 0 与未知分开，漏斗分母及 ABC 完整性/币种严格验证。
- Cookie 会话身份、配对、nonce、DPAPI/ACL、重放和写接口隔离。

### Task 1: 完整平台 SKU 与商品中心

Files: migrations/013_listing_platform_skus.sql、backend/db/erp.py、backend/repositories/erp.py、tests/test_platform_skus.py、src/types/product.ts、src/services/workbenchApi.ts、src/components/erp/ProductCenter.tsx。
Produces: Listing.platform_skus[{sku, source, shipment_type}]；商品 API 支持平台 SKU 和店铺筛选，原字段兼容。
- [ ] 测试 sources-only、重复来源、跨店同 SKU、旧数据幂等升级、查询与详情。
- [ ] 最小迁移与同步映射，SKU 关联不由 sources 制造变体。
- [ ] 商品中心增加店铺选择及清晰的全部 SKU/Listing 详情。
- [ ] 运行定向测试、旧商品同步回归、构建和浏览器验证。

### Task 2: 官方运营分析持久化读取

Files: migration 014、backend/integrations/ozon/analytics_client.py、backend/repositories/analytics.py、backend/services/analytics_service.py、backend/api/analytics.py、tests/test_analytics.py。
Consumes: Store 与 scope、OzonBaseClient、现有 SyncJob。Produces: 日期/来源/完整度明确的本地分析查询，独立只读刷新 Job。
- [ ] 基于本地 Swagger 写分页、受限指标、日期、失败保留、金额/ABC/漏斗测试并观察失败。
- [ ] 实现 adapter/repository/service 与精确计算；注册 GET 查询及主电脑只读刷新。
- [ ] 验证不同店铺、0/null、币种、权限、取消、分页与重试。

### Task 3: 店铺表格与六个运营分析页

Files: backend/api/stores.py、src/components/erp/StoreCenter.tsx、AnalyticsCenter.tsx、src/types/analytics.ts、src/services/workbenchApi.ts、src/main.tsx。
Consumes: Task 2 本地查询与 Job；Produces: 店铺筛选分页，六页日期/来源/状态/真实数据展示。
- [ ] API/UI 行为测试先失败，再实现最小表格与日期筛选。
- [ ] 六页支持的数据正常显示，空/缺权限/失败/部分数据清楚呈现。
- [ ] 实际构建与浏览器路径验证，不能以六个标题代替完成。

### Task 4: 本机绑定、受管会话与无人值守

Files: 独立绑定扩展、独立 loopback 服务、受管 DPAPI session 与每店铺 schedule；具体文件随协议核查固定。
Consumes: 已核实 Seller 后台身份/查询契约。Produces: 配对、单次绑定、隔离验证、默认关闭的 30 分钟刷新。
- [ ] 实现和测试本地安全协议及加密，保持原采集扩展权限不变。
- [ ] 未核实 Cookie 名称/身份协议时不猜测、不采集、不宣称已绑定。
- [ ] 在具体实现可审核后，仅为必要实际后台只读观察/权限扩展向用户说明需要的授权。

### Task 5: 回归、用户入口与交付

- [ ] 定向与完整 Python、typecheck/build、扩展构建、自检；明确 skip 和未验证项。
- [ ] 审查变更、备份并应用可验证功能到用户入口，实操验证。
- [ ] 生成无凭证源码包、解包复验与哈希；未达成目标不写完整 PASS。
