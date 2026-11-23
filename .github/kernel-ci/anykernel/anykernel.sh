# SPDX-License-Identifier: GPL-2.0-only
# cancro / MoKee Android 10 - AnyKernel3 installer
properties() { '
kernel.string=Cancro KernelSU for MoKee Android 10
do.devicecheck=1
do.modules=0
do.systemless=0
do.cleanup=1
do.cleanuponabort=0
device.name1=cancro
supported.versions=10
supported.patchlevels=
supported.vendorpatchlevels=
'; }

BLOCK=boot
IS_SLOT_DEVICE=0
RAMDISK_COMPRESSION=auto
PATCH_VBMETA_FLAG=0
NO_MAGISK_CHECK=1
NO_VBMETA_PARTITION_PATCH=1
CUSTOMDD="bs=4096"

. tools/ak3-core.sh

# WLAN is built into this kernel (CONFIG_PRONTO_WLAN=y); the zip ships no
# separate kernel module, so no module replacement step is needed.

dump_boot        # split_boot + unpack_ramdisk
write_boot       # repack_ramdisk + flash_boot

ui_print "Done."
