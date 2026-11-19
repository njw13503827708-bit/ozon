import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_ready_product(root: Path, product_id: str, collection_id: str, sku_id: str, store_ids: list[str]) -> None:
    product = root / "products" / product_id
    image = product / "output" / "accepted-images" / f"{sku_id}.jpg"
    detail_image = product / "output" / "accepted-images" / f"{sku_id}-detail.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(f"image:{product_id}".encode("utf-8"))
    detail_image.write_bytes(f"detail:{product_id}".encode("utf-8"))
    image_relative = image.relative_to(root).as_posix()
    detail_relative = detail_image.relative_to(root).as_posix()
    write_json(product / "status.json", {
        "product_id": product_id,
        "status": "READY_FOR_BATCH_REVIEW",
        "current_step": "image_qc",
        "next_action": "batch_upload_review",
    })
    write_json(product / "input" / "source.json", {
        "product_id": product_id,
        "collection_id": collection_id,
        "title_cn": f"不锈钢厨房收纳架 {product_id}",
    })
    write_json(product / "input" / "category-selection.json", {
        "selection_source": "user_final_choice",
        "category_id": 170000,
        "type_id": 170001,
        "category_name_ru": "Кухонные органайзеры",
        "category_name_zh": "厨房收纳架",
        "category_path": ["Дом", "Кухня", "Кухонные органайзеры"],
        "category_path_zh": ["家居", "厨房", "厨房收纳架"],
        "locked_for_batch": True,
        "allow_runtime_rematch": False,
    })
    write_json(product / "output" / "sku-run-snapshot.json", {
        "product_id": product_id,
        "collection_id": collection_id,
        "sku_rows": [{
            "sku_id": sku_id,
            "offer_id": f"offer-{sku_id}",
            "selling_price_rub": 1299,
            "image_path": image_relative,
        }],
    })
    write_json(product / "output" / "copy-ru.json", {
        "product_id": product_id,
        "title_ru": f"Кухонный органайзер {product_id}",
        "description_ru": "Подробное описание кухонного органайзера на русском языке.",
        "bullets_ru": [{"text_ru": "Проверенное преимущество товара"}],
    })
    write_json(product / "output" / "ozon-category.json", {
        "product_id": product_id, "category_id": 170000, "type_id": 170001,
        "category_name": "Кухонные органайзеры",
        "category_path": ["Дом", "Кухня", "Кухонные органайзеры"],
        "match_status": "api_confirmed", "metadata_source": "ozon_seller_api", "confidence": 1.0,
    })
    write_json(product / "output" / "ozon-attributes-final.json", {
        "product_id": product_id,
        "category_id": 170000,
        "attributes": [{"attribute_id": 1, "value": "сталь", "source": "1688"}],
        "required_summary": {"total": 1, "filled": 1, "missing": 0, "missing_attribute_ids": []},
    })
    write_json(product / "output" / "ozon-upload-config.json", {
        "product_id": product_id,
        "category_id": 170000,
        "target_store_ids": store_ids,
        "stock_mode": "not_set",
        "sku_prices": [{"source_sku_id": sku_id, "price": 1299}],
        "product_dimensions": {"length_mm": 100, "width_mm": 80, "height_mm": 50},
        "package_dimensions": {"length_mm": 110, "width_mm": 90, "height_mm": 60},
        "product_weight": {"value_g": 200},
        "package_weight": {"value_g": 250},
    })
    write_json(product / "output" / "ozon-draft.json", {
        "product_id": product_id,
        "category_id": 170000,
        "stock": {"quantity": None, "warehouse_id": "unknown"},
        "skus": [{
            "sku_id": sku_id,
            "offer_id": f"offer-{sku_id}",
            "price": 1299,
            "stock": None,
            "images": [image_relative, detail_relative],
        }],
        "images": [image_relative, detail_relative],
        "preflight": {"status": "pass"},
    })
    write_json(product / "output" / "ozon-tags.json", {"tags": ["#товар", "#покупка"]})
    write_json(product / "output" / "rich-content.json", {
        "items": [{"type": "text", "text": "Подробности и преимущества товара"}],
    })
    write_json(product / "output" / "image-qc-report.json", {
        "product_id": product_id,
        "decision": "pass",
        "images_checked": [
            {"path": image_relative, "status": "pass"},
            {"path": detail_relative, "status": "pass"},
        ],
        "technical_checks": [
            {"path": image_relative, "status": "pass"},
            {"path": detail_relative, "status": "pass"},
        ],
        "critical_failures": [],
    })


class BatchUploadApprovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        make_ready_product(self.root, "P000101", "C-101", "SKU-101", ["store-a", "store-b"])
        make_ready_product(self.root, "P000102", "C-102", "SKU-102", ["store-a"])
        self.batch = {
            "batch_id": "B-REVIEW-1",
            "products": [
                {"product_id": "P000102", "collection_id": "C-102", "target_store_ids": ["store-a"]},
                {"product_id": "P000101", "collection_id": "C-101", "target_store_ids": ["store-a", "store-b"]},
            ],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ready_batch_has_stable_review_and_zero_api_counters(self) -> None:
        from scripts.batch_upload_approval import (
            AWAITING_BATCH_UPLOAD_CONFIRMATION,
            build_batch_upload_review,
            compute_batch_review_hash,
        )

        review = build_batch_upload_review(self.root, self.batch)

        self.assertEqual(review["status"], AWAITING_BATCH_UPLOAD_CONFIRMATION)
        self.assertTrue(review["can_confirm"])
        self.assertEqual(review["product_count"], 2)
        self.assertEqual(review["sku_count"], 2)
        self.assertEqual(review["blocker_count"], 0)
        self.assertEqual(review["write_api_calls"], 0)
        self.assertEqual(review["inventory_api_calls"], 0)
        self.assertEqual(len(review["review_hash"]), 64)
        self.assertEqual(review["review_hash"], compute_batch_review_hash(self.root, self.batch))
        self.assertEqual(review["review_hash"], build_batch_upload_review(self.root, self.batch)["review_hash"])
        self.assertEqual([row["product_id"] for row in review["products"]], ["P000101", "P000102"])
        self.assertEqual(review["target_store_ids"], ["store-a", "store-b"])
        self.assertEqual(review["products"][0]["category_name_zh"], "厨房收纳架")
        self.assertEqual(review["products"][0]["category_name_ru"], "Кухонные органайзеры")
        self.assertEqual(review["products"][0]["category_path"], ["Дом", "Кухня", "Кухонные органайзеры"])

    def test_ineligible_artifacts_block_the_entire_batch(self) -> None:
        from scripts.batch_upload_approval import (
            BLOCKED_BEFORE_UPLOAD,
            build_batch_upload_review,
        )

        cases = [
            ("product_not_ready", lambda: write_json(self.root / "products/P000101/status.json", {
                "product_id": "P000101", "status": "NEEDS_ATTENTION",
            })),
            ("missing_final_attributes", lambda: (self.root / "products/P000101/output/ozon-attributes-final.json").unlink()),
            ("image_qc_failed", lambda: write_json(self.root / "products/P000101/output/image-qc-report.json", {
                "decision": "fail", "critical_failures": [{"code": "blur"}],
            })),
            ("missing_target_store", lambda: (
                self.batch["products"][1].update({"target_store_ids": []}),
                write_json(self.root / "products/P000101/output/sku-run-snapshot.json", {
                    "product_id": "P000101", "collection_id": "C-101", "target_store_ids": [],
                    "sku_rows": [{"sku_id": "SKU-101", "offer_id": "offer-SKU-101", "selling_price_rub": 1299}],
                }),
                write_json(self.root / "products/P000101/output/ozon-upload-config.json", {
                    "product_id": "P000101", "category_id": 170000, "target_store_ids": [], "sku_prices": [],
                }),
            )),
            ("inventory_field_forbidden", lambda: write_json(self.root / "products/P000101/output/ozon-draft.json", {
                "product_id": "P000101", "skus": [{"sku_id": "SKU-101", "offer_id": "offer-SKU-101"}], "stock": 5,
            })),
            ("collection_mismatch", lambda: self.batch["products"][1].update({"collection_id": "WRONG"})),
            ("missing_sku", lambda: write_json(self.root / "products/P000101/output/sku-run-snapshot.json", {
                "product_id": "P000101", "collection_id": "C-101", "sku_rows": [],
            })),
            ("missing_offer_id", lambda: write_json(self.root / "products/P000101/output/ozon-draft.json", {
                "product_id": "P000101", "category_id": 170000,
                "skus": [{"sku_id": "SKU-101", "price": 1299, "images": []}], "images": [],
            })),
            ("unsafe_publication_state", lambda: write_json(self.root / "products/P000101/output/ozon-upload-config.json", {
                "product_id": "P000101", "category_id": 170000, "target_store_ids": ["store-a", "store-b"],
                "publication_state": "submitted",
            })),
            ("placeholder_listing_identity", lambda: write_json(self.root / "products/P000101/output/copy-ru.json", {
                "title_ru": "TEST-DO-NOT-SELL-20260921", "description_ru": "Описание",
                "bullets_ru": [{"text_ru": "Преимущество"}],
            })),
            ("missing_russian_copy", lambda: write_json(self.root / "products/P000101/output/copy-ru.json", {
                "title_ru": "", "description_ru": "", "bullets_ru": [],
            })),
            ("category_not_confirmed", lambda: write_json(self.root / "products/P000101/output/ozon-category.json", {
                "category_id": 170000, "match_status": "needs_review", "confidence": 0.40,
            })),
            ("missing_required_logistics", lambda: write_json(self.root / "products/P000101/output/ozon-upload-config.json", {
                "product_id": "P000101", "category_id": 170000,
                "target_store_ids": ["store-a", "store-b"], "sku_prices": [{"source_sku_id": "SKU-101", "price": 1299}],
            })),
            ("missing_search_tags", lambda: write_json(self.root / "products/P000101/output/ozon-tags.json", {"tags": []})),
            ("missing_rich_content", lambda: write_json(self.root / "products/P000101/output/rich-content.json", {"items": []})),
            ("image_set_incomplete", lambda: write_json(self.root / "products/P000101/output/image-qc-report.json", {
                "decision": "pass", "critical_failures": [],
                "images_checked": [{"path": "products/P000101/output/accepted-images/SKU-101.jpg", "status": "pass"}],
                "technical_checks": [{"path": "products/P000101/output/accepted-images/SKU-101.jpg", "status": "pass"}],
            })),
        ]
        for expected_code, mutate in cases:
            with self.subTest(expected_code=expected_code):
                self.tearDown()
                self.setUp()
                mutate()
                review = build_batch_upload_review(self.root, self.batch)
                self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
                self.assertFalse(review["can_confirm"])
                self.assertIn(expected_code, {risk["code"] for risk in review["risks"]})
                self.assertEqual(review["write_api_calls"], 0)
                self.assertEqual(review["inventory_api_calls"], 0)

    def test_low_risk_estimate_is_a_warning_not_a_blocker(self) -> None:
        from scripts.batch_upload_approval import (
            AWAITING_BATCH_UPLOAD_CONFIRMATION,
            build_batch_upload_review,
        )

        attributes = json.loads((self.root / "products/P000101/output/ozon-attributes-final.json").read_text(encoding="utf-8"))
        attributes["attributes"].append({"attribute_id": 2, "value": "10", "source": "AI_estimated"})
        write_json(self.root / "products/P000101/output/ozon-attributes-final.json", attributes)

        review = build_batch_upload_review(self.root, self.batch)

        self.assertEqual(review["status"], AWAITING_BATCH_UPLOAD_CONFIRMATION)
        self.assertTrue(review["can_confirm"])
        self.assertEqual(review["blocker_count"], 0)
        self.assertGreaterEqual(review["warning_count"], 1)
        self.assertIn("low_risk_estimate", {risk["code"] for risk in review["risks"]})

    def test_non_positive_or_non_finite_price_blocks_entire_batch(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        for price in (0, -1, float("nan")):
            with self.subTest(price=price):
                for relative, key in (
                    ("output/sku-run-snapshot.json", "selling_price_rub"),
                    ("output/ozon-draft.json", "price"),
                    ("output/ozon-upload-config.json", "price"),
                ):
                    path = self.root / "products/P000101" / relative
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    rows = payload.get("sku_rows") or payload.get("skus") or payload.get("sku_prices")
                    rows[0][key] = price
                    write_json(path, payload)

                review = build_batch_upload_review(self.root, self.batch)
                self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
                self.assertIn("missing_final_price", {risk["code"] for risk in review["risks"]})
                self.tearDown()
                self.setUp()

    def test_missing_required_attribute_blocks_entire_batch(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        path = self.root / "products/P000101/output/ozon-attributes-final.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["required_summary"] = {"total": 2, "filled": 1, "missing": 1, "missing_attribute_ids": [99]}
        write_json(path, payload)

        review = build_batch_upload_review(self.root, self.batch)
        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertIn("missing_required_attributes", {risk["code"] for risk in review["risks"]})

    def test_missing_required_attribute_summary_blocks_entire_batch(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        path = self.root / "products/P000101/output/ozon-attributes-final.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("required_summary")
        write_json(path, payload)

        review = build_batch_upload_review(self.root, self.batch)
        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertIn("missing_required_attributes", {risk["code"] for risk in review["risks"]})

    def test_non_russian_copy_tags_and_rich_content_block_entire_batch(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        write_json(self.root / "products/P000101/output/copy-ru.json", {
            "title_ru": "厨房收纳架",
            "description_ru": "Описание 中文详情",
            "bullets_ru": [{"text_ru": "中文卖点"}],
        })
        write_json(self.root / "products/P000101/output/ozon-tags.json", {"tags": ["#厨房收纳"]})
        write_json(self.root / "products/P000101/output/rich-content.json", {
            "items": [{"type": "text", "text": "中文富内容"}],
        })

        review = build_batch_upload_review(self.root, self.batch)
        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertTrue({"missing_russian_copy", "missing_search_tags", "missing_rich_content"}.issubset(
            {risk["code"] for risk in review["risks"]}
        ))

    def test_mixed_chinese_residue_blocks_russian_content(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        write_json(self.root / "products/P000101/output/copy-ru.json", {
            "title_ru": "Кухонный органайзер 厨房收纳架",
            "description_ru": "Описание 中文详情",
            "bullets_ru": [{"text_ru": "Преимущество 中文卖点"}],
        })
        write_json(self.root / "products/P000101/output/rich-content.json", {
            "items": [{"type": "text", "text": "Описание 中文富内容"}],
        })

        review = build_batch_upload_review(self.root, self.batch)
        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertTrue({"missing_russian_copy", "missing_rich_content"}.issubset(
            {risk["code"] for risk in review["risks"]}
        ))

    def test_category_artifact_mismatch_blocks_entire_batch(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        category_path = self.root / "products/P000101/output/ozon-category.json"
        category = json.loads(category_path.read_text(encoding="utf-8"))
        category.update(category_name="Подушки", category_path=["Дом", "Подушки"])
        write_json(category_path, category)
        copy_path = self.root / "products/P000101/output/copy-ru.json"
        copy = json.loads(copy_path.read_text(encoding="utf-8"))
        copy["title_ru"] = "Подушка"
        write_json(copy_path, copy)

        review = build_batch_upload_review(self.root, self.batch)
        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertIn("category_selection_mismatch", {risk["code"] for risk in review["risks"]})

    def test_locked_authoritative_category_allows_cross_language_synonyms(self) -> None:
        from scripts.batch_upload_approval import AWAITING_BATCH_UPLOAD_CONFIRMATION, build_batch_upload_review

        source_path = self.root / "products/P000101/input/source.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["title_cn"] = "汽车后轮挡泥板"
        write_json(source_path, source)
        selection_path = self.root / "products/P000101/input/category-selection.json"
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        selection.update(category_name_ru="Автомобильные аксессуары", category_name_zh="汽车配件", category_path=["Авто", "Автомобильные аксессуары"], category_path_zh=["汽车", "汽车配件"])
        write_json(selection_path, selection)
        category_path = self.root / "products/P000101/output/ozon-category.json"
        category = json.loads(category_path.read_text(encoding="utf-8"))
        category.update(category_name="Автомобильные аксессуары", category_path=["Авто", "Автомобильные аксессуары"])
        write_json(category_path, category)
        copy_path = self.root / "products/P000101/output/copy-ru.json"
        copy = json.loads(copy_path.read_text(encoding="utf-8"))
        copy["title_ru"] = "Защитный брызговик заднего колеса"
        write_json(copy_path, copy)

        review = build_batch_upload_review(self.root, self.batch)
        self.assertEqual(review["status"], AWAITING_BATCH_UPLOAD_CONFIRMATION)
        self.assertNotIn("category_selection_mismatch", {risk["code"] for risk in review["risks"]})

    def test_boolean_or_non_finite_logistics_blocks_entire_batch(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        for field, value in (("dimension", True), ("dimension", float("nan")), ("weight", True), ("weight", float("nan"))):
            with self.subTest(field=field, value=value):
                path = self.root / "products/P000101/output/ozon-upload-config.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                if field == "dimension":
                    payload["product_dimensions"]["length_mm"] = value
                else:
                    payload["product_weight"]["value_g"] = value
                write_json(path, payload)

                review = build_batch_upload_review(self.root, self.batch)
                self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
                self.assertIn("missing_required_logistics", {risk["code"] for risk in review["risks"]})
                self.tearDown()
                self.setUp()

    def test_draft_upload_image_set_must_match_passed_qc_set(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        draft_path = self.root / "products/P000101/output/ozon-draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["images"] = draft["images"][:1]
        draft["skus"][0]["images"] = draft["skus"][0]["images"][:1]
        write_json(draft_path, draft)

        review = build_batch_upload_review(self.root, self.batch)
        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertIn("image_set_incomplete", {risk["code"] for risk in review["risks"]})

    def test_schema_required_no_op_stock_does_not_block_review(self) -> None:
        from scripts.batch_upload_approval import AWAITING_BATCH_UPLOAD_CONFIRMATION, build_batch_upload_review

        review = build_batch_upload_review(self.root, self.batch)

        self.assertEqual(review["status"], AWAITING_BATCH_UPLOAD_CONFIRMATION)
        self.assertNotIn("inventory_field_forbidden", {risk["code"] for risk in review["risks"]})

    def test_top_level_null_warehouse_id_blocks_review(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        draft_path = self.root / "products/P000101/output/ozon-draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["stock"]["warehouse_id"] = None
        write_json(draft_path, draft)

        review = build_batch_upload_review(self.root, self.batch)

        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertIn("inventory_field_forbidden", {risk["code"] for risk in review["risks"]})

    def test_executable_draft_stock_values_block_review(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        cases = [
            ("top_level_quantity", {"quantity": 1, "warehouse_id": "unknown"}, None),
            ("top_level_warehouse", {"quantity": None, "warehouse_id": 123}, None),
            ("sku_stock", {"quantity": None, "warehouse_id": "unknown"}, 1),
        ]
        for name, stock, sku_stock in cases:
            with self.subTest(name=name):
                draft_path = self.root / "products/P000101/output/ozon-draft.json"
                draft = json.loads(draft_path.read_text(encoding="utf-8"))
                draft["stock"] = stock
                if sku_stock is not None:
                    draft["skus"][0]["stock"] = sku_stock
                write_json(draft_path, draft)

                review = build_batch_upload_review(self.root, self.batch)

                self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
                self.assertIn("inventory_field_forbidden", {risk["code"] for risk in review["risks"]})
                self.tearDown()
                self.setUp()

    def test_missing_final_artifact_image_reference_blocks_review(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        draft_path = self.root / "products/P000101/output/ozon-draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["images"].append("products/P000101/output/accepted-images/missing.jpg")
        write_json(draft_path, draft)

        review = build_batch_upload_review(self.root, self.batch)

        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertIn("missing_image_file", {risk["code"] for risk in review["risks"]})

    def test_missing_final_sku_local_image_reference_blocks_review(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        draft_path = self.root / "products/P000101/output/ozon-draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["skus"][0]["local_image_path"] = "products/P000101/output/accepted-images/missing-sku.jpg"
        write_json(draft_path, draft)

        review = build_batch_upload_review(self.root, self.batch)

        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertIn("missing_image_file", {risk["code"] for risk in review["risks"]})

    def test_real_required_summary_drives_attribute_coverage_and_risks(self) -> None:
        from scripts.batch_upload_approval import AWAITING_BATCH_UPLOAD_CONFIRMATION, build_batch_upload_review

        review = build_batch_upload_review(self.root, self.batch)
        first_product = review["products"][0]

        self.assertEqual(review["status"], AWAITING_BATCH_UPLOAD_CONFIRMATION)
        self.assertEqual(first_product["attribute_coverage"], {
            "total": 1,
            "filled": 1,
            "missing": 0,
            "missing_attribute_ids": [],
            "unknown_count": 0,
            "estimated_count": 0,
        })
        self.assertEqual(first_product["unknown_count"], 0)
        self.assertEqual(first_product["estimated_count"], 0)

    def test_hash_is_root_independent_for_absolute_final_image_paths(self) -> None:
        from scripts.batch_upload_approval import compute_batch_review_hash

        with tempfile.TemporaryDirectory() as second_temp:
            second_root = Path(second_temp)
            make_ready_product(second_root, "P000101", "C-101", "SKU-101", ["store-a", "store-b"])
            make_ready_product(second_root, "P000102", "C-102", "SKU-102", ["store-a"])
            for root in (self.root, second_root):
                for product_id, sku_id in (("P000101", "SKU-101"), ("P000102", "SKU-102")):
                    image_path = (root / "products" / product_id / "output" / "accepted-images" / f"{sku_id}.jpg").resolve()
                    image_reference = str(image_path)
                    snapshot_path = root / "products" / product_id / "output" / "sku-run-snapshot.json"
                    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                    snapshot["sku_rows"][0]["image_path"] = image_reference
                    write_json(snapshot_path, snapshot)
                    draft_path = root / "products" / product_id / "output" / "ozon-draft.json"
                    draft = json.loads(draft_path.read_text(encoding="utf-8"))
                    draft["images"] = [image_reference]
                    draft["skus"][0]["images"] = [image_reference]
                    draft["source_refs"] = [image_reference]
                    write_json(draft_path, draft)
                    qc_path = root / "products" / product_id / "output" / "image-qc-report.json"
                    qc = json.loads(qc_path.read_text(encoding="utf-8"))
                    qc["images_checked"][0]["path"] = image_reference
                    write_json(qc_path, qc)

            self.assertEqual(
                compute_batch_review_hash(self.root, self.batch),
                compute_batch_review_hash(second_root, self.batch),
            )

    def test_submitted_preflight_status_blocks_review(self) -> None:
        from scripts.batch_upload_approval import BLOCKED_BEFORE_UPLOAD, build_batch_upload_review

        draft_path = self.root / "products/P000101/output/ozon-draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["preflight"]["status"] = "submitted"
        write_json(draft_path, draft)

        review = build_batch_upload_review(self.root, self.batch)

        self.assertEqual(review["status"], BLOCKED_BEFORE_UPLOAD)
        self.assertIn("unsafe_publication_state", {risk["code"] for risk in review["risks"]})

    def test_hash_changes_when_protected_image_bytes_change(self) -> None:
        from scripts.batch_upload_approval import compute_batch_review_hash

        before = compute_batch_review_hash(self.root, self.batch)
        image = self.root / "products/P000101/output/accepted-images/SKU-101.jpg"
        image.write_bytes(b"changed-image-byte")

        self.assertNotEqual(before, compute_batch_review_hash(self.root, self.batch))
