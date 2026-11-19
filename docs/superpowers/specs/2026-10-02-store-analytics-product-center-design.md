# V1.1B 定向增补：店铺绑定、运营分析与完整 SKU 商品中心

日期：2026-10-02。状态：**DRAFT / 等待用户审核**。本文件只描述待实施设计，不代表功能已完成、接口已实测或真实读取已获批准。

依据：`docs/MASTER_DEVELOPMENT_DIRECTIVE.md`、既有 V1.1B spec/plan，以及用户本轮确认。现有 B1–B9 记录保留，本增补属于同一 V1.1B 交付范围，不自动进入 V2。`AGENTS.md` 中旧的“不扩展运营分析”限制与本轮新增范围不同；本轮授权仅覆盖本文的只读运营分析，不授权订单处理、财务记账、库存或价格操作。实施前在计划中记录这个范围增补，保留原主指令的历史文本。

## 1. 已确认的产品目标

- 评价中心及底层代码暂时保留，本轮不删除、不继续扩展。
- 店铺中心参照用户图 4 的表格、查询、状态与分页组织方式，运营分析参照图 5 的六个标签页；只参考信息架构，不复制第三方品牌、代码或素材。
- 同一版交付数据概览、所有指标、销售漏斗、ABC 分析、销售领先产品、搜索位置六个页面。
- 商品中心展示已绑定店铺中已读取的全部商品及其 SKU，支持店铺筛选、SKU 和货号查询。完整性必须有分页完成和同步结果证据，不能把一个本地样例称为全店商品。
- 数据来源顺序为官方 Seller API 优先，经过核实的 Seller 后台 Cookie 只读访问补充。无权限、无数据、未同步和不支持分别表示，不以假数据替代。
- 用户在主电脑打开官方 Ozon Seller 后台，切换到每个目标店铺后分别点击扩展“绑定当前店铺”；扩展自动读取所需 Cookie，无需手工复制 Cookie，不进行后台持续采集。
- ERP 重启后保留绑定；关闭 Edge 后仍支持无人值守只读刷新。会话过期后重新绑定，不承诺永久登录。
- 已选方案 1：独立 loopback 接收入口，与对外 ERP 服务隔离；本机加密保存会话。其他电脑只查看本增补的数据，不绑定 Cookie、不启动刷新或配置计划。
- 使用 `v1.1b-development`，冻结 `demo-v1.1a`。FastAPI + React + SQLite + Repository/Service/Adapter 路线保持不变。

## 2. 当前实现与复用点

审查基线：分支 `v1.1b-development`，HEAD `079b20b`，2026-10-02 核对。四个现有未提交商品中心修复和所有未跟踪文件保留。本文件为新增文档，不改动它们。

| 当前文件/能力 | 现状 | 增补方式 |
|---|---|---|
| `backend/services/store_service.py`、`backend/api/stores.py` | 店铺列表、公司身份与 Product Read 验证 | 复用服务/仓储边界，增加会话和分析状态投影 |
| `StoreCenter.tsx` | 状态卡片，读取真实 API | 改成可筛选分页的店铺表格，保留真实未知/错误状态 |
| `backend/repositories/erp.py::list_product_center` | 已有店铺参数、分页、SKU/Offer/Product 搜索 | 保持原 API 兼容，补 Ozon SKU 查询和全部来源标识 |
| `ProductCenter.tsx` | Listing 卡片与简略 Product 详情 | 增加店铺筛选、表格和可展开 SKU/Listing 详情 |
| `OzonProductClient`、`OzonCatalogSyncService` | 全分页读同步、详情/属性、Job/取消/重试 | 复用，不把分析失败变成商品同步失败 |
| `CredentialStore` | API Key `.env.<store>` + Windows ACL，兼容旧 uploader | 保持兼容；Cookie 单独用 DPAPI，不能写入旧 `.env` |
| `sync_jobs`、`sync_cursors`、`audit_logs` | 持久化任务、范围、审计 | 增加分析任务类型和无敏感信息的事件，不引入外部队列 |
| 原采集扩展 | MV3，存在广泛 Ozon/HTTP host permissions，无 cookies 权限 | 复用构建方式；见下节的独立窄权限绑定扩展 |
| 现有主机识别 | 依赖请求客户端/本机地址 | Cookie 接收不复用此判断；代理可把远程流量转为 loopback |

