"""Behavior tests using temporary source trees and the accepted extractors."""

import collections
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.workflow_contract.check_contracts import audit

CLI = ROOT / "scripts/workflow_contract/check_contracts.py"
API = Path("android_app/app/src/main/java/com/smartfitness/app/api/ApiService.kt")


class ContractAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "backend").mkdir()
        (self.root / API).parent.mkdir(parents=True)

    def sources(self, backend, android):
        (self.root / "backend/main.py").write_text(backend, encoding="utf-8")
        (self.root / API).write_text(android, encoding="utf-8")

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(CLI), "--repo", str(self.root), *args],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def test_shape_method_literals_and_duplicate_partition(self):
        self.sources(
            'raise RuntimeError("must never execute")\n'
            '@app.get("/items/{item_id}")\ndef get(): pass\n'
            '@app.get("/items/{item_id}")\ndef duplicate(): pass\n'
            '@app.get("/groups/{group}/items/{item}")\ndef nested(): pass\n',
            '@GET("items/{id}")\n@GET("items/{id}")\n'
            '@POST("items/{id}")\n@GET("other/{id}")\n'
            '@GET("items/{id}/")\n@GET("groups/{g}/items/{i}")\n',
        )
        result = audit(self.root)
        self.assertEqual((result["backend_count"], result["android_count"]), (3, 6))
        self.assertEqual(len(result["matched"]), 3)
        self.assertEqual(len(result["missing"]), 3)
        rows = result["matched"] + result["missing"]
        self.assertEqual(collections.Counter(row["line"] for row in rows),
                         collections.Counter(range(1, 7)))
        self.assertEqual(result["matched"][1]["path"], "/items/{id}")
        self.assertTrue(all(row["source"] == API.as_posix() for row in rows))
        self.assertTrue(result["limitations"])
        self.assertEqual(result, audit(self.root))

    def test_comments_qualified_annotations_and_source_lines(self):
        self.sources(
            '# @app.get("/fake")\n@app.put(path="/real/{id}")\ndef f(): pass\n',
            '// @GET("fake")\n/* @DELETE("fake")\n */\n'
            '@retrofit2.http.PUT("real/{key}")\n',
        )
        result = audit(self.root)
        self.assertEqual(result["backend_count"], 1)
        self.assertEqual(result["android_count"], 1)
        self.assertEqual(result["matched"][0]["line"], 4)
        self.assertEqual(result["missing"], [])

    def test_cli_findings_default_and_strict(self):
        self.sources('', '@GET("missing")\n')
        normal, strict = self.cli(), self.cli("--strict")
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertEqual(strict.returncode, 1, strict.stderr)
        self.assertEqual(json.loads(normal.stdout), json.loads(strict.stdout))
        self.assertEqual(len(json.loads(normal.stdout)["missing"]), 1)

    def test_strict_success_and_empty_inventory(self):
        for backend, android in [('', ''), (
            '@app.get("/ok/{key}")\ndef f(): pass\n', '@GET("ok/{id}")\n'
        )]:
            with self.subTest(android=android):
                self.sources(backend, android)
                result = self.cli("--strict")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["missing"], [])

    def test_input_errors_are_nonzero_without_success_json(self):
        self.sources('def broken(', '')
        for failure in ("syntax", "missing_android", "missing_backend"):
            with self.subTest(failure=failure):
                if failure == "missing_android":
                    self.sources('', '')
                    (self.root / API).unlink()
                elif failure == "missing_backend":
                    (self.root / "backend/main.py").unlink()
                    (self.root / "backend").rmdir()
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn("Contract audit failed", result.stderr)

    def test_nonexistent_root(self):
        with self.assertRaises(NotADirectoryError):
            audit(self.root / "absent")


if __name__ == "__main__":
    unittest.main()
