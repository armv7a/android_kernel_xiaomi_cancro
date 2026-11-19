import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "anykernel/anykernel.sh"


class AnyKernel3Tests(unittest.TestCase):
    def text(self):
        return INSTALLER.read_text()

    def test_installer_parses(self):
        self.assertTrue(INSTALLER.is_file(), "installer is missing")
        result = subprocess.run(["sh", "-n", str(INSTALLER)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_device_gate_limits_flashing_to_cancro(self):
        text = self.text()
        self.assertIn("do.devicecheck=1", text)
        self.assertIn("device.name1=cancro", text)
        self.assertIn("supported.versions=10", text)

    def test_uses_native_boot_flow(self):
        text = self.text()
        self.assertIn("dump_boot", text, "dump_boot splits the image and unpacks the ramdisk")
        self.assertIn("write_boot", text, "write_boot repacks the ramdisk, builds and flashes")
        self.assertLess(text.index("dump_boot"), text.index("write_boot"))

    def test_wlan_is_builtin_and_requires_no_module_step(self):
        text = self.text()
        self.assertIn("CONFIG_PRONTO_WLAN=y", text, "WLAN is built into the kernel; no module replacement")
        self.assertNotIn("wlan.ko", text, "a builtin WLAN kernel must not reference an external module")

    def test_no_second_dtb_is_passed(self):
        text = self.text()
        self.assertNotIn("--dt ", text, "zImage-dtb already carries the appended DTBs")


if __name__ == "__main__":
    unittest.main()