### REFERENCE REPO REUSE MATRIX

| JLC模块 | 参考项目 | 参考内容 | KEEP/ADAPT/IGNORE | License Risk |
|---|---|---|---|---|
| Product / SKU / Listing | Saleor | Product、Variant 与 ChannelListing 各自身份和展示边界 | ADAPT 概念，KEEP JLC 数据与 ID | BSD-3-Clause；本轮无代码复制 |
| 店铺身份绑定 / 分析映射 | OCA Connector | Adapter、Mapper、Binder、Synchronizer 分工 | ADAPT 职责到现有服务，不引入 Odoo 组件框架 | AGPL-3；仅架构参考 |
| 分析刷新 / 去重 / 重试 | OCA Queue | 持久化 Job、identity_key 去重、有界 retry | ADAPT 到现有 SQLite Job | AGPL-3；仅架构参考 |
| 其他 ERP / Review 项目 | 已批准参考仓库 | 本增补不涉及对应新模块 | IGNORE 本轮实现 | 原许可证限制继续有效 |

参考目录 `D:\jlc-reference-repos` 保持只读。Saleor 本地 Git 对象已读取四个相关模型；OCA 快照目录只有稀疏工作树，相关 blob 本轮本地读取失败，未执行 fetch/checkout。OCA 职责和 Job 模式依据官方项目文档核对，不能声称已审阅缺失快照文件。本轮没有复制参考代码。

## 3. 店铺绑定与凭证通道

### 3.1 绑定扩展

新增轻量 Ozon Seller 绑定扩展包，沿用现有 MV3/TypeScript 构建方式。原因：给现有广 host permissions 的采集扩展增加 cookies 权限，会同时扩大其其他域名 Cookie 访问能力；独立包可保持最小权限且不影响原采集功能。

- 仅支持明确允许的官方 Seller origin，初版 `https://seller.ozon.ru`；不匹配任意 `*.ozon.ru`，不读取买家站或其他站点 Cookie。新增官方域名必须有实际核验证据。
- 使用可选 cookies 权限，在用户点击绑定时请求。Cookie 只进入扩展受信任后台的短期内存，不进入 content script、popup、DOM、页面 storage、console 或通用采集消息。
- 每次点击确认当前标签页确为允许的 Seller 页面，并选择 ERP 目标店铺。页面提供的名称只作提示，不能独自证明店铺身份。
- Cookie 名称、domain/path、store/partition 上下文只接受核实过的认证契约；不盲目收集整个浏览器 cookie store。分区 Cookie 不支持时明确拒绝，不能悄悄漏掉后仍显示成功。
- 原采集扩展继续运行，绑定扩展不导入其广权限 manifest 或通用转发接口。

### 3.2 本机接收入口

默认 `http://127.0.0.1:8766`，独立最小 FastAPI 应用，仅注册配对与绑定所需接口。此地址为设计值，本轮未启动；端口冲突则启动失败并明确报错，不自动改成 LAN/公网监听。

- 只绑定 `127.0.0.1`；不挂载原 ERP 应用，不监听 `0.0.0.0`；8765、LAN 入口和 Cloudflare ingress 均不代理这个服务。
- 本机首次设置向绑定扩展提供一次性配对码；配对码来自本机启动/设置过程，不由 8765 或远程页面返回。配对后固定允许的扩展 ID，生成专用随机 capability；扩展仅在受信任后台 storage 保存 capability，不保存 Cookie。
- 校验实际 loopback 连接、固定 Host、配对 capability、扩展 Origin/ID，以及与目标店铺绑定的短期单次 nonce。Origin/ID 不是唯一认证依据，不信任 `X-Forwarded-For` 等代理头。
- 限制请求大小、有效期、次数；拒绝重复 nonce、错误店铺、未配对扩展、网页来源、任意 URL/path 转发。凭证响应只返回状态，不回显请求内容。
- 预检/CORS 只服务已配对扩展，不对任意网页开放。HTTP 接收与异常日志不记录 Cookie、Authorization、配对码或请求 body。
- 浏览器 host permission 对 loopback 的范围仍由 API 能力决定；代码必须精确固定目的地址与 8766 端口，不能把 manifest 权限误称为端口级授权。

