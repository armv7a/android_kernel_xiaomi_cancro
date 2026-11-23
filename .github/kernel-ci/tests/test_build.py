import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BuildTests(unittest.TestCase):
    def test_ci_locks_kernelsu_branch_and_anykernel_inputs(self):
        self.assertTrue((ROOT / "build.conf").is_file(), "version lock is missing")
        self.assertIn("KSU_REPO=https://github.com/rsuntk/KernelSU.git", (ROOT / "build.conf").read_text())
        result = subprocess.run(["bash", "-c", '. "$1"; printf "%s\n" "$KSU_BRANCH" "$ANYKERNEL_SHA" "$DEFCONFIG"', "bash", str(ROOT / "build.conf")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["master", "41f04bdb4ee30a44e429c2c1131d5e8bb32b15f1", "virgo_defconfig"])

    def test_malformed_sha_fails_before_checkout(self):
        for sha in ["bad", "a" * 39, "a" * 41, "a" * 39 + ";", "-help"]:
            with self.subTest(sha=sha):
                result = subprocess.run(["bash", str(ROOT / "build.sh"), sha], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("SHA", result.stderr)

    def test_kernelsu_checkout_pins_detected_sha_not_remote_head(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "origin"
            source.mkdir()
            env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}

            def git(*args):
                return subprocess.check_output(["git", "-C", str(source), *args], env=env, stderr=subprocess.DEVNULL, text=True).strip()

            git("init", "-b", "master")
            (source / "value").write_text("pinned")
            git("add", "value"); git("commit", "-m", "pinned")
            pinned = git("rev-parse", "HEAD")
            (source / "value").write_text("new remote head")
            git("commit", "-am", "advance")
            clone = root / "ksu"
            subprocess.check_call(["git", "clone", "--depth=1", "--no-checkout", "--filter=blob:none", source.as_uri(), str(clone)], env=env, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(clone), "fetch", "--depth=1", "origin", pinned], check=False, env=env, capture_output=True)
            subprocess.check_call(["git", "-C", str(clone), "checkout", "--detach", pinned], env=env)
            self.assertEqual(subprocess.check_output(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True).strip(), pinned)
            self.assertEqual((clone / "value").read_text(), "pinned")
            script = (ROOT / "build.sh").read_text()
            self.assertIn('git -C "$KSU" fetch --depth=1 origin "$SHA"', script)
            self.assertIn('[[ $(git -C "$KSU" rev-parse HEAD) == "$SHA" ]]', script)

    def test_arm_syscalls_never_access_absent_execveat(self):
        fixture = ROOT / "tests/fixtures/syscall_table_hook_arm.c"
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "kernel/hook/syscall_table_hook_arm.c"
            source.parent.mkdir(parents=True)
            shutil.copyfile(fixture, source)
            (work / "kernel/Makefile").write_text("kernelsu-y := ksu.o\n")
            (work / "kernel/kernel_includes.h").write_text("#include <linux/printk.h>\n")
            result = subprocess.run(["python3", str(ROOT / "patch_ksu.py"), str(work)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = source.read_text()
            enable = re.search(r"static void syscall_table_sucompat_enable\(\)\n\{.*?\n\}", text, re.S).group()
            disable = re.search(r"static void syscall_table_sucompat_disable\(\)\n\{.*?\n\}", text, re.S).group()
            macros = "\n".join(re.findall(r"^#define __ARMEABI_.*$", text, re.M))
            for count, present, expected in [(384, True, ["11", "334", "327"]), (512, True, ["11", "387", "334", "327"]), (384, False, ["11", "334", "327"])]:
                with self.subTest(count=count, present=present):
                    code = '#include <stdio.h>\n#define KERNEL_VERSION(a,b,c) (((a)<<16)+((b)<<8)+(c))\n#define LINUX_VERSION_CODE KERNEL_VERSION(3,10,84)\n#define guarded_mutex_lock(...) ((void)0)\n#define replace_syscall(a,n,b,t) printf("%d\\n", n)\n#define read_and_replace_syscall(a,n,b,t) printf("%d\\n", n)\n#define restore_syscall(a,n,b,t) printf("%d\\n", n)\n'
                    code += f"#define __NR_syscalls {count}\n" + ("#define __NR_execveat 387\n" if present else "")
                    code += macros + "\n" + enable + "\n" + disable + "\nint main(void) { syscall_table_sucompat_enable(); syscall_table_sucompat_disable(); }\n"
                    (work / "hook.c").write_text(code)
                    result = subprocess.run(["gcc", str(work / "hook.c"), "-o", str(work / "hook")], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run([str(work / "hook")], capture_output=True, text=True)
                    self.assertEqual(result.stdout.splitlines(), expected + expected)


class InlineCompatibilityTests(unittest.TestCase):
    def test_gnu11_ksu_preserves_old_kernel_extern_inline_linkage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kernel").mkdir()
            (root / "kernel/hook").mkdir()
            (root / "kernel/hook/syscall_table_hook_arm.c").write_text("#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n\treplace_syscall(&armeabi_execveat);\n#endif\n")
            shutil.copyfile(ROOT / "tests/fixtures/ksu-inline.make", root / "kernel/Makefile")
            (root / "kernel/kernel_includes.h").write_text("#include <linux/printk.h>\n")
            shutil.copyfile(ROOT / "tests/fixtures/ftrace.h", root / "ftrace.h")
            result = subprocess.run(["python3", str(ROOT / "patch_ksu.py"), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            (root / "Makefile").write_text('CC = gcc\ncc-option = $(shell $(CC) $(1) -Werror -x c -c /dev/null -o /dev/null >/dev/null 2>&1 && printf "%s" "$(1)")\ninclude kernel/Makefile\nprint:\n\t@printf "%s" "$(CFLAGS_ksu.o)"\n')
            flags = subprocess.check_output(["make", "--no-print-directory", "print"], cwd=root, text=True).split()
            (root / "arch.c").write_text('void *return_address(unsigned int n) { return (void *)0; }\n')
            (root / "ksu.c").write_text('#include <stddef.h>\n#include "ftrace.h"\nint main(void) { return return_address(0) != NULL; }\n')
            subprocess.check_call(["gcc", "-std=gnu89", "-c", "arch.c", "-o", "arch.o"], cwd=root)
            result = subprocess.run(["gcc", *flags, "ksu.c", "arch.o", "-o", "program"], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(subprocess.run([str(root / "program")]).returncode, 0)




class RebuildTests(unittest.TestCase):
    def test_kernelsu_checkout_resets_previous_patch_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "origin"
            source.mkdir()
            env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}

            def git(*args, cwd=source):
                return subprocess.check_output(["git", "-C", str(cwd), *args], env=env, stderr=subprocess.DEVNULL, text=True).strip()

            git("init", "-b", "master")
            (source / "kernel").mkdir()
            (source / "kernel/Makefile").write_text("kernelsu-y := ksu.o\n")
            (source / "kernel/hook").mkdir()
            (source / "kernel/hook/syscall_table_hook_arm.c").write_text("#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n\treplace_syscall(&armeabi_execveat);\n#endif\n")
            git("add", "."); git("commit", "-m", "pinned")
            pinned = git("rev-parse", "HEAD")

            script = (ROOT / "build.sh").read_text()
            checkout_block = re.search(r"^if \[\[ ! -d \"\$KSU/\.git\" \]\]; then.*?^fi$", script, re.M | re.S)
            self.assertIsNotNone(checkout_block, "KSU checkout block not found")
            self.assertIn("--force", script, "checkout must discard prior patch edits")
            self.assertIn("git -C \"$KSU\" reset --hard", script, "working tree must be reset before patch_ksu runs")
            self.assertIn("git -C \"$KSU\" clean -qfd", script, "untracked build files must not leak into the next build")

            clone = root / "ksu"
            subprocess.check_call(["git", "clone", "-q", "--depth=1", "--no-checkout", source.as_uri(), str(clone)], env=env, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(clone), "checkout", "-q", "--detach", pinned], env=env)
            (clone / "kernel/Makefile").write_text("kernelsu-y := ksu.o\n\nCFLAGS_ksu.o += $(call cc-option, -fgnu89-inline)\n")
            subprocess.check_call(["git", "-C", str(clone), "fetch", "-q", "--depth=1", "origin", pinned], env=env, capture_output_output=False) if False else subprocess.run(["git", "-C", str(clone), "fetch", "-q", "--depth=1", "origin", pinned], env=env, capture_output=True)
            reset = subprocess.run(["git", "-C", str(clone), "checkout", "-q", "--force", "--detach", pinned], env=env, capture_output=True, text=True)
            self.assertEqual(reset.returncode, 0, "forced checkout must discard local patch edits")
            self.assertNotIn("fgnu89-inline", (clone / "kernel/Makefile").read_text())


class KernelSUPatchTests(unittest.TestCase):
    def checkout(self, root, header):
        hook = root / "kernel/hook/syscall_table_hook_arm.c"
        hook.parent.mkdir(parents=True)
        hook.write_text(header)
        (root / "kernel").mkdir(exist_ok=True)
        (root / "kernel/Makefile").write_text("kernelsu-y := ksu.o\n")
        (root / "kernel/kernel_includes.h").write_text("#include <linux/printk.h>\n")
        return root

    def test_guards_absent_execveat_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.checkout(Path(directory), '#define __ARMEABI_execveat\t387\nstatic void enable()\n{\n#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n\treplace_syscall(&armeabi_execveat, 387, hook);\n#endif\n}\n')
            for _ in range(2):
                result = subprocess.run(["python3", str(ROOT / "patch_ksu.py"), str(root)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            text = (root / "kernel/hook/syscall_table_hook_arm.c").read_text()
            self.assertEqual(text.count("&& __ARMEABI_execveat < __NR_syscalls"), 1)
            self.assertIn("fgnu89-inline", (root / "kernel/Makefile").read_text())

    def test_survives_renamed_helper_and_multiple_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.checkout(Path(directory), 'static void enable()\n{\n#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n\trenamed_syscall(&armeabi_execveat);\n#endif\n}\nstatic void disable()\n{\n#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n\trestore_renamed(&armeabi_execveat);\n#endif\n}\n')
            result = subprocess.run(["python3", str(ROOT / "patch_ksu.py"), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "kernel/hook/syscall_table_hook_arm.c").read_text().count("&& __ARMEABI_execveat < __NR_syscalls"), 2)

    def test_refuses_when_execveat_cannot_be_found(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.checkout(Path(directory), 'static void untouched(void) { }\n')
            result = subprocess.run(["python3", str(ROOT / "patch_ksu.py"), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing", result.stderr)

    def test_leaves_non_execveat_guards_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.checkout(Path(directory), 'static void other(void)\n{\n#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n\tsomething(&armeabi_execveat);\n#endif\n\n#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n\treplace_syscall(&armeabi_faccessat);\n#endif\n}\n')
            result = subprocess.run(["python3", str(ROOT / "patch_ksu.py"), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = (root / "kernel/hook/syscall_table_hook_arm.c").read_text()
            self.assertEqual(text.count("&& __ARMEABI_execveat < __NR_syscalls"), 1)
            self.assertIn("#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 7, 0)\n\treplace_syscall(&armeabi_faccessat);", text)



class EarlyDebugTests(unittest.TestCase):
    def test_debug_flag_enables_only_symbols_this_kernel_defines(self):
        text = (ROOT / "build.sh").read_text()
        self.assertIn("--debug", text, "build.sh needs a debug mode for first-screen hangs")
        for symbol in ["DEBUG_LL", "DEBUG_LL_UART_NONE", "EARLY_PRINTK", "MSM_HAS_DEBUG_UART_HS"]:
            with self.subTest(symbol=symbol):
                self.assertIn("--enable " + symbol, text)
        self.assertNotIn("DEBUG_LL_UART_8250", text, "3.10 has no 8250 debug option")
        self.assertNotIn("DEBUG_UART_PHYS", text, "msm8974 supplies MSM_DEBUG_UART_PHYS from mach/msm_iomap-8974.h")

    def test_debug_build_keeps_normal_defaults_when_absent(self):
        text = (ROOT / "build.sh").read_text()
        self.assertIn('if [[ "$DEBUG" == --debug ]]', text, "debug must be opt-in")
        self.assertIn('Second argument must be --debug', text, "an unknown extra argument must be rejected")


class IntegrationTests(unittest.TestCase):
    def make_sources(self, root):
        kernel, ksu = root / "kernel", root / "ksu"
        (kernel / "drivers").mkdir(parents=True)
        (ksu / "kernel").mkdir(parents=True)
        (kernel / "drivers/Makefile").write_text("obj-y += existing/\n")
        (kernel / "drivers/Kconfig").write_text('menu "Drivers"\nendmenu\n')
        (ksu / "kernel/Kconfig").write_text('config KSU\n    bool "KernelSU"\n')
        return kernel, ksu

    def test_integration_is_idempotent_and_points_to_requested_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            kernel, ksu = self.make_sources(Path(directory))
            for _ in range(2):
                result = subprocess.run(["python3", str(ROOT / "prepare.py"), str(kernel), str(ksu)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((kernel / "drivers/kernelsu").resolve(), ksu / "kernel")
            self.assertEqual((kernel / "drivers/Makefile").read_text(), 'obj-y += existing/\n\nobj-$(CONFIG_KSU) += kernelsu/\n')
            self.assertEqual((kernel / "drivers/Kconfig").read_text(), 'menu "Drivers"\nendmenu\n\nsource "drivers/kernelsu/Kconfig"\n')

    def test_config_enables_builtin_arm_hooks_without_changing_wifi_or_selinux(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, ksu = self.make_sources(root)
            config = root / ".config"
            config.write_text("CONFIG_PRONTO_WLAN=y\nCONFIG_SECURITY_SELINUX=y\n# CONFIG_KSU is not set\nCONFIG_KSU_TAMPER_SYSCALL_TABLE=n\n")
            result = subprocess.run(["python3", str(ROOT / "prepare.py"), str(kernel), str(ksu), str(config)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = dict(line.split("=", 1) for line in config.read_text().splitlines() if "=" in line)
            self.assertEqual(values["CONFIG_KSU"], "y")
            self.assertEqual(values["CONFIG_KSU_TAMPER_SYSCALL_TABLE"], "y")
            self.assertEqual(values["CONFIG_KSU_LSM_SECURITY_HOOKS"], "y")
            self.assertEqual(values["CONFIG_KSU_KPROBES_KSUD"], "n")
            self.assertEqual(values["CONFIG_KSU_HACK_ARM64_BRANCH_LINK"], "n")
            self.assertEqual(values["CONFIG_PRONTO_WLAN"], "y")
            self.assertEqual(values["CONFIG_SECURITY_SELINUX"], "y")

    def test_conflicting_driver_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            kernel, ksu = self.make_sources(Path(directory))
            target = kernel / "drivers/kernelsu"
            target.write_text("unrelated data")
            result = subprocess.run(["python3", str(ROOT / "prepare.py"), str(kernel), str(ksu)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("occupied", result.stderr)
            self.assertEqual(target.read_text(), "unrelated data")
            self.assertEqual((kernel / "drivers/Makefile").read_text(), "obj-y += existing/\n")


class ToolchainTests(unittest.TestCase):
    def test_corrupt_cached_package_is_rejected_before_extraction(self):
        script = ROOT / "toolchain.sh"
        self.assertTrue(script.is_file(), "checked toolchain acquisition is missing")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.deb").write_bytes(b"corrupt download")
            result = subprocess.run(["bash", "-c", 'source "$1"; DEBDIR="$2"; TOOLCHAIN="$2/output"; checked_deb https://example.invalid/bad.deb bad.deb "$3"', "bash", str(script), str(root), "0" * 64], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
