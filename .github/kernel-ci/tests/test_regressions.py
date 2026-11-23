"""Pipeline regressions: no kernel or C compilation is performed."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
KERNEL_SHA = "b" * 40
NAME = "cancro-kernel-" + KERNEL_SHA + "-" + SHA


def load(name):
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildRegressionTests(unittest.TestCase):
    def test_ksu_preparation_supplies_ratelimit_header_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kernel/hook").mkdir(parents=True)
            (root / "kernel/Makefile").write_text("kernelsu-y := ksu.o\n")
            (root / "kernel/hook/syscall_table_hook_arm.c").write_text(
                "#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n"
                "replace_syscall(&armeabi_execveat);\n#endif\n")
            header = root / "kernel/kernel_includes.h"
            header.write_text("#include <linux/printk.h>\n")
            for _ in range(2):
                result = subprocess.run([sys.executable, str(ROOT / "patch_ksu.py"), str(root)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(header.read_text().count("#include <linux/ratelimit.h>"), 1)
            self.assertEqual(header.read_text().count("#include <linux/random.h>"), 1)
            self.assertLess(header.read_text().index("linux/printk.h"),
                            header.read_text().index("linux/ratelimit.h"))

    def test_ratelimit_patch_preserves_existing_include_and_rejects_bad_anchor(self):
        patcher = load("patch_ksu")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kernel").mkdir()
            header = root / "kernel/kernel_includes.h"
            original = "#include <linux/printk.h>\n#include <linux/ratelimit.h>\n#include <linux/random.h>\n/* keep this */\n"
            header.write_text(original)
            patcher.supply_legacy_headers(root)
            self.assertEqual(header.read_text(), original)
            for text in ["/* no anchor */\n", "#include <linux/printk.h>\n" * 2]:
                header.write_text(text)
                with self.assertRaises(SystemExit):
                    patcher.supply_legacy_headers(root)
                self.assertEqual(header.read_text(), text)

    def test_get_random_u32_uses_existing_kernel_rng(self):
        header = (ROOT.parents[1] / "include/linux/random.h").read_text()
        self.assertIn("static inline u32 get_random_u32(void)", header)
        import re
        match = re.search(r"static inline u32 get_random_u32\(void\)\s*\{(.*?)\}", header, re.S)
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertEqual(" ".join(body.split()),
                         "u32 value; get_random_bytes(&value, sizeof(value)); return value;")
        self.assertEqual(header.count("get_random_u32(void)"), 1)
        self.assertLess(header.index("#ifdef __KERNEL__"), match.start())
        self.assertLess(match.end(), header.index("#endif /* __KERNEL___ */"))
        self.assertIn("u32 value;", body)
        self.assertIn("get_random_bytes(&value, sizeof(value));", body)
        self.assertIn("return value;", body)
        self.assertNotIn("get_random_int", body)
        self.assertLess(header.index("extern void get_random_bytes("), match.start())

    def test_verify_build_cli_rejects_missing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT / "verify.py"), "build", directory],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, "CLI must execute verify_build")
            self.assertNotIn("Verified", result.stdout)

    def test_detector_can_read_actions_metadata(self):
        workflow = (ROOT.parents[1] / ".github/workflows/kernel.yml").read_text()
        self.assertRegex(workflow, r"(?m)^  actions: read$")

    def test_workflow_has_no_publish_job(self):
        workflow = (ROOT.parents[1] / ".github/workflows/kernel.yml").read_text()
        self.assertNotRegex(workflow, r"(?m)^  publish:")


class ArtifactDetectionTests(unittest.TestCase):
    def detect(self, artifacts, conclusion="success", api_error=False):
        ci = load("ci")
        def run(command, **kwargs):
            if command[0] == "git":
                return subprocess.CompletedProcess(command, 0, SHA + "\trefs/heads/master\n", "")
            if command[:2] == ["gh", "api"]:
                endpoint = command[-1]
                if api_error:
                    return subprocess.CompletedProcess(command, 1, "", "HTTP 403")
                if "/actions/artifacts?" in endpoint:
                    body = {"artifacts": artifacts, "total_count": len(artifacts)}
                elif "/actions/runs/" in endpoint:
                    body = {"head_sha": KERNEL_SHA, "conclusion": conclusion}
                else:
                    return subprocess.CompletedProcess(command, 1, "", "HTTP 404")
                return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
            return subprocess.CompletedProcess(command, 1, "", "release not found")
        with mock.patch.object(ci.subprocess, "run", side_effect=run), mock.patch.dict(
                os.environ, {"GITHUB_SHA": KERNEL_SHA}):
            return ci.detect("armv7a/kernel", "https://github.com/rsuntk/KernelSU.git", "master")

    def test_successful_same_kernel_and_ksu_commit_skips(self):
        artifact = {"name": NAME, "workflow_run": {"id": 10, "head_sha": KERNEL_SHA}}
        self.assertFalse(self.detect([artifact])["changed"])

    def test_first_run_and_failed_build_retry(self):
        self.assertTrue(self.detect([])["changed"])
        artifact = {"name": NAME, "workflow_run": {"id": 10, "head_sha": KERNEL_SHA}}
        self.assertTrue(self.detect([artifact], conclusion="failure")["changed"])

    def test_artifacts_from_other_source_or_ksu_commit_do_not_skip(self):
        for name in [NAME.replace(SHA, "c" * 40), NAME.replace(KERNEL_SHA, "d" * 40)]:
            artifact = {"name": name, "workflow_run": {"id": 10, "head_sha": KERNEL_SHA}}
            self.assertTrue(self.detect([artifact])["changed"])

    def test_api_failure_is_not_a_new_commit(self):
        with self.assertRaises(RuntimeError):
            self.detect([], api_error=True)


if __name__ == "__main__":
    unittest.main()
