#!/usr/bin/env python3
"""Offline whole-project safety and consistency check.

This command never calls Ozon or any other network service. It is safe to run
before starting the workbench, before a batch, and after code changes.
"""

from __future__ import annotations

import argparse
import json
import os
try:
    from scripts.process_probe import probe_process
except ModuleNotFoundError:
    from process_probe import probe_process
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from scripts.batch_upload_approval import _review_allows_authorized_runtime_replay, authorization_path, build_batch_upload_review
except ModuleNotFoundError:
    from batch_upload_approval import _review_allows_authorized_runtime_replay, authorization_path, build_batch_upload_review


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "logs/project-self-check.json"
SKIP_PARTS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv",
    "dist", "build", "target", "release", "logs", "cache", "runtime", "prototypes",
}
SOURCE_SUFFIXES = {
    ".py", ".js", ".ts", ".json", ".md", ".html", ".css", ".sh", ".command",
    ".yaml", ".yml", ".toml", ".ini", ".cfg",
}
REQUIRED_FILES = (
    "README.md", "AGENTS.md", "PROJECT_HANDOFF.md", "CURRENT_TASK.md",
    "WORKBENCH_REQUIREMENTS_MATRIX.md", ".gitignore", "requirements.txt",
    "collector/local-ingest/app.py", "collector/local-ingest/static/workbench.html",
    "collector/local-ingest/static/workbench.js", "collector/edge-extension/manifest.json",
    "collector/edge-extension/content.js", "scripts/run_batch.py",
    "scripts/multi_store_upload.py", "scripts/validate_product.py",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def source_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        if path.name.startswith(".env") or relative.parts[:1] in {("products",), ("batches",)}:
            continue
        yield path


def add(results: List[Dict[str, Any]], code: str, ok: bool, message: str, severity: str = "error", **details: Any) -> None:
    results.append({"code": code, "ok": bool(ok), "severity": severity, "message": message, **details})


def check_required_files(results: List[Dict[str, Any]]) -> None:
    missing = [name for name in REQUIRED_FILES if not (ROOT / name).is_file()]
    add(results, "required_files", not missing, "关键项目文件齐全" if not missing else "缺少关键项目文件", missing=missing)


def check_source_syntax(results: List[Dict[str, Any]]) -> None:
    python_errors: List[str] = []
    json_errors: List[str] = []
    for path in source_files(ROOT):
        relative = str(path.relative_to(ROOT))
        if path.suffix == ".py":
            try:
                compile(path.read_text(encoding="utf-8"), relative, "exec")
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                python_errors.append(f"{relative}: {type(exc).__name__}")
        elif path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                json_errors.append(f"{relative}: {type(exc).__name__}")
    add(results, "python_syntax", not python_errors, "Python源码语法正常" if not python_errors else "Python源码存在语法错误", files=python_errors)
    add(results, "json_syntax", not json_errors, "源码和规则JSON均可解析" if not json_errors else "源码或规则JSON损坏", files=json_errors)


def check_javascript(results: List[Dict[str, Any]]) -> None:
    node = next((path for path in ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node") if Path(path).is_file()), None)
    files = (
        "collector/edge-extension/content.js", "collector/edge-extension/popup.js",
        "collector/edge-extension/page-probe.js", "collector/local-ingest/static/workbench.js",
    )
    if not node:
        add(results, "javascript_syntax", True, "未找到Node，已跳过JavaScript语法检查", severity="warning")
        return
    failures = []
    for name in files:
        completed = subprocess.run([node, "--check", str(ROOT / name)], capture_output=True, text=True, check=False)
        if completed.returncode:
            failures.append(name)
    add(results, "javascript_syntax", not failures, "工作台和插件JavaScript语法正常" if not failures else "JavaScript语法错误", files=failures)


def check_secrets_and_forbidden_clients(results: List[Dict[str, Any]]) -> None:
    credential_re = re.compile(
        r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*[\"'](?!unknown|example|placeholder|change-me)([A-Za-z0-9_./+=-]{16,})[\"']"
    )
    secret_files: List[str] = []
    forbidden_ai: List[str] = []
    inventory_endpoints: List[str] = []
    inventory_re = re.compile(r"/[vV]\d+/(?:product/)?(?:stocks?|inventory)(?:/|[\"'])")
    for path in source_files(ROOT):
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if credential_re.search(text):
            secret_files.append(relative)
        if (
            path.suffix.lower() in {".py", ".js", ".ts", ".sh", ".command"}
            and relative != "scripts/project_self_check.py"
            and not relative.startswith("tests/")
        ):
            ai_endpoint = "api." + "openai" + ".com"
            ai_key_name = "OPENAI" + "_API_KEY"
            # User authorized only Images 2.0 and its worker key filtering.
            allowed_endpoint = relative == "scripts/openai_images2.py"
            allowed_key = relative in {"scripts/openai_images2.py", "scripts/run_batch.py"}
            if ((ai_endpoint.lower() in text.lower() and not allowed_endpoint)
                    or (ai_key_name in text and not allowed_key)
                    or re.search(r"(?i)(?:from|import)\s+openai", text)):
                forbidden_ai.append(relative)
        if inventory_re.search(text):
            inventory_endpoints.append(relative)
    add(results, "hardcoded_credentials", not secret_files, "未发现源码硬编码凭证" if not secret_files else "疑似硬编码凭证，内容未显示", files=sorted(set(secret_files)))
    add(results, "external_ai_clients", not forbidden_ai, "仅允许已授权 Images 2.0 接入" if not forbidden_ai else "发现未授权的外部AI API客户端", files=sorted(set(forbidden_ai)))
    add(results, "inventory_endpoints", not inventory_endpoints, "未发现库存接口调用" if not inventory_endpoints else "发现库存接口路径", files=sorted(set(inventory_endpoints)))


def check_edge_extension(results: List[Dict[str, Any]]) -> None:
    extension = ROOT / "collector/edge-extension"
    manifest = load_json(extension / "manifest.json")
    package = load_json(extension / "package.json")
    content = (extension / "content.js").read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)', content)
    versions = {"manifest": str(manifest.get("version") or ""), "package": str(package.get("version") or ""), "content": match.group(1) if match else ""}
    add(results, "extension_version", len(set(versions.values())) == 1 and all(versions.values()), "插件版本号一致" if len(set(versions.values())) == 1 else "插件版本号不一致", versions=versions)
    mirrors = {}
    for name in ("content", "popup"):
        runtime = extension / f"{name}.js"
        source = extension / "src" / f"{name}.ts"
        mirrors[name] = runtime.is_file() and runtime.stat().st_size > 0 and runtime.stat().st_mtime >= source.stat().st_mtime
    add(results, "extension_source_mirror", all(mirrors.values()), "插件运行文件已由当前源码构建" if all(mirrors.values()) else "插件运行文件早于源码，请重新构建", mirrors=mirrors)
    tree = load_json(extension / "category-tree.zh-CN.json")
    rules = load_json(extension / "category-rules-cache.json")
    tree_count = int(tree.get("item_count") or 0)
    search_count = len(tree.get("search_items") or [])
    rule_count = len(rules.get("rules_by_key") or {})
    cache_ok = (
        tree.get("locale") == "zh-CN"
        and tree.get("source") == "ozon_seller_api"
        and tree.get("api_language") == "ZH_HANS"
        and tree.get("official_labels_required") is True
        and tree_count > 0
        and search_count == tree_count
        and rule_count >= tree_count
    )
    add(
        results,
        "extension_category_cache",
        cache_ok,
        "插件Ozon官方中文类目树和规则缓存完整" if cache_ok else "插件官方中文类目缓存不完整或仍在使用本地翻译",
        tree_count=tree_count,
        search_count=search_count,
        rule_count=rule_count,
        source=tree.get("source"),
        api_language=tree.get("api_language"),
    )


def pid_alive(value: Any) -> bool:
    try:
        probe_process(int(value))
        return True
    except (OSError, TypeError, ValueError):
        return False


def product_contract_class(product_dir: Path) -> str:
    """Classify persisted product data without rewriting historical records."""
    status = load_json(product_dir / "status.json")
    state = str(status.get("status") or "").upper()
    if state in {"ARCHIVED", "ABANDONED"} or status.get("archived_at"):
        return "archived"
    source = load_json(product_dir / "input/source.json")
    if (
        source.get("source_kind") == "workbench_collection"
        and source.get("product_id") == product_dir.name
        and source.get("collection_id")
    ):
        return "current"
    return "pre_contract"


def check_runtime(results: List[Dict[str, Any]]) -> None:
    product_errors: List[str] = []
    products = 0
    uploaded = 0
    handed_off = 0
    archived = 0
    pre_contract: List[str] = []
    for product_dir in sorted((ROOT / "products").glob("P[0-9][0-9][0-9][0-9][0-9][0-9]")):
        products += 1
        contract_class = product_contract_class(product_dir)
        if contract_class == "archived":
            archived += 1
            continue
        if contract_class == "pre_contract":
            pre_contract.append(product_dir.name)
        status = load_json(product_dir / "status.json")
        state = str(status.get("status") or "unknown")
        if state == "HANDED_OFF_TO_OZON":
            handed_off += 1
            ozon = status.get("ozon") or {}
            if str(ozon.get("task_id") or "unknown") == "unknown":
                product_errors.append(f"{product_dir.name}: 已交接Ozon但缺少task_id")
        if state == "UPLOADED":
            uploaded += 1
            ozon = status.get("ozon") or {}
            if any(str(ozon.get(key) or "unknown") == "unknown" for key in ("offer_id", "task_id", "product_id")):
                product_errors.append(f"{product_dir.name}: 已上传但Ozon标识不完整")
        if state == "PENDING_REMOTE" and str((status.get("ozon") or {}).get("task_id") or "unknown") == "unknown":
            product_errors.append(f"{product_dir.name}: 状态待回查但缺少task_id")
        if int(status.get("api_write_count") or 0) > 0 and state not in {"PENDING_REMOTE", "OZON_MODERATION", "HANDED_OFF_TO_OZON", "UPLOADED", "ACTIVE", "NEEDS_ATTENTION"}:
            product_errors.append(f"{product_dir.name}: 已发生写请求但状态不明确")
    stale_workers = []
    worker_dir = ROOT / "logs/product-workers"
    for path in worker_dir.glob("*.json") if worker_dir.is_dir() else []:
        worker = load_json(path)
        if not pid_alive(worker.get("pid")):
            stale_workers.append(path.name)
    active_batches = []
    for batch_dir in (ROOT / "batches").glob("B-*") if (ROOT / "batches").is_dir() else []:
        batch = load_json(batch_dir / "batch.json")
        if str(batch.get("status") or "").upper() in {"RUNNING", "QUEUED", "PROCESSING"}:
            active_batches.append(batch_dir.name)
    add(
        results, "runtime_products", not product_errors,
        "活跃商品状态与Ozon异步标识一致" if not product_errors else "活跃商品运行状态存在矛盾",
        products=products, uploaded=uploaded, handed_off=handed_off, archived=archived,
        pre_contract=pre_contract, issues=product_errors,
    )
    add(
        results, "pre_contract_products", not pre_contract,
        "没有旧目录契约商品" if not pre_contract else "存在旧目录契约商品；已禁止进入新批次，等待单独迁移或归档",
        severity="warning", products=pre_contract,
    )
    add(results, "runtime_workers", not stale_workers, "没有失效的商品任务进程记录" if not stale_workers else "存在失效的任务进程记录", files=stale_workers)
    add(results, "runtime_batches", not active_batches, "当前没有遗留运行批次" if not active_batches else "存在运行中的批次", active_batches=active_batches, severity="warning")
    queues = {}
    for name in ("remote-pending-queue.json", "image-channel-queue.json"):
        queue = load_json(ROOT / name)
        queues[name] = len(queue.get("items") or [])
    add(results, "runtime_queues", True, "本地异步队列已读取", severity="info", queues=queues)


def _batch_reviews() -> Iterable[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]]:
    for batch_dir in sorted((ROOT / "batches").glob("B-*")) if (ROOT / "batches").is_dir() else []:
        batch = load_json(batch_dir / "batch.json")
        if not batch:
            continue
        try:
            review = build_batch_upload_review(ROOT, batch)
        except Exception:
            review = {"can_confirm": False, "review_hash": "", "risks": [{"code": "review_build_failed"}]}
        yield batch_dir.name, batch, review, load_json(authorization_path(ROOT, batch_dir.name))


def _authorization_matches(batch_id: str, batch: dict[str, Any], review: dict[str, Any], record: dict[str, Any]) -> bool:
    product_ids = sorted(str(item.get("product_id") or "") for item in batch.get("products") or [])
    return (
        record.get("status") == "authorized"
        and record.get("batch_id") == batch_id == batch.get("batch_id")
        and sorted(record.get("product_ids") or []) == product_ids
        and record.get("target_store_ids") == review.get("target_store_ids")
        and record.get("review_hash") == review.get("review_hash")
        and _review_allows_authorized_runtime_replay(ROOT, batch_id, review, allow_upload_retry=True)
    )


def check_batch_upload_gate(results: List[Dict[str, Any]]) -> None:
    waiting = blocked = authorized = stale = 0
    preapproval_writes: set[str] = set()
    blocker_codes: set[str] = set()
    for batch_id, batch, review, record in _batch_reviews():
        valid_authorization = _authorization_matches(batch_id, batch, review, record)
        if valid_authorization:
            authorized += 1
        elif record.get("status") == "authorized":
            stale += 1
        elif review.get("can_confirm"):
            waiting += 1
        else:
            blocked += 1
        if not valid_authorization:
            blocker_codes.update(
                str(risk.get("code")) for risk in review.get("risks") or []
                if risk.get("level", "blocker") == "blocker"
            )
        if not valid_authorization:
            for item in batch.get("products") or []:
                product_id = str(item.get("product_id") or "")
                status = load_json(ROOT / "products" / product_id / "status.json")
                if int(status.get("api_write_count") or 0) > 0:
                    preapproval_writes.add(product_id)
    ok = not preapproval_writes and not blocker_codes
    add(
        results, "batch_upload_gate", ok,
        "整批上传门禁正常" if ok else "整批上传门禁发现阻断项",
        waiting_batches=waiting, blocked_batches=blocked, authorized_batches=authorized,
        stale_authorized_batches=stale,
        preapproval_write_products=sorted(preapproval_writes), blocker_codes=sorted(blocker_codes),
    )


def check_batch_authorization_integrity(results: List[Dict[str, Any]]) -> None:
    missing: list[str] = []
    stale: list[str] = []
    for batch_id, batch, review, record in _batch_reviews():
        claims_authorized = str(batch.get("status") or "").upper() == "UPLOAD_AUTHORIZED"
        if claims_authorized and not record:
            missing.append(batch_id)
        elif (claims_authorized or record.get("status") == "authorized") and not _authorization_matches(batch_id, batch, review, record):
            stale.append(batch_id)
    ok = not missing and not stale
    add(
        results, "batch_authorization_integrity", ok,
        "批次授权记录与当前审阅快照一致" if ok else "批次授权记录缺失或已过期",
        missing_authorization_batches=missing, stale_authorized_batches=stale,
    )


def validate_products(results: List[Dict[str, Any]]) -> None:
    failures = []
    count = 0
    archived = []
    handed_off = []
    pre_contract = []
    for product_dir in sorted((ROOT / "products").glob("P[0-9][0-9][0-9][0-9][0-9][0-9]")):
        contract_class = product_contract_class(product_dir)
        if contract_class == "archived":
            archived.append(product_dir.name)
            continue
        if contract_class == "pre_contract":
            pre_contract.append(product_dir.name)
            continue
        status = load_json(product_dir / "status.json")
        if str(status.get("status") or "unknown") == "HANDED_OFF_TO_OZON":
            handed_off.append(product_dir.name)
            continue
        count += 1
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_product.py"), str(product_dir)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if completed.returncode:
            failures.append(product_dir.name)
    add(
        results, "product_validation", not failures,
        "当前契约商品全部通过结构校验" if not failures else "当前契约商品结构校验失败",
        checked=count, products=failures, archived=archived, handed_off=handed_off, pre_contract=pre_contract,
    )


def run_full_tests(results: List[Dict[str, Any]]) -> None:
    python_bin = ROOT / ".venv/bin/python"
    executable = str(python_bin) if python_bin.is_file() else sys.executable
    completed = subprocess.run(
        [executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"Ran\s+(\d+)\s+tests", output)
    skipped = re.search(r"skipped=(\d+)", output)
    add(
        results, "full_tests", completed.returncode == 0,
        "完整自动测试通过" if completed.returncode == 0 else "完整自动测试失败",
        tests=int(match.group(1)) if match else None,
        skipped=int(skipped.group(1)) if skipped else 0,
        python=executable,
        output_tail=output[-4000:] if completed.returncode else "",
    )


def write_report(results: List[Dict[str, Any]], full_tests: bool) -> Dict[str, Any]:
    failures = [item for item in results if not item["ok"] and item["severity"] == "error"]
    warnings = [item for item in results if not item["ok"] and item["severity"] == "warning"]
    report = {
        "schema_version": "1.0.0", "checked_at": now_iso(), "root": str(ROOT),
        "network_calls": 0, "ozon_write_api_calls": 0, "inventory_api_calls": 0,
        "full_tests_requested": full_tests,
        "decision": "PASS" if not failures else "FAIL",
        "summary": {"checks": len(results), "failures": len(failures), "warnings": len(warnings)},
        "checks": results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(REPORT_PATH)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="离线检查整个 crossborder-ai-factory 项目")
    parser.add_argument("--full-tests", action="store_true", help="同时运行完整自动测试")
    args = parser.parse_args()
    results: List[Dict[str, Any]] = []
    check_required_files(results)
    check_source_syntax(results)
    check_javascript(results)
    check_secrets_and_forbidden_clients(results)
    check_edge_extension(results)
    check_runtime(results)
    check_batch_upload_gate(results)
    check_batch_authorization_integrity(results)
    validate_products(results)
    if args.full_tests:
        run_full_tests(results)
    report = write_report(results, args.full_tests)
    print(json.dumps({
        "decision": report["decision"], **report["summary"],
        "report": str(REPORT_PATH), "ozon_write_api_calls": 0, "inventory_api_calls": 0,
    }, ensure_ascii=False))
    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