### 3.3 Windows 保存与失效

- Cookie 加密采用用户范围 Windows DPAPI，加密文件再验证 Windows ACL。SQLite 仅保存 opaque `session_ref` 和状态。API Key 原有 `.env` 格式不迁移。
- 加密文件置于开发环境专属 `runtime/private/ozon-seller/`，文件名使用内部随机引用，不包含 Cookie、账号名称或敏感值；写入原子替换，失败保留上次有效文件。
- ERP 和绑定服务在同一 Windows 用户下运行。更换用户/机器或不同服务账号导致不能解密时显示“需重新绑定”；不用 machine-wide DPAPI 或明文回退绕过。
- 解密只在只读客户端需要时进行；敏感对象 repr/异常固定脱敏。只接受受管引用，禁止任意路径读写。
- 本地解除绑定删除本机加密凭证并关闭该会话自动刷新，不删除已同步商品/分析快照，也不调用 Ozon 退出登录或其他写接口。
- Ozon 设置的新 Cookie 仅在已验证来源和同一店铺/会话下更新本机加密文件；401、登录重定向、MFA/验证码、身份不一致时停止，不自动登录或绕过验证。
- Cookie 本身可能具备后台写权限；本文限定的是 JLC 的调用能力，并不声称平台授予了只读 Cookie。加密不能防御已经控制同一 Windows 用户的恶意进程。

## 4. 店铺身份与跨店隔离

用户每次先切换目标店铺再绑定，但这不能单独证明 Cookie 已永久锁定该店铺。后台可能把“当前店铺”保存为账号级服务端状态，浏览器后续切换可能影响旧会话。

- 在独立接收服务中用核实的只读身份契约核对目标店铺稳定标识；绑定成功前验证，自动刷新每批开始及提交前再验证。
- Web store ID、内部 Store ID、Client-Id、Seller ID、Company 分字段和来源记录；字段相同也不推定语义相同。SellerInfo 无 Seller ID 时继续保持 null/“官方响应未提供”。
- 身份未核实、目标不匹配或该会话不能明确限定目标店铺时拒绝成功绑定/无人值守刷新。不得自动调用“切换店铺”操作规避，除非另行核实且批准其权限与副作用。
- 若一个账号共享会话不能稳定支持多个店铺，记录 NEEDS-FOLLOW-UP；可提出独立浏览器 profile 的方案供审核，但不能用猜测归属的数据交付。
- 远端数据只写入 `(organization_id, platform, store_id)` 匹配的本地快照。同一 SKU 在不同店铺可重复，不能跨店覆盖。

## 5. 数据读取与无人值守任务

官方 Seller API endpoint/schema 以用户提供的 `docs/ozon/swagger.json` 为准。当前 SHA-256：`B5BB73A0AEB28F66E9661A035A9EA0F0D68C638CA125419FF26A7A813277891F`。

本轮核对到 POST `/v1/seller/info`（`SellerAPI_SellerInfo`）、POST `/v3/product/info/list`（`ProductAPI_GetProductInfoList`）、POST `/v1/analytics/data`（`AnalyticsAPI_AnalyticsGetData`）。`revenue` 是订购金额，不是实收或利润；`ordered_units` 是订购商品数量。其他分析指标受 Premium/权限限制，非 Premium 日期范围在规范中受限。

