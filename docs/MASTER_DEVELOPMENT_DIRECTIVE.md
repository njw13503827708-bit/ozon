# JLC Cross-border AI ERP
# MASTER DEVELOPMENT DIRECTIVE V1.0

你现在接手的是一个已经存在并正在运行的跨境电商 ERP 项目。

这不是一个 Demo，不是一次性脚本，也不再只是一个 Ozon 自动上品工具。

从现在开始，本项目的正式产品定位为：

JLC Cross-border AI ERP

最终目标是建设一个可以商业销售的跨境电商 SaaS ERP。

==================================================
一、最终产品目标
==================================================

最终产品必须支持：

1. Web SaaS
2. Windows 客户端
3. 多用户
4. 多企业 / 多租户
5. 多 Ozon 店铺
6. 商品中心
7. 库存中心
8. 价格中心
9. 订单中心
10. 财务与利润
11. AI 文案
12. AI 图片
13. AI 类目与属性
14. 自动上品
15. 自动化任务中心
16. 月卡
17. 季卡
18. 年卡
19. 永久授权
20. 管理后台
21. 后续 Kaspi
22. 后续 Wildberries
23. 后续其他跨境平台

当前阶段只开发 Ozon。

禁止现在同时开发 Kaspi 和 Wildberries。

==================================================
二、现有能力必须保护
==================================================

当前 V1 已经能够：

- 保存 Store / Product / SKU / Listing
- 从 Ozon 同步商品
- 成功读取约 594 个商品
- 分页同步
- Retry
- 异步 Job
- 商品详情读取
- Attribute读取
- API Key基础脱敏
- 原自动上品 Pipeline
- 图片 QC
- 人工确认安全门

后续所有开发不得破坏这些已验证能力。

任何重构都必须保持 backward compatibility，
除非有经过批准的 Migration。

==================================================
三、开发阶段
==================================================

项目严格按照：

V1
↓
V1.1 Stabilization
↓
V2 Ozon Operations Data
↓
V3 ERP Business Center + SaaS
↓
V4 AI + Automation + Commercial Release
↓
V5 Multi Marketplace

开发。

当前版本未经审核通过：

禁止提前进入下一版本。

==================================================
四、Superpowers 强制工作流
==================================================

任何重要功能必须使用：

superpowers:using-superpowers

遇到 Bug：

superpowers:systematic-debugging

设计新功能：

superpowers:brainstorming

复杂阶段：

superpowers:writing-plans

实施：

superpowers:test-driven-development

适合拆分时：

superpowers:subagent-driven-development

或：

superpowers:executing-plans

开发完成：

superpowers:requesting-code-review

最终宣布完成前：

superpowers:verification-before-completion

合并阶段：

superpowers:finishing-a-development-branch

重要阶段建议使用：

superpowers:using-git-worktrees

每个大阶段尽量在独立 branch/worktree 中开发。

==================================================
五、严禁 Guess-and-Fix
==================================================

遇到问题必须：

复现
↓
查看错误
↓
增加诊断
↓
找到失败层
↓
形成单一根因假设
↓
写测试
↓
修复
↓
验证

禁止：

“可能是这里”
→ 连改五个文件
→ 看看是否能跑。

==================================================
六、架构方向
==================================================

目标架构：

Web / Windows Client
        │
        ▼
Cloud API
        │
        ▼
FastAPI
        │
 ┌──────┼───────────┐
 ▼      ▼           ▼
Postgres Worker   Object Storage
        │
        ▼
Marketplace Integrations
        │
        ▼
Ozon

前端继续：

React + Vite

除非存在明确理由，
不要无意义更换前端框架。

后端继续：

Python + FastAPI。

禁止为了部署到 Cloudflare
重写成熟 Python 业务后端。

==================================================
七、后端架构
==================================================

所有新增业务逐步迁移到：

backend/
  api/
  services/
  repositories/
  integrations/
  db/

Ozon：

backend/integrations/ozon/

建议拆分：

base_client.py
account_client.py
product_client.py
inventory_client.py
price_client.py
warehouse_client.py
order_client.py
finance_client.py

禁止继续把新业务逻辑堆进：

app.py

旧代码采用渐进迁移。

禁止 Big Bang Rewrite。

==================================================
八、业务数据模型
==================================================

必须严格区分：

Product
SKU
Marketplace Listing

不能把它们当作同一个对象。

长期数据库至少包含：

organizations
users
organization_memberships

