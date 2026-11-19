# Windows 10/11 使用方法

本项目支持由 Codex 自动下载、初始化并启动，不要求用户手动配置开发环境。

## 直接交给 Codex

在 Windows 版 Codex 中发送：

> 请克隆这个仓库到我的 Documents 目录，然后在仓库根目录运行
> `powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1`。
> 安装缺失依赖、构建工作台和采集插件，并保持工作台运行。

把上面的“这个仓库”替换为实际 GitHub 仓库地址即可。

初始化脚本会通过 `winget` 检查或安装 Git、Python 3.12、Node.js LTS 和 Codex CLI，随后：

1. 创建 `.venv` 并安装 Python 依赖；
2. 从当前源码构建工作台界面，避免回退到旧采集页面；
3. 构建 Edge 采集插件；
4. 启动 `http://127.0.0.1:8765`。

首次安装系统依赖后，如果当前 Codex 终端尚未识别新命令，关闭并重新打开 Codex，再执行同一条命令即可。Ozon 店铺密钥属于本机配置，不包含在 GitHub 源码中。

## 整批上传操作

```text
运行批次 → 等待全部商品生成与质检 → 审阅整批上传 →
若阻断则返回工作台修改 → 重新生成 → 确认整批上传 → 查看多店提交结果
```

整批审阅页面只读，只展示商品汇总和风险。任何一件商品失败、未完成或未经审阅都会禁用确认按钮；请返回工作台修改对应商品，重新生成后再次审阅。一次确认只授权当前整批快照，确认后修改文案、类目、规格、价格、店铺或图片都会自动使授权失效。

真实提交前建议在项目目录运行：

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe scripts\project_self_check.py
```

输出必须包含 `decision: PASS`、`ozon_write_api_calls: 0` 和 `inventory_api_calls: 0`。`--full-tests` 是诊断旧版 Windows 兼容问题的可选检查；当前已知基线仍会失败，不能替代上述上传安全自检。店铺凭证只能填写在本机配置中；不要把凭证、运行日志、PID、缓存、`.venv` 或 `node_modules` 放入交付包。详细规则见 [docs/BATCH_UPLOAD_APPROVAL.md](docs/BATCH_UPLOAD_APPROVAL.md)。
