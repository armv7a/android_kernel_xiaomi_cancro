#!/usr/bin/env python3
"""Check build identity/format and the required AnyKernel payload, not hardware bootability."""
import json
import hashlib
import tempfile
from pathlib import Path
import struct
import re
import subprocess
import sys
import zipfile

REQUIRED = {"zImage", "anykernel.sh", "tools/ak3-core.sh", "tools/magiskboot", "tools/busybox", "META-INF/com/google/android/update-binary", "META-INF/com/google/android/updater-script", "LICENSE.kernel"}
TEMPLATE_REQUIRED = {"anykernel.sh", "tools/ak3-core.sh", "tools/magiskboot", "tools/busybox", "META-INF/com/google/android/update-binary", "META-INF/com/google/android/updater-script", "LICENSE.kernel"}


def verify_image(image, base):
    if len(base) < 48 or struct.unpack_from("<I", base, 36)[0] != 0x016F2818 or not image.startswith(base):
        raise ValueError("Invalid ARM zImage or appended image prefix")
    remaining, count, mi4 = image[len(base):], 0, False
    while remaining:
        if len(remaining) < 40 or remaining[:4] != bytes.fromhex("d00dfeed"):
            raise ValueError("Invalid appended DTB header")
        size = struct.unpack_from(">I", remaining, 4)[0]
        if size < 40 or size > len(remaining):
            raise ValueError("Invalid appended DTB size")
        mi4 |= b"Mi 4 (W)CDMA/LTE" in remaining[:size]
        count += 1
        remaining = remaining[size:]
    if count != 4 or not mi4:
        raise ValueError("Expected four upstream DTBs including Mi4")
    return count


def verify_arm_elf(data, elf_type):
    if len(data) < 20 or data[:6] != b"\x7fELF\x01\x01" or struct.unpack_from("<HH", data, 16) != (elf_type, 40):
        raise ValueError("Payload is not the expected 32-bit ARM ELF")


def verify_packed_image(image):
    """Accept a plain zImage; validate appended DTBs only when the payload has them.

    The flash package ships the plain zImage on purpose: AK3 reuses the dtb it
    split from the device's own boot.img. Bundling zImage-dtb appends a second
    DTB set and the bootloader rejects the result.
    """
    if len(image) < 48:
        raise ValueError("Truncated ARM zImage")
    magic, start, end = struct.unpack_from("<III", image, 36)
    size = end - start
    if magic != 0x016F2818 or size < 48 or size > len(image):
        raise ValueError("Invalid ARM zImage length")
    base = image[:size]
    if not image.startswith(base):
        raise ValueError("Invalid ARM zImage prefix")
    payload = image[size:]
    if not payload:
        return 0
    return verify_image(image, base)


def verify_module(module, release):
    info = subprocess.check_output(["readelf", "-p", ".modinfo", str(module)], text=True)
    if "vermagic=" + release + " " not in info:
        raise ValueError("WLAN module vermagic does not match the built kernel")


def verify_build(out):
    config = (out / ".config").read_text().splitlines()
    for setting in ["CONFIG_KSU=y", "CONFIG_KSU_TAMPER_SYSCALL_TABLE=y", "CONFIG_KSU_LSM_SECURITY_HOOKS=y", "CONFIG_SECURITY_SELINUX=y", "CONFIG_PRONTO_WLAN=y"]:
        if setting not in config:
            raise ValueError("Required config missing: " + setting)
    with (out / "vmlinux").open("rb") as stream:
        header = stream.read(20)
    verify_arm_elf(header, 2)
    symbols = subprocess.check_output(["readelf", "-Ws", str(out / "vmlinux")], text=True)
    names = {line.split()[-1] for line in symbols.splitlines() if line.split()}
    if not {"kernelsu_init", "hook_armeabi_reboot"} <= names:
        raise ValueError("KernelSU built-in/ARM syscall hook symbols are missing")
    release = re.search(r'"([^"\n]+)"', (out / "include/generated/utsrelease.h").read_text()).group(1)
    boot = out / "arch/arm/boot"
    return verify_packed_image((boot / "zImage").read_bytes())


def validate_zip(path, sha=None, template=False):
    required = TEMPLATE_REQUIRED if template else REQUIRED
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if sum(info.file_size for info in archive.infolist()) > 128 * 1024 * 1024:
            raise ValueError("AnyKernel ZIP declares an unsafe expansion size")
        if not template and ("zImage-dtb" in names or any(name == "Image" or name.startswith("Image.") or name.startswith("Image-") for name in names)):
            raise ValueError("Alternate kernel entry may override the verified zImage")
        if not required <= names:
            raise ValueError("AnyKernel ZIP missing: " + ", ".join(sorted(required - names)))
        if any(name.startswith("/") or ".." in Path(name).parts or ".git" in Path(name).parts for name in names):
            raise ValueError("Unsafe or repository-only ZIP entry")
        if any(archive.getinfo(name).file_size == 0 for name in required) or archive.testzip() is not None:
            raise ValueError("Empty or corrupt AnyKernel payload")
        if template:
            return {"template": True}
        info = json.loads(archive.read("build-info.json"))
        if not isinstance(info, dict):
            raise ValueError("Invalid build metadata")
        if sha is not None and info.get("ksu_sha") != sha:
            raise ValueError("ZIP does not match detected KernelSU SHA")
        image = archive.read("zImage")
        verify_packed_image(image)
        release = info.get("kernel_release")
        if not isinstance(release, str) or not release.startswith("3.4.113"):
            raise ValueError("ZIP kernel release is missing or mismatched")
        if info.get("zimage_sha256") != hashlib.sha256(image).hexdigest():
            raise ValueError("ZIP payload differs from verified build output: zimage_sha256")
        return info


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "build":
            print("Verified built-in ARM KernelSU and plain zImage")
        elif len(sys.argv) >= 4 and sys.argv[1] == "zip":
            template = "--template" in sys.argv[4:]
            validate_zip(Path(sys.argv[2]), sys.argv[3] or None, template=template)
            print("Verified AnyKernel3 template ZIP" if template else "Verified AnyKernel3 ZIP and pinned KernelSU SHA")
        else:
            raise ValueError("Usage: verify.py build OUT | zip ZIP SHA")
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error))