stores

products
skus
marketplace_listings

warehouses
inventory_records
price_records

orders
order_items

financial_transactions

image_assets
image_versions

sync_jobs
sync_job_items
sync_job_logs

audit_logs

plans
subscriptions
entitlements
usage_counters

==================================================
九、多租户设计
==================================================

从现在开始所有新的商业数据模型必须考虑：

organization_id

最终结构：

Organization
    ↓
Users
    ↓
Stores
    ↓
ERP Data

角色：

OWNER
ADMIN
OPERATOR
VIEWER

未来绝对禁止：

Organization A

通过修改 URL 或 API 参数读取：

Organization B

的数据。

==================================================
十、数据库
==================================================

本地开发可以继续：

SQLite。

生产 SaaS：

PostgreSQL。

业务层不得直接绑死某数据库。

Repository 层负责隔离。

推荐：

SQLAlchemy
Alembic
PostgreSQL

如果 Supabase 可用：

可以用于：

Postgres
Auth
RLS
Migration辅助

但 Supabase 不得成为业务代码无法替换的硬依赖。

==================================================
十一、认证
==================================================

V3 开始实现：

注册
登录
登出
密码重置
Email Verification
Session
Refresh Token
RBAC

可以使用：

Supabase Auth

或者其他可靠 Auth Provider。

==================================================
十二、Ozon Secret 安全
==================================================

API Key / Secret：

绝不能出现在：

Frontend
localStorage
API Response
Logs
Exceptions
Exports
Git
明文数据库

本机 Windows：

未来使用：

DPAPI
或
Windows Credential Manager。

云端：

使用 encrypted secret storage。

数据库只保存：

credential_ref。

==================================================
十三、远程写操作安全门
==================================================

任何危险 Marketplace 操作：

Publish
Update Price
Update Stock
Fulfillment
Archive/Delete

必须：

PREVIEW
↓
CONFIRM
↓
EXECUTE

禁止后台 AI 自动直接修改远程商品。

==================================================
十四、任务系统
==================================================

长期任务类型：

OZON_PRODUCT_SYNC
OZON_INVENTORY_SYNC
OZON_PRICE_SYNC
OZON_ORDER_SYNC
OZON_FINANCE_SYNC
OZON_WAREHOUSE_SYNC

AI_COPY
AI_IMAGE
IMAGE_PROCESSING
PRODUCT_PUBLISH

Job 状态：

QUEUED
RUNNING
WAITING_USER
RETRYING
PARTIAL
SUCCEEDED
FAILED
INTERRUPTED
CANCELLED

任何批量任务：

一个 SKU 失败不能让整个批次丢失。

==================================================
十五、AI 架构
==================================================

AI 功能不得永久绑定：

codex exec。

建立：

AIProvider

例如：

TextAIProvider
ImageAIProvider

未来可以替换 Provider。

AI 生成商品修改必须：

Original
↓
Suggestion
↓
Diff
↓
Accept / Reject

禁止直接覆盖。

==================================================
十六、图片架构
==================================================

所有图片至少分成：

ORIGINAL
PROCESSED
MARKETPLACE

Original 永远不可被 AI 覆盖。

长期存储：

Cloudflare R2
或兼容 Object Storage。

本机文件仅作为开发 Adapter。

建立：

FileStorage

LocalFileStorage
R2FileStorage

==================================================
十七、搜索/关键词数据规则
==================================================

外部关键词数据不是 ERP 使用前提。

商品优化分三级：

LEVEL 1 — BASELINE

使用现有：

Ozon title
description
category
attributes
images
price
SKU

无需外部数据即可运行 AI 基础优化。

LEVEL 2 — OZON ENHANCED

如果存在：

search
impression
click
conversion
order

等 Ozon 数据：

增强建议。

LEVEL 3 — EXTERNAL RESEARCH

Yandex
Seerfar
第三方趋势数据

全部 OPTIONAL。

禁止因为没有第三方数据：
阻止商品优化。

==================================================
十八、前端产品语言
==================================================

普通运营人员不应该看到大量：

JSON
Internal Pipeline Name
Internal Task ID
Stack Trace
技术文件路径

这些进入：

Advanced
Developer Details
Logs

业务界面使用：

商品
库存
价格
订单
利润
店铺
任务
AI

等业务语言。

==================================================
十九、SaaS 部署
==================================================

最终必须支持：

其他电脑打开浏览器：

https://app.domain.com

