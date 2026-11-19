#!/usr/bin/env bash
# Build an AnyKernel3 zip for cancro.
#   package.sh <ksu-sha>              full package (plain zImage)
#   package.sh --template [<ksu-sha>] installer only; drop in your own zImage
set -euo pipefail
CI=$(cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(cd -- "$CI/../.." && pwd)
source "$CI/build.conf"

TEMPLATE=0
if [[ "${1:-}" == --template ]]; then
  TEMPLATE=1
  shift
fi
SHA=${1:-}
if [[ $TEMPLATE -eq 0 ]]; then
  [[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Provide a full KernelSU SHA, or --template" >&2; exit 2; }
fi

OUT="$ROOT/out/build/out"
DIST="$ROOT/out/dist"
mkdir -p "$DIST"

AK3="$ROOT/out/build/AnyKernel3"
if [[ ! -d "$AK3/.git" ]]; then
  git clone --depth=1 --no-checkout "$ANYKERNEL_REPO" "$AK3"
  git -C "$AK3" fetch --depth=1 origin "$ANYKERNEL_SHA"
  git -C "$AK3" checkout --detach "$ANYKERNEL_SHA"
fi
[[ $(git -C "$AK3" rev-parse HEAD) == "$ANYKERNEL_SHA" ]] || {
  echo "AnyKernel3 checkout does not match the pinned commit" >&2; exit 1
}

STAGE=$(mktemp -d "$ROOT/out/build/anykernel3.XXXXXX")
cleanup() { [[ "$STAGE" == "$ROOT/out/build/anykernel3."* ]] && rm -rf -- "$STAGE"; }
trap cleanup EXIT

# Ship the pinned AK3 runtime, minus repo metadata and placeholders.
(tar --exclude=.git --exclude=.github --exclude='*.l2s.*' --exclude='*placeholder' --exclude=README.md -C "$AK3" -cf - . | tar -C "$STAGE" -xf -)
cp "$CI/anykernel/anykernel.sh" "$STAGE/anykernel.sh"
cp "$ROOT/COPYING" "$STAGE/LICENSE.kernel"

if [[ $TEMPLATE -eq 0 ]]; then
  python3 "$CI/verify.py" build "$OUT"
  # Ship the plain zImage: AK3 reuses the dtb it split out of the device's own
  # boot.img. Adding zImage-dtb here would append a second set of DTBs and the
  # bootloader rejects the image (observed: immediate reboot to fastboot).
  cp "$OUT/arch/arm/boot/zImage" "$STAGE/zImage"
  cp "$OUT/.config" "$STAGE/kernel.config"
  cp "$OUT/build-info.json" "$STAGE/build-info.json"
  test -f "$ROOT/out/ksu/kernel/LICENSE" && cp "$ROOT/out/ksu/kernel/LICENSE" "$STAGE/LICENSE.kernelsu"
  NAME="cancro-kernelsu-${SHA:0:12}.zip"
else
  # No kernel, no module: the user supplies both.
  NAME="cancro-kernelsu-template.zip"
fi

fresh_zip() {
  local stage=$1 destination=$2 new
  new="$stage/.flash-new.zip"
  (cd "$stage" && zip -r9 "$new" . -x '*placeholder' -x .flash-new.zip)
  python3 "$CI/verify.py" zip "$new" "$SHA" $([[ $TEMPLATE -eq 1 ]] && echo --template)
  mv -Tf -- "$new" "$destination"
}
fresh_zip "$STAGE" "$DIST/$NAME"

if [[ $TEMPLATE -eq 0 ]]; then
  cp "$OUT/build-info.json" "$DIST/build-info.json"
  cp "$OUT/.config" "$DIST/kernel.config"
  (cd "$DIST" && sha256sum "$NAME" build-info.json kernel.config > SHA256SUMS)
  printf 'Flash ZIP: %s\n' "$DIST/$NAME"
else
  (cd "$DIST" && sha256sum "$NAME" > "$NAME.sha256")
  printf 'Template ZIP: %s\n' "$DIST/$NAME"
  printf 'Drop zImage (plain, no appended DTB) in the zip root, then re-zip.\n'
fi
