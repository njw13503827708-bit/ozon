"""Scrapling preview/import uses the existing device gate and collector ingest.

Executed in app.py globals like the other extracted route files.
"""
import uuid
from scripts.scrapling_collector import capture_public, parse_1688, parse_ozon_list, validate_url


@app.post("/api/collector/scrapling/preview")
def preview_scrapling(payload: Dict[str, Any]) -> Dict[str, Any]:
    require_owner_role()
    if set(payload) - {"url", "html", "render"} or not isinstance(payload.get("render", False), bool):
        raise HTTPException(status_code=422, detail="采集只接受url及可选的本人保存HTML")
    try:
        url = validate_url(payload.get("url"))
        if "html" in payload:
            parse = parse_1688 if urllib.parse.urlsplit(url).hostname == "detail.1688.com" else parse_ozon_list
            result = parse(payload["html"], url, now_iso())
            result["capture_method"] = "user_supplied_html"
        else:
            result = capture_public(url, render=payload.get("render", False))
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="采集依赖不可用；请在运行ERP的同一解释器安装 requirements-collector.txt，未保存采集快照。") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result["state"] == "unavailable":
        raise HTTPException(status_code=503, detail="采集依赖不可用；请在运行ERP的同一解释器安装 requirements-collector.txt，未保存采集快照。")
    if result["state"] in {"ready", "incomplete"}:
        if result.get("kind") == "1688":
            result["capture"]["raw_snapshot"]["capture_method"] = result["capture_method"]
        capture_id = uuid.uuid4().hex
        result["capture_id"] = capture_id
        atomic_write_json(ROOT / "runtime/collector-captures" / f"{capture_id}.json", {
            "owner_id": current_operator_id(), "result": result, "imported": None,
        })
    return result


@app.post("/api/collector/scrapling/import")
def import_scrapling(payload: Dict[str, Any]) -> Dict[str, Any]:
    require_owner_role()
    if set(payload) - {"capture_id", "selected_sku_ids", "category_id", "type_id", "allow_new_version"}:
        raise HTTPException(status_code=422, detail="不能替换服务端已保存的原始采集事实")
    capture_id = str(payload.get("capture_id") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", capture_id):
        raise HTTPException(status_code=422, detail="采集编号无效")
    path = ROOT / "runtime/collector-captures" / f"{capture_id}.json"
    # ponytail: serialize local imports, per-capture locks if simultaneous imports matter.
    with BATCH_QUEUE_LOCK:
        saved = load_optional_json(path)
        if not saved:
            raise HTTPException(status_code=404, detail="采集预览不存在，请重新采集")
        if saved.get("owner_id") != current_operator_id():
            raise HTTPException(status_code=403, detail="不能导入其他操作员的采集快照")
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if saved.get("imported"):
            if saved.get("import_request_hash") != request_hash:
                raise HTTPException(status_code=409, detail="此快照已导入，不能用新选择覆盖原采集资料")
            return saved["imported"]
        result = saved["result"]
        if result.get("kind") != "1688" or result.get("state") != "ready":
            raise HTTPException(status_code=422, detail="只有读到真实SKU的1688快照能进入采集箱；Ozon网页观察不能当正式销量报表导入")
        selected = payload.get("selected_sku_ids")
        if not isinstance(selected, list) or not 1 <= len(selected) <= 10 or any(not isinstance(s, str) for s in selected) or len(set(selected)) != len(selected):
            raise HTTPException(status_code=422, detail="请选择1至10个不重复的真实SKU")
        capture = copy_module.deepcopy(result["capture"])
        available = {s["sku_id"]: s for s in capture["skus"]}
        if any(s not in available or available[s].get("availability") == "out_of_stock" for s in selected):
            raise HTTPException(status_code=422, detail="所选SKU不存在或已无库存")
        try:
            category_id, type_id = int(payload["category_id"]), int(payload["type_id"])
            if category_id <= 0 or type_id <= 0:
                raise ValueError("最终类目编号必须为正整数")
            rules = prepare_rules(ROOT, category_id, type_id, allow_fetch=False)
        except (KeyError, TypeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=f"未能读取已有类目规则：{exc}") from exc
        capture["skus"] = [available[s] for s in selected]
        capture["selected_sku_keys"] = selected
        capture["ozon_category_selection"] = {"category_id": category_id, "type_id": type_id,
                                               "rules_snapshot": rules, "selected_at": now_iso()}
        capture["allow_new_version"] = payload.get("allow_new_version") is True
        try:
            imported = ingest_capture(capture, current_operator())
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("message"):
                message = str(exc.detail["message"])
                if exc.detail.get("existing_product_id"):
                    message += f" 已有商品：{exc.detail['existing_product_id']}"
                raise HTTPException(status_code=exc.status_code, detail=message) from exc
            raise
        saved.update({"imported": imported, "import_request_hash": request_hash})
        atomic_write_json(path, saved)
        return imported
