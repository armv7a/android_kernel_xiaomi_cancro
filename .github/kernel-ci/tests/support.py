import json
import hashlib
from pathlib import Path
import struct
import zipfile

RELEASE = "3.4.113-Fixture"
FILES = ["zImage", "anykernel.sh", "tools/ak3-core.sh", "tools/magiskboot", "tools/busybox", "META-INF/com/google/android/update-binary", "META-INF/com/google/android/updater-script", "LICENSE.kernel", "build-info.json", "kernel.config"]


def payloads(sha):
    base = bytearray(64)
    struct.pack_into("<III", base, 36, 0x016F2818, 0, 64)
    model = b"Mi 4 (W)CDMA/LTE"
    dtb = struct.pack(">II", 0xD00DFEED, 40 + len(model)) + bytes(32) + model
    result = {name: b"fixture" for name in FILES}
    result["zImage"] = bytes(base) + dtb * 4
    result["build-info.json"] = json.dumps({"ksu_sha": sha, "kernel_release": RELEASE}).encode()
    info = json.loads(result["build-info.json"])
    info["zimage_sha256"] = hashlib.sha256(result["zImage"]).hexdigest()
    result["build-info.json"] = json.dumps(info).encode()
    result["kernel.config"] = b"CONFIG_KSU=y\nCONFIG_KSU_TAMPER_SYSCALL_TABLE=y\nCONFIG_KSU_LSM_SECURITY_HOOKS=y\nCONFIG_SECURITY_SELINUX=y\nCONFIG_PRONTO_WLAN=y\n"
    return result


def write_zip(path, sha, omitted=None, replacements=None):
    data = payloads(sha)
    data.update(replacements or {})
    info = json.loads(data["build-info.json"])
    info["zimage_sha256"] = hashlib.sha256(data["zImage"]).hexdigest()
    data["build-info.json"] = json.dumps(info).encode()
    with zipfile.ZipFile(path, "w") as stream:
        for name, content in data.items():
            if name != omitted:
                stream.writestr(name, content)
