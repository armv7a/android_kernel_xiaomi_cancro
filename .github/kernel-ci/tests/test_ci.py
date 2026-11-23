import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile
from support import write_zip
REAL_RUN = subprocess.run

ROOT = Path(__file__).resolve().parents[1]
SHA = "88867e52ab4c4e754f4155674ba29a9f1092a875"
TOKEN = "123:private-test-token"
FILES = ["zImage", "anykernel.sh", "tools/ak3-core.sh", "tools/magiskboot", "tools/busybox", "META-INF/com/google/android/update-binary", "META-INF/com/google/android/updater-script", "LICENSE.kernel", "build-info.json", "kernel.config"]


class FakeServices:
    def __init__(self, release=None, sha=SHA, mode="success"):
        self.release, self.sha, self.mode = release, sha, mode
        self.events, self.requests, self.commands = [], [], []

    def run(self, command, **kwargs):
        if command[0] == "readelf":
            return REAL_RUN(command, **kwargs)
        self.commands.append(command)
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, self.sha + "\trefs/heads/master\n", "")
        if command[1] == "api":
            if "/actions/artifacts?" in command[-1] and self.mode != "github-error":
                artifacts = []
                if self.release is not None and not self.release["draft"] and self.release.get("tag_name", "ksu-" + SHA) == "ksu-" + self.sha:
                    artifacts = [{"name": "cancro-kernel-" + "b" * 40 + "-" + self.sha, "workflow_run": {"id": 10, "head_sha": "b" * 40}}]
                return subprocess.CompletedProcess(command, 0, json.dumps({"artifacts": artifacts}), "")
            if "/actions/runs/" in command[-1] and self.mode != "github-error":
                return subprocess.CompletedProcess(command, 0, json.dumps({"head_sha": "b" * 40, "conclusion": "success"}), "")
            if self.mode == "hidden-draft" and self.release is not None and self.release["draft"]:
                return subprocess.CompletedProcess(command, 1, "", "HTTP 404")
            if self.mode == "github-error":
                return subprocess.CompletedProcess(command, 1, "", "HTTP 403")
            if self.release is None or self.release.get("tag_name", "ksu-" + SHA) != "ksu-" + self.sha:
                return subprocess.CompletedProcess(command, 1, "", "gh: Not Found (HTTP 404)")
            return subprocess.CompletedProcess(command, 0, json.dumps(self.release), "")
        if command[2] == "view":
            if self.release is None or self.release.get("tag_name", "ksu-" + SHA) != "ksu-" + self.sha:
                return subprocess.CompletedProcess(command, 1, "", "release not found")
            return subprocess.CompletedProcess(command, 0, json.dumps({"isDraft": self.release["draft"]}), "")
        operation = command[2]
        self.events.append(operation)
        if operation == "create":
            self.release = {"draft": True, "tag_name": "ksu-" + SHA}
        elif operation == "edit":
            self.release["draft"] = False
        return subprocess.CompletedProcess(command, 0, "", "")

    def urlopen(self, request, **kwargs):
        self.requests.append(request)
        if request.full_url.endswith("getChat"):
            self.events.append("getChat")
            data = {"ok": True, "result": {"id": -10012345, "type": "supergroup" if self.mode == "group" else "channel"}}
        else:
            self.events.append("sendDocument")
            if self.mode == "http-error":
                raise urllib.error.URLError("failed URL containing " + TOKEN)
            if self.mode == "ok-false":
                data = {"ok": False, "description": "denied"}
            else:
                data = {"ok": True, "result": {"message_id": 12, "chat": {"id": -10012345, "type": "channel"}, "document": {"file_id": "example"}}}
        if self.mode == "missing-id":
            data["result"].pop("id", None)
            data["result"].get("chat", {}).pop("id", None)
        if self.mode == "missing-message-id":
            data["result"].pop("message_id", None)
        if self.mode == "bad-document" and request.full_url.endswith("sendDocument"):
            data["result"]["document"] = True
        return io.BytesIO(json.dumps(data).encode())