- 商品目录继续由原官方 Product 读取链路负责，分析读取独立成 Job，互不阻断。
- 官方分析 API 优先，Cookie 后台数据补充。对同一指标/日期/维度选择一个明确来源，不叠加两个来源重复计数；历史来源保留可追溯信息。
- Seller 后台接口通常不在 Seller API Swagger 中。其 endpoint、method、认证 Cookie/额外 header、店铺限定方式、请求/响应字段及只读语义必须来自经单独批准的实际只读观察或脱敏协议证据。未找到时记 `NOT FOUND IN CURRENT SWAGGER` / `NOT VERIFIED`，不虚构 URL。
- HTTP method 不能决定是否只读：允许核实过的查询 POST；禁止通用请求代理。代码固定目标域、endpoint、method、请求字段和响应契约；禁用携带 Cookie 的跨域重定向及写操作。
- 后端使用已有 HTTP/任务机制，超时、有界重试和安全日志复用。单机一个分析调度器，持久化每店铺计划和下次到期时间；不引入 Celery、Redis 或分布式队列。
- 自动刷新能力提供本机启用开关，默认关闭；启用后默认每 30 分钟读取最近 7 天的支持指标。日期筛选读本地快照；主电脑可发起受来源窗口约束的历史区间补读，其他设备仅看已有数据。
- 同店铺/任务/日期范围活动 Job 去重，分页/批次安全边界持久化进度、响应取消。服务重启标记未完成任务 INTERRUPTED，恢复调度时重新核实身份；不把未完成批次标 SUCCEEDED。
- 网络/超时/429/5xx 最多三次请求尝试并遵循 Retry-After。401、明确无权限、登录挑战、身份不匹配不盲目重试；会话失效关闭 Cookie 刷新并提示重新绑定。403 必须区分已核实的权限拒绝与登录/风控，无法区分则暂停该路径。
- 页面保留上次成功快照及读取时间，失败展示过期/部分数据和原因。不会以 0 覆盖未知值，也不会混合不同完整度的快照伪装全量。
- 无人值守指 Edge 关闭且 ERP 进程运行期间继续刷新；本轮不承诺电脑关机、ERP 停止或切换 Windows 用户后继续运行，不新增 Windows Service 安装或部署。

## 6. 数据模型、迁移与 API 边界

所有新增 schema 用后续编号 migration，沿当前链升级，不重建现有数据库。离线迁移测试覆盖 V1.1A 及现有 schema 012 fixture，重复执行数据/ID 不丢失。

最小新增数据：

| 实体 | 作用 | 关键数据 |
|---|---|---|
| Store Browser Session | 本地凭证引用和身份验证状态 | organization/store/platform、session_ref、verified web store identity/source、状态、绑定/验证/过期时间 |
| Store Read Schedule | 每店铺无人值守配置 | enabled、interval、next_due、最近结果；不存 Cookie |
| Analytics Snapshot / Rows | 一批次读取证据及标准化指标 | 店铺、来源、日期区间、维度、metric/value/unit、availability、fetched_at、completeness、Job |
| Listing Platform SKU Identifiers | 保存顶层和 `sources[]` 的多个真实 Ozon SKU | Listing FK、external SKU、原 source/shipment 元数据及同步时间；不制造变体 |

分析结果区分 OBSERVED 原始来源和 CALCULATED 本地计算。不同指标可以不同可用性，未知是 null；真实已确认的零值是 0。精确金额计算采用现有可用方式或标准库 Decimal，不把不同币种合计成一个金额。快照仅留所需业务字段，不保存包含登录信息的原始全响应。

业务 API 继续放在 `backend/api/`，路由形状将在实施计划中固定：

- 现有 `/api/erp/stores` 和详情：增加不敏感的 session/analytics 状态、真实来源字段和分页筛选；保留旧 `items` 契约。
- 店铺分析 GET：按 store/date/tab/filter/page 读持久化快照，返回 availability、source、fetched_at、完整度。
- `/api/erp/products` 与 Product 详情：增量增加 Ozon SKU/来源、店铺筛选和全部 SKU/Listing 数据，保留既有字段。
- 本机配对/绑定/解除/计划与刷新控制：只在独立 8766 服务暴露；8765 无接收 Cookie 路由或任意远端 URL 请求路由。

