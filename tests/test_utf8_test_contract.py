import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Utf8TestContract(unittest.TestCase):
    def test_tests_do_not_depend_on_the_windows_default_codepage(self):
        offenders = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\.read_text\(\)", text):
                offenders.append(path.name)
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
