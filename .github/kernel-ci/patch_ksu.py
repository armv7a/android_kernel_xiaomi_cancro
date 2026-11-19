#!/usr/bin/env python3
"""Apply the minimal old-kernel compatibility edits to a KernelSU checkout.

Upstream renames helpers and reshuffles these files often, so the edits key on
stable identifiers (the armeabi_execveat call and the version guard) instead of
surrounding lines. Running it twice is a no-op, and it fails loudly rather than
silently shipping an unguarded build.
"""
from pathlib import Path
import sys

ARM_HOOK = "kernel/hook/syscall_table_hook_arm.c"
ARM_GUARD = "&& __ARMEABI_execveat < __NR_syscalls"
VERSION_IF = "#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)"
KSU_MAKEFILE = "kernel/Makefile"
INLINE_FLAG = "CFLAGS_ksu.o += $(call cc-option, -fgnu89-inline)"


def guard_arm_execveat(root):
    """Skip slot 387 when the kernel's __NR_syscalls table is shorter than that.

    Linux 3.10 has 384 entries; reading or replacing index 387 is out of bounds.
    """
    path = root / ARM_HOOK
    if not path.is_file():
        raise SystemExit("Missing " + ARM_HOOK + "; refusing an unchecked build")
    lines = path.read_text().splitlines(keepends=True)
    guarded = 0
    for index, line in enumerate(lines):
        if not line.startswith(VERSION_IF) or ARM_GUARD in line:
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if "armeabi_execveat" in following:
            lines[index] = VERSION_IF + " " + ARM_GUARD + "\n"
            guarded += 1
    path.write_text("".join(lines))
    if guarded == 0 and ARM_GUARD not in path.read_text():
        raise SystemExit("Could not guard armeabi_execveat in " + ARM_HOOK + "; refusing an unchecked build")


def allow_gnu11_inline(root):
    """GNU11 inline semantics collide with the extern inline helpers in 3.10 headers."""
    path = root / KSU_MAKEFILE
    if not path.is_file():
        raise SystemExit("Missing " + KSU_MAKEFILE + "; refusing an unchecked build")
    text = path.read_text()
    if INLINE_FLAG in text:
        return
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n# Linux 3.10 headers need GNU89 extern-inline behaviour.\n" + INLINE_FLAG + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch_ksu.py KERNELSU_DIRECTORY")
    checkout = Path(sys.argv[1])
    guard_arm_execveat(checkout)
    allow_gnu11_inline(checkout)