## 7. 店铺中心与六个运营分析页

店铺中心从卡片改为可分页表格。显示店铺名称、平台店铺标识及来源、公司、API 连接/读取权限、Cookie 状态、最近商品同步、商品/Listing 数、币种、最近分析读取；提供名称/已核实店铺 ID、状态筛选。密钥到期、上新额度、仓库数量只有在已核实的只读数据源支持时展示，否则标明“不支持/未读取”，不推算。仓库数量仅是来源元数据，不建立库存中心。

选择店铺进入运营分析，含今天/昨天/周/月/季度/自定义区间；显示平台时区、来源和数据时间。分页、列表/图表数据均来自 ERP API，不使用生产 mock。

| 标签页 | 展示 | 必须遵守的计算/可用性规则 |
|---|---|---|
| 数据概览 | 订购金额、订购数量、支持的展示/访问/购物车/取消/退货，日期趋势 | 缺字段显示不可用；各币种、时间/来源口径分开 |
| 所有指标 | 每商品/SKU 支持指标表、搜索/排序/分页 | 只显示实际返回或明确计算值；分页汇总需完整性证据 |
| 销售漏斗 | 支持的展示→访问→加购→订购 | 分母/口径不一致不计算；零分母结果 null；不把不同数据来源拼成可比漏斗 |
| ABC 分析 | 按明确选定订购金额或数量做累计贡献分类 | 本地计算，A 累计 80%、B 至 95%、C 其余；确定性排序；数据不完整、跨币种或总量非正时不生成误导分类 |
| 销售领先产品 | 按订购数量或订购金额排名，关联商品/SKU | 展示排序口径和所选日期；不能称为市场销量排名 |
| 搜索位置 | 来源支持的商品查询/位置指标 | `position_category` 与真实关键词排名分开命名；缺数据不通过销量/曝光推算位置 |

六页同版提供入口、真实数据通道和可用性状态；当前协议支持的指标才可以标“可用”。仅有页面壳/不可用提示不能宣称已完成全部销售数据读取。

## 8. 商品中心

- 增加店铺选择、清晰的 SKU/货号搜索、商品状态过滤与分页；默认展示绑定店铺的本地已同步 Listing，可展开 Product/SKU 明细。
- 显示图片、商品标题、所属店铺、商家 SKU/货号（offer_id）、Ozon SKU、Ozon Product ID、价格/币种、Listing/出现/属性状态和最近同步。内部 Product/SKU/Listing ID 保留在详情并清楚标识。
- 使用 `/api/erp/products` 及现有详情 API。SKU 查询覆盖商家 SKU、顶层 `ozon_sku` 及核实的 `sources[].sku`；货号查 offer_id；不将市场 SKU 替换成商家货号。
- `sources[]` 的 FBO/FBS/SDS 是平台标识/销售模式，不自动当作三个颜色/尺寸变体。保留全部 distinct 平台 SKU 与来源，多个来源同 SKU 不虚增商品/SKU 数。
- 没有变体分组证据时按已有 Product 关系展示，不通过标题相似或相同货号猜测跨店 Product 合并。
- “全店同步完成”只在所有 Product List 页面成功且详情/SKU 完整度满足契约时显示；FAILED/PARTIAL 同步保留既有 reconciliation 限制和 last_seen 语义。
- 已批准的图片 URL 校验和迟到详情响应修复保留并回归；正常公开 Ozon 图片继续使用。无图片、加载失败和被拦截地址有清晰状态。

## 9. 验证与交付条件

按 RED → 最小实现 → focused tests → 受影响 V1.1A 回归 → review → checkpoint 执行。测试使用可写、无真实凭证隔离副本和临时数据库。

必须验证：

