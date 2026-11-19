import importlib.util
import json
import os
import re
from pathlib import Path
import struct
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from support import FILES, RELEASE, payloads, write_zip

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = FILES
SHA = "88867e52ab4c4e754f4155674ba29a9f1092a875"


class ArtifactTests(unittest.TestCase):
    def module(self):
        path = ROOT / "verify.py"
        self.assertTrue(path.exists(), "artifact verifier is missing")
        spec = importlib.util.spec_from_file_location("verify", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_packager_rejects_missing_build_before_creating_zip(self):
        script = ROOT / "package.sh"
        self.assertTrue(script.is_file(), "packager is missing")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copyfile(script, root / "scripts/package.sh")
            shutil.copyfile(ROOT / "verify.py", root / "scripts/verify.py")
            shutil.copyfile(ROOT / "build.conf", root / "build.conf")
            result = subprocess.run(["bash", str(root / "scripts/package.sh"), SHA], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(root.glob("dist/*.zip")), [])

    def test_dtb_append_requires_valid_lengths_and_mi4_payload(self):
        verify = self.module()
        base = bytearray(64)
        struct.pack_into("<I", base, 36, 0x016F2818)
        model = b"Mi 4 (W)CDMA/LTE"
        dtb = struct.pack(">II", 0xD00DFEED, 40 + len(model)) + bytes(32) + model
        self.assertEqual(verify.verify_image(bytes(base) + dtb * 4, bytes(base)), 4)
        for bad in [bytes(base), bytes(base) + dtb[:12], bytes(base) + struct.pack(">II", 0xD00DFEED, 400) + b"Mi4", bytes(base) + dtb.replace(b"Mi 4", b"Mi 3") * 4]:
            with self.subTest(length=len(bad)), self.assertRaises(ValueError):
                verify.verify_image(bad, bytes(base))

    def test_zip_rejects_unbounded_declared_uncompressed_payload(self):
        verify = self.module()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "kernel.zip"
            write_zip(archive, SHA)
            data = bytearray(archive.read_bytes())
            central = data.index(b"PK\x01\x02")
            struct.pack_into("<I", data, central + 24, 300_000_000)
            archive.write_bytes(data)
            with self.assertRaises(ValueError):
                verify.validate_zip(archive, SHA)

    def test_zip_rejects_wrong_arch_dtb_or_unpaired_wlan_payload(self):
        verify = self.module()
        cases = [{"zImage": b"not an ARM kernel"}, {"zImage-dtb": payloads(SHA)["zImage"][:64]}, {"build-info.json": json.dumps({"ksu_sha": SHA, "kernel_release": "3.10.84-Wrong"}).encode()}]
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "kernel.zip"
            for replacements in cases:
                with self.subTest(replacements=list(replacements)):
                    write_zip(archive, SHA, replacements=replacements)
                    with self.assertRaises(ValueError):
                        verify.validate_zip(archive, SHA)
            for missing in ["LICENSE.kernel", "tools/ak3-core.sh"]:
                write_zip(archive, SHA, omitted=missing)
                with self.subTest(missing=missing), self.assertRaises(ValueError):
                    verify.validate_zip(archive, SHA)

    def test_zip_rejects_alternate_kernel_that_ak3_would_prefer(self):
        verify = self.module()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "kernel.zip"
            write_zip(archive, SHA)
            with zipfile.ZipFile(archive, "a") as stream:
                stream.writestr("zImage-dtb", b"old kernel")
            with self.assertRaises(ValueError):
                verify.validate_zip(archive, SHA)

    def test_repack_removes_stale_old_kernel_entries(self):
        verify = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name, content in payloads(SHA).items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            output = root / "kernel.zip"
            write_zip(output, SHA)
            with zipfile.ZipFile(output, "a") as archive:
                archive.writestr("zImage", b"stale old kernel")
            script = (ROOT / "package.sh").read_text()
            function = re.search(r"^fresh_zip\(\) \{.*?^\}", script, re.M | re.S)
            if function is None:
                result = subprocess.run(["zip", "-qr9", str(output), "."], cwd=stage, capture_output=True, text=True)
            else:
                result = subprocess.run(["bash", "-c", function.group() + '\nfresh_zip "$1" "$2"', "bash", str(stage), str(output)], env={**os.environ, "ROOT": str(ROOT), "SHA": SHA}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with zipfile.ZipFile(output) as archive:
                self.assertNotIn("zImage-dtb", archive.namelist())
            verify.validate_zip(output, SHA)

    def test_zip_requires_payload_installer_and_licenses(self):
        verify = self.module()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "kernel.zip"
            for omitted in [None, "LICENSE.kernel", "tools/magiskboot", "META-INF/com/google/android/update-binary"]:
                with self.subTest(omitted=omitted):
                    write_zip(archive, SHA, omitted=omitted)
                    if omitted is None:
                        verify.validate_zip(archive, SHA)
                        with self.assertRaises(ValueError):
                            verify.validate_zip(archive, "a" * 40)
                    else:
                        with self.assertRaises(ValueError):
                            verify.validate_zip(archive, SHA)


if __name__ == "__main__":
    unittest.main()
