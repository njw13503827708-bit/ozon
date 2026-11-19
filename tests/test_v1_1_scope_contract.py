import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V11ScopeContractTest(unittest.TestCase):
    def test_master_directive_and_current_documents_lock_v1_1(self):
        directive = (ROOT / "docs/MASTER_DEVELOPMENT_DIRECTIVE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("MASTER DEVELOPMENT DIRECTIVE V1.0", directive)
        self.assertIn("只允许执行 V1.1", directive)
        self.assertIn("PREVIEW", directive)
        self.assertIn("CONFIRM", directive)
        self.assertIn("EXECUTE", directive)
        for relative in (
            "AGENTS.md",
            "README.md",
            "CURRENT_TASK.md",
            "PROJECT_HANDOFF.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("docs/MASTER_DEVELOPMENT_DIRECTIVE.md", text, relative)
            self.assertIn("V1.1 Stabilization", text, relative)

    def test_current_documents_do_not_authorize_unreviewed_upload(self):
        combined = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in (
                "AGENTS.md",
                "README.md",
                "CURRENT_TASK.md",
                "PROJECT_HANDOFF.md",
            )
        )
        self.assertNotIn(
            "点击运行任务即明确授权当前采集箱批次执行到Ozon创建或更新",
            combined,
        )
        self.assertNotIn("新建批次默认且强制使用自动提交", combined)


if __name__ == "__main__":
    unittest.main()
