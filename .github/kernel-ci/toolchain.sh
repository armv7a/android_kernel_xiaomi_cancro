#!/usr/bin/env bash
set -euo pipefail

checked_deb() {
  local url=$1 name=$2 hash=$3
  if [[ ! -f "$DEBDIR/$name" ]]; then
    curl -fL --connect-timeout 15 --max-time 180 --retry 2 "$url" -o "$DEBDIR/$name"
  fi
  printf '%s  %s\n' "$hash" "$DEBDIR/$name" | sha256sum -c -
  dpkg-deb -x "$DEBDIR/$name" "$TOOLCHAIN"
}

setup_toolchain() {
  local arch base libdir gcc_hash cpp_hash mpfr_hash binutils tool name
  case $(uname -m) in
    aarch64)
      arch=arm64; base=https://ports.ubuntu.com/ubuntu-ports; libdir=aarch64-linux-gnu
      gcc_hash=09398b75f7b9f7480f2275f72cfb3fd2b0eae42a2a2e24d6cb93b7a89532295b
      cpp_hash=1c2d68b089ffb4394fcc40ec0034edfce51a4cbb52cb45529656f9a59ad64f10
      mpfr_hash=1f090ec5909ccefda114649e4c86dba233750f9337769939567e93110af7343d ;;
    x86_64)
      arch=amd64; base=https://archive.ubuntu.com/ubuntu; libdir=x86_64-linux-gnu
      gcc_hash=9545c1197a8507f45bd8cee8ad33f1574c645c720df506f8e3a62473dcef0118
      cpp_hash=4e50334f685f28a3a56e01fb2d68239667df496b9eaaeac3e0bcbf1576677f7b
      mpfr_hash=3973a97387bbe0e8a775995c22861d8478edee2a594e8117970f635de25b2c8a ;;
    *) echo "GCC 4.9 toolchain supports Linux ARM64/x86_64 hosts only" >&2; return 1 ;;
  esac
  TOOLCHAIN="$ROOT/toolchains/gcc49"
  DEBDIR="$ROOT/toolchains/gcc49-debs"
  mkdir -p "$TOOLCHAIN" "$DEBDIR"
  name="gcc-4.9-arm-linux-gnueabi_4.9.3-13ubuntu2cross1_$arch.deb"
  checked_deb "$base/pool/universe/g/gcc-4.9-cross/$name" "$name" "$gcc_hash"
  name="cpp-4.9-arm-linux-gnueabi_4.9.3-13ubuntu2cross1_$arch.deb"
  checked_deb "$base/pool/universe/g/gcc-4.9-cross/$name" "$name" "$cpp_hash"
  name=libgcc-4.9-dev-armel-cross_4.9.3-13ubuntu2cross1_all.deb
  checked_deb "$base/pool/universe/g/gcc-4.9-cross/$name" "$name" 879afb840e53b6528fad28289890d330fc8cc09f576d804bbc53dd7cad0d1005
  name="libmpfr4_3.1.4-1_$arch.deb"
  checked_deb "$base/pool/main/m/mpfr4/$name" "$name" "$mpfr_hash"
  binutils=$(command -v arm-linux-gnueabi-as || command -v arm-linux-gnueabihf-as)
  binutils=${binutils%as}
  mkdir -p "$TOOLCHAIN/usr/arm-linux-gnueabi/bin"
  ln -sf arm-linux-gnueabi-gcc-4.9 "$TOOLCHAIN/usr/bin/arm-linux-gnueabi-gcc"
  for tool in as ld ar nm objcopy objdump strip readelf size strings ranlib addr2line; do
    test -x "$binutils$tool"
    ln -sf "$binutils$tool" "$TOOLCHAIN/usr/arm-linux-gnueabi/bin/$tool"
    ln -sf "$binutils$tool" "$TOOLCHAIN/usr/bin/arm-linux-gnueabi-$tool"
  done
  export LD_LIBRARY_PATH="$TOOLCHAIN/usr/lib/$libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export PATH="$TOOLCHAIN/usr/bin:$PATH"
  export CROSS_COMPILE="$TOOLCHAIN/usr/bin/arm-linux-gnueabi-"
  [[ $("${CROSS_COMPILE}gcc" -dumpversion) == 4.9.3 ]]
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
  setup_toolchain
  printf 'CROSS_COMPILE=%s\n' "$CROSS_COMPILE"
fi