class CITests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"GITHUB_SHA": "b" * 40})
        env.start()
        self.addCleanup(env.stop)

    def module(self):
        path = ROOT / "ci.py"
        self.assertTrue(path.is_file(), "CI detector/publisher is missing")
        sys.path.insert(0, str(ROOT))
        spec = importlib.util.spec_from_file_location("ci", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_module_paths_resolve_inside_the_repository(self):
        ci = self.module()
        self.assertTrue((ci.ROOT / ".github/kernel-ci/build.conf").is_file(), "ci.py ROOT must point at the repository root")
        self.assertFalse((ci.ROOT / ".github/.github").exists(), "double .github path regression")

    def assets(self, root):
        archive = root / ("cancro-kernelsu-" + SHA[:12] + ".zip")
        write_zip(archive, SHA)
        (root / ("source-" + SHA[:12] + ".tar.gz")).write_bytes(b"source fixture")
        (root / "build-info.json").write_text(json.dumps({"ksu_sha": SHA}))
        (root / "kernel.config").write_text("CONFIG_KSU=y\n")

        checksums = []
        for path in sorted(root.iterdir()):
            checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name)
        (root / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
        return archive

    def services(self, ci, service):
        return mock.patch.object(ci.subprocess, "run", side_effect=service.run), mock.patch.object(ci.urllib.request, "urlopen", side_effect=service.urlopen)

    def test_first_run_changed_commit_and_draft_all_build(self):
        ci = self.module()
        for release in [None, {"draft": True}, {"draft": False, "tag_name": "ksu-" + "a" * 40}]:
            service = FakeServices(release)
            with self.subTest(release=release), mock.patch.object(ci.subprocess, "run", side_effect=service.run):
                result = ci.detect("owner/kernel", "https://github.com/rsuntk/KernelSU.git", "master")
                self.assertEqual(result, {"sha": SHA, "changed": True})
                self.assertEqual(service.commands[0][-1], "refs/heads/master")

    def test_published_commit_skips_build(self):
        ci = self.module()
        service = FakeServices({"draft": False})
        with mock.patch.object(ci.subprocess, "run", side_effect=service.run):
            self.assertEqual(ci.detect("owner/kernel", "https://github.com/rsuntk/KernelSU.git", "master"), {"sha": SHA, "changed": False})

    def test_github_errors_are_not_treated_as_first_run(self):
        ci = self.module()
        service = FakeServices(mode="github-error")
        with mock.patch.object(ci.subprocess, "run", side_effect=service.run), self.assertRaises(RuntimeError):
            ci.detect("owner/kernel", "https://github.com/rsuntk/KernelSU.git", "master")

    def test_malformed_commit_is_rejected(self):
        ci = self.module()
        for sha in ["bad", "a" * 39, "a" * 39 + ";", "A" * 40]:
            service = FakeServices(sha=sha)
            with self.subTest(sha=sha), mock.patch.object(ci.subprocess, "run", side_effect=service.run), self.assertRaises(ValueError):
                ci.detect("owner/kernel", "https://github.com/rsuntk/KernelSU.git", "master")

    def test_publish_marks_success_only_after_channel_upload(self):
        ci = self.module()
        service = FakeServices()
        with tempfile.TemporaryDirectory() as directory:
            self.assets(Path(directory))
            run, web = self.services(ci, service)
            with run, web, mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-10012345", "ENABLE_TELEGRAM": "true", "GITHUB_SHA": "a" * 40}):
                ci.publish("owner/kernel", SHA, Path(directory))
            self.assertEqual(service.events, ["create", "upload", "getChat", "sendDocument", "edit"])
            self.assertFalse(service.release["draft"])
            upload = service.requests[-1].data
            self.assertIn(b"-10012345", upload)
            self.assertIn(SHA.encode(), upload)
            self.assertIn(("cancro-kernelsu-" + SHA[:12] + ".zip").encode(), upload)

    def test_telegram_failure_leaves_draft_retryable(self):
        ci = self.module()
        for mode in ["ok-false", "http-error", "group"]:
            service = FakeServices(mode=mode)
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                self.assets(Path(directory))
                run, web = self.services(ci, service)
                with run, web, mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-10012345", "ENABLE_TELEGRAM": "true", "GITHUB_SHA": "a" * 40}):
                    with self.assertRaises(RuntimeError) as error:
                        ci.publish("owner/kernel", SHA, Path(directory))
                self.assertNotIn(TOKEN, str(error.exception))
                self.assertTrue(service.release["draft"])
                self.assertNotIn("edit", service.events)
                if mode == "group":
                    self.assertNotIn("sendDocument", service.events)

    def test_malformed_telegram_success_never_marks_release_complete(self):
        ci = self.module()
        for mode in ["missing-id", "missing-message-id", "bad-document"]:
            service = FakeServices(mode=mode)
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                self.assets(Path(directory))
                run, web = self.services(ci, service)
                with run, web, mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-10012345", "ENABLE_TELEGRAM": "true"}), self.assertRaises(RuntimeError):
                    ci.publish("owner/kernel", SHA, Path(directory))
                self.assertNotIn("edit", service.events)

    def test_document_upload_binds_resolved_channel_id_not_mutable_username(self):
        ci = self.module()
        service = FakeServices()
        with tempfile.TemporaryDirectory() as directory:
            archive = self.assets(Path(directory))
            with mock.patch.object(ci.urllib.request, "urlopen", side_effect=service.urlopen), mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "@examplechannel"}):
                ci.send_telegram(archive, SHA)
            body = service.requests[-1].data
            self.assertIn(b"-10012345", body)
            self.assertNotIn(b"@examplechannel", body)

    def test_missing_or_corrupt_build_never_creates_release(self):
        ci = self.module()
        for broken in ["missing", "checksum"]:
            service = FakeServices()
            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if broken == "checksum":
                    archive = self.assets(root)
                    archive.write_bytes(b"corrupted zip")
                run, web = self.services(ci, service)
                with run, web, mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-10012345", "ENABLE_TELEGRAM": "true"}), self.assertRaises(ValueError):
                    ci.publish("owner/kernel", SHA, root)
                self.assertEqual(service.events, [])

    def test_hidden_draft_tag_uses_cli_lookup_and_never_creates_duplicate(self):
        ci = self.module()
        service = FakeServices({"draft": True}, mode="hidden-draft")
        with tempfile.TemporaryDirectory() as directory:
            self.assets(Path(directory))
            run, web = self.services(ci, service)
            with run, web, mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-10012345", "ENABLE_TELEGRAM": "true"}):
                ci.publish("owner/kernel", SHA, Path(directory))
            self.assertEqual(service.events, ["upload", "getChat", "sendDocument", "edit"])
            self.assertFalse(service.release["draft"])

    def test_existing_draft_is_reused_instead_of_marked_complete(self):
        ci = self.module()
        service = FakeServices({"draft": True})
        with tempfile.TemporaryDirectory() as directory:
            self.assets(Path(directory))
            run, web = self.services(ci, service)
            with run, web, mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-10012345", "ENABLE_TELEGRAM": "true"}):
                ci.publish("owner/kernel", SHA, Path(directory))
            self.assertEqual(service.events, ["upload", "getChat", "sendDocument", "edit"])

    def test_telegram_disabled_by_default_skips_channel_upload(self):
        ci = self.module()
        service = FakeServices()
        with tempfile.TemporaryDirectory() as directory:
            self.assets(Path(directory))
            run, web = self.services(ci, service)
            with run, web, mock.patch.dict(os.environ, {"GITHUB_SHA": "a" * 40}):
                ci.publish("owner/kernel", SHA, Path(directory))
            # No Telegram secrets or ENABLE_TELEGRAM: the release still publishes,
            # but no Telegram request may be made.
            self.assertEqual(service.events, ["create", "upload", "edit"])
            self.assertFalse(service.release["draft"])


if __name__ == "__main__":
    unittest.main()
