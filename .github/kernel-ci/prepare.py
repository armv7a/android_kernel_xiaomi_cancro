#!/usr/bin/env python3
"""Connect a verified KernelSU checkout to the kernel without overwriting other drivers."""
import os
from pathlib import Path
import sys


def prepare(kernel, ksu, config=None):
    drivers = kernel / "drivers"
    source = (ksu / "kernel").resolve()
    if not (source / "Kconfig").is_file():
        raise SystemExit("KernelSU kernel/Kconfig is missing")
    link = drivers / "kernelsu"
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or link.resolve() != source:
            raise SystemExit("KernelSU driver path is occupied")
    else:
        link.symlink_to(os.path.relpath(source, drivers))
    for name, line in [("Makefile", 'obj-$(CONFIG_KSU) += kernelsu/'), ("Kconfig", 'source "drivers/kernelsu/Kconfig"')]:
        path = drivers / name
        text = path.read_text()
        if line not in text.splitlines():
            with path.open("a") as stream:
                stream.write("\n" + line + "\n")

    if config is not None:
        settings = {"CONFIG_KSU": "y", "CONFIG_KSU_TAMPER_SYSCALL_TABLE": "y", "CONFIG_KSU_LSM_SECURITY_HOOKS": "y", "CONFIG_KSU_KPROBES_KSUD": "n", "CONFIG_KSU_HACK_ARM64_BRANCH_LINK": "n"}
        lines = [line for line in config.read_text().splitlines() if line.split("=", 1)[0] not in settings and not any(line == f"# {name} is not set" for name in settings)]
        config.write_text("\n".join(lines + [f"{name}={value}" for name, value in settings.items()]) + "\n")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit("Usage: prepare.py KERNEL_DIRECTORY KERNELSU_DIRECTORY [CONFIG_FILE]")
    prepare(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) == 4 else None)
