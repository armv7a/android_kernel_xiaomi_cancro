#!/usr/bin/env bash
# Builds THIS kernel tree with a pinned KernelSU commit. Integration edits the
# working tree only; the repository history and tracked sources stay untouched.
set -euo pipefail
CI=$(cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(cd -- "$CI/../.." && pwd)
source "$CI/build.conf"
SHA=${1:-}
DEBUG=${2:-}
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Provide a full lowercase KernelSU SHA" >&2; exit 2; }
[[ -z "$DEBUG" || "$DEBUG" == --debug || "$DEBUG" == --no-ksu ]] || { echo "Second argument must be --debug or --no-ksu" >&2; exit 2; }
source "$CI/toolchain.sh"
setup_toolchain
KERNEL_SHA=$(git -C "$ROOT" rev-parse HEAD)
OUT="$ROOT/out/build/out"
KSU="$ROOT/out/ksu"
mkdir -p "$OUT" "$ROOT/out/build/logs"

apply_patches() {
  local directory=$1 group=$2 patch
  for patch in "$CI/patches/$group/"*.patch; do
    [[ -f "$patch" ]] || continue
    if git -C "$directory" apply --check "$patch" >/dev/null 2>&1; then
      git -C "$directory" apply "$patch"
    else
      git -C "$directory" apply --reverse --check "$patch" || { echo "Patch neither applies nor is applied: $patch" >&2; return 1; }
    fi
  done
}

apply_patches "$ROOT" kernel
if [[ ! -d "$KSU/.git" ]]; then
  git clone --depth=1 --no-checkout --filter=blob:none "$KSU_REPO" "$KSU"
fi
git -C "$KSU" fetch --depth=1 origin "$SHA"
# Discard edits from an earlier build so re-runs start from pristine upstream.
git -C "$KSU" checkout --force --detach "$SHA"
git -C "$KSU" reset --hard --quiet "$SHA"
git -C "$KSU" clean -qfd
[[ $(git -C "$KSU" rev-parse HEAD) == "$SHA" ]] || { echo "KernelSU SHA mismatch" >&2; exit 1; }
python3 "$CI/patch_ksu.py" "$KSU"
python3 "$CI/prepare.py" "$ROOT" "$KSU"

make_args=(-C "$ROOT" O="$OUT" ARCH=arm CROSS_COMPILE="$CROSS_COMPILE")
[[ -f "$OUT/.config" ]] || make "${make_args[@]}" "$DEFCONFIG"
python3 "$CI/prepare.py" "$ROOT" "$KSU" "$OUT/.config"
if [[ "$DEBUG" == --no-ksu ]]; then
  # Diagnostic: build the same tree without KernelSU to prove whether the
  # kernel itself boots. A first-screen hang here means the kernel, not KSU.
  "$ROOT/scripts/config" --file "$OUT/.config" --disable KSU
  echo "KernelSU disabled: diagnostic build of the plain kernel." >&2
fi
if [[ "$DEBUG" == --debug ]]; then
  # Verbose early console so a first-screen hang produces real output.
  "$ROOT/scripts/config" --file "$OUT/.config" --enable DEBUG_KERNEL --enable DEBUG_LL \
    --enable DEBUG_LL_UART_NONE --enable EARLY_PRINTK \
    --enable MSM_HAS_DEBUG_UART_HS
  echo "Early printk enabled: boot with earlyprintk appended to the kernel cmdline." >&2
fi
make "${make_args[@]}" oldnoconfig
make "${make_args[@]}" -j"${JOBS:-2}" zImage 2>&1 | tee "$ROOT/out/build/logs/build.log"
python3 "$CI/verify.py" build "$OUT"
python3 - "$ROOT" "$CI" "$KERNEL_SHA" "$SHA" "$ANYKERNEL_SHA" "$CROSS_COMPILE" <<'PY'
import datetime, hashlib, json, pathlib, re, subprocess, sys
root, ci, kernel_sha, ksu_sha, ak3_sha, cross = sys.argv[1:]
out = pathlib.Path(root, "out/build/out")
info = {"device": "cancro", "rom": "MoKee Android 10", "arch": "arm",
        "kernel_sha": kernel_sha, "ksu_sha": ksu_sha, "anykernel_sha": ak3_sha,
        "compiler": subprocess.check_output([cross + "gcc", "--version"], text=True).splitlines()[0],
        "binutils": subprocess.check_output([cross + "ld", "--version"], text=True).splitlines()[0],
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
info["kernel_release"] = re.search(r'"([^"\n]+)"', (out / "include/generated/utsrelease.h").read_text()).group(1)
info["zimage_sha256"] = hashlib.sha256((out / "arch/arm/boot/zImage").read_bytes()).hexdigest()
(out / "build-info.json").write_text(json.dumps(info, indent=2) + "\n")
PY