1. 扩展只在用户点击/官方页面触发；未配对、错误域/店铺、重放、分区上下文错误均拒绝；Cookie 不入 popup/content script/storage/log。
2. 独立服务仅监听 loopback；LAN/8765/代理不能提交会话；未持 capability 请求拒绝；同用户 DPAPI 重启可解密、不同用户/损坏/ACL 不合格均失败关闭，无明文回退。
3. 多店绑定、账号全局切店、跨组织/店铺、重复 SKU、会话过期和误匹配不会污染数据；取消/恢复/去重/重试符合契约。
4. 真实分页数据、来源/日期/币种、null 与 0、订阅拒绝、指标级 PARTIAL、ABC/漏斗/排名计算和时间边界正确。
5. 商品全 SKU、offer_id 和 Ozon SKU 查询、分页/详情、图片与迟到响应；店铺表格及六页 loading/empty/error/unsupported 浏览器验证。
6. 旧商品同步、属性、reconciliation、Job 中断/取消、API Key ACL、整批 `PREVIEW → CONFIRM → EXECUTE`、本地 Reply Draft 回归。所有自动测试网络都为 mock；明确统计商品/价格/库存/平台回复写调用为 0。
7. 全量 Python failures=0、errors=0，记录所有 skip 与 V1.1A/本次基线差异；Command Center typecheck/build、两个扩展 build、project_self_check PASS。全量结果不能用 focused 结果替代。
8. 最终源码 ZIP 排除 Cookie、配对 capability、API Key、密文凭证、真实商品/批次、数据库、日志、依赖/缓存；解包新目录复跑完整 gate，记录 SHA-256/文件数和证据。

真实只读验证须另行批准：仅核实自有目标店铺身份、所需后台查询契约、Cookie 关闭 Edge/ERP 重启后的可用性、跨店稳定性和支持的六页指标；不观察或执行上传、改价、库存、平台回复。批准前真实 Ozon read=0，标 NOT VERIFIED。

若 Cookie 后端重放因平台会话机制失败，或无法证明按店铺限定，Cookie 无人值守路径标 NEEDS-FOLLOW-UP。不得把 mock 通过、API-only 通过或“六页已显示”替代该用户目标。独立报告测试样例可用与真实店铺可用。

## 10. 执行与审核边界

本设计以两条相关工作线组织在同一 V1.1B 增补版：店铺会话/分析底座 → 店铺表格及六页；商品完整 SKU 映射 → 商品表格/查询。任务顺序和具体文件/route/migration 编号由书面 Implementation Plan 固定。

本轮只新增本设计稿，不改业务源码、主指令、旧 plan、CURRENT_TASK、Git 提交或真实 runtime。书面设计审核通过后再写实施计划；计划批准后才编码。真实读取与实际部署分别遵守既有授权范围。完成本次交付后停止，不自动进入 V2。

## 11. 资料与核验范围

- 本地 `docs/ozon/swagger.json`：官方 Seller API endpoint/schema source of truth；不代表官方 Seller 网页内部 API 文档。
- [Chrome Cookies API](https://developer.chrome.com/docs/extensions/reference/api/cookies)：Cookie/host 权限及 cookie-store/partition 数据。
- [Chrome 可选权限](https://developer.chrome.com/docs/extensions/reference/api/permissions)：运行时权限请求。
- [Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)：DPAPI 用户范围与 machine-wide 范围差异。
- Saleor 本地 snapshot `5ff56489737c78a9a5631d528f699303c953696a` 的 `saleor/product/models.py`：四个 Product/Variant/ChannelListing 模型，架构参考。
- [OCA Connector 官方组件说明](https://odoo-connector.com/api/api_components.html)：Adapter/Mapper/Binder/Synchronizer 职责；文档与本地快照不是同一实测证据。
- [OCA Queue 官方项目 README](https://github.com/OCA/queue/blob/18.0/queue_job/README.rst)：持久任务、去重、有界重试模式；无代码复制。

本轮没有启动服务、运行产品 gate、读取登录 Cookie、访问真实 Ozon 店铺或迁移真实数据库；这些不能标 PASS。