登录后使用同一套 ERP。

建议：

Frontend:
Cloudflare edge/static deployment

Backend:
FastAPI Docker

Database:
PostgreSQL

Object Storage:
Cloudflare R2

CDN/DNS/WAF:
Cloudflare

禁止正式生产环境依赖：

127.0.0.1

或个人电脑 Cloudflare Tunnel。

Tunnel 只用于：

development
staging
temporary access。

==================================================
二十、Windows 客户端
==================================================

Web SaaS 完成后：

使用 Tauri

包装现有 Web Frontend。

不要重新开发一套 Windows UI。

Windows Client 登录同一 SaaS Backend。

最终支持：

Installer
Code Signing
Auto Update
Version Check。

==================================================
二十一、收费系统
==================================================

最终支持：

MONTHLY
QUARTERLY
YEARLY
LIFETIME

但业务代码禁止到处：

if plan == "PRO"

使用：

Entitlement Service。

例如权益：

max_stores
max_users
max_skus

ai_text_credits
ai_image_credits

storage_limit

automation_enabled
analytics_enabled

marketplace_count

==================================================
二十二、永久买断
==================================================

LIFETIME 不代表：

永久免费 AI
永久免费服务器
永久无限存储。

建议：

永久 ERP 功能授权

但是：

AI
云存储
高成本 API

仍可采用：

Credit
Quota
Cloud Service Renewal

未来还可以提供：

Desktop / Self-hosted Lifetime License。

==================================================
二十三、支付
==================================================

建立：

BillingProvider

不要把整个产品永久绑死一个支付平台。

未来可实现：

StripeBillingProvider
PaddleBillingProvider
其他 Provider

支付 Webhook：

必须：

signature verification
idempotency
audit logs

==================================================
二十四、邮件
==================================================

EmailProvider abstraction。

可以使用：

Resend。

用途：

注册
验证码
密码重置
付款成功
付款失败
到期提醒
续费提醒。

==================================================
二十五、Cloudflare
==================================================

可以使用 Cloudflare：

DNS
HTTPS
CDN
WAF
Turnstile
R2

以及合适的 edge services。

不要因为使用 Cloudflare：

重写现有成熟 FastAPI Backend。

==================================================
二十六、Product Design / Figma
==================================================

V3/V4 UI 大改之前：

使用：

Product Design
Figma

先：

Audit
User Flow
Information Architecture
Design System
Prototype

再实现 React。

禁止：

开发完一堆页面再发现交互不合理。

==================================================
二十七、Testing
==================================================

任何新业务功能必须有测试。

必须覆盖：

Unit Tests
Repository Tests
API Tests
Marketplace Mock Tests

重要用户路径：

E2E Tests。

Ozon 自动测试默认：

Mock Transport。

禁止测试代码无确认修改真实 Ozon 数据。

==================================================
二十八、CI/CD
==================================================

长期目标：

PR 时自动：

Backend Tests
Frontend Typecheck
Frontend Build
Migration Tests
Security Tests

Staging 自动部署。

Production：

人工批准。

==================================================
二十九、Definition of Done
==================================================

任何 Task/Phase 只有满足以下条件才算完成：

1. 前后端真实接通。
2. Production 不使用 Mock Data。
3. 有自动测试。
4. 所有测试实际运行。
5. Build 实际通过。
6. Secret 不泄漏。
7. Migration 正确。
8. 旧功能没有回归。
9. 用户可以实际操作。
10. Placeholder 不算完成。
11. TODO 不算完成。
12. 未完成标记 NOT IMPLEMENTED。
13. 未真实验证标记 NOT VERIFIED。
14. 输出修改文件。
15. 输出数据库 Migration。
16. 输出 API 变化。
17. 输出 UI 变化。
18. 输出测试结果。
19. 输出 Known Issues。
20. 阶段结束必须停止等待审核。

==================================================
三十、阶段禁止越权
==================================================

每一个 Phase 完成以后：

STOP。

禁止：

“顺便开始下一阶段”。

只有用户明确批准：

才能进入下一 Phase。

==================================================
三十一、当前执行状态
==================================================

当前项目位于：

V1 完成
准备进入：

V1.1 Stabilization

所以现在：

只允许执行 V1.1。

禁止开始：

V2
V3
V4。

请把本文件保存为：

docs/MASTER_DEVELOPMENT_DIRECTIVE.md

以后所有 Plan 和实现必须引用此文档。
