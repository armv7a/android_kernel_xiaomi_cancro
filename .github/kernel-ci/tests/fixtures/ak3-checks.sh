# Extracted unmodified from AnyKernel3 41f04bdb; GPL-2.0, see upstream LICENSE.
do_devicecheck() {
  [ "$(file_getprop anykernel.sh do.devicecheck)" == 1 ] || return 1;
  local device devicename match product testname vendordevice vendorproduct;
  ui_print "Checking device...";
  device=$(getprop ro.product.device 2>/dev/null);
  product=$(getprop ro.build.product 2>/dev/null);
  vendordevice=$(getprop ro.product.vendor.device 2>/dev/null);
  vendorproduct=$(getprop ro.vendor.product.device 2>/dev/null);
  for testname in $(grep '^device.name.*=' anykernel.sh | cut -d= -f2-); do
    for devicename in $device $product $vendordevice $vendorproduct; do
      if [ "$devicename" == "$testname" ]; then
        ui_print "$testname" " ";
        match=1;
        break 2;
      fi;
    done;
  done;
  if [ ! "$match" ]; then
    abort " " "Unsupported device. Aborting...";
  fi;
}
int2ver() {
  if [ "$1" -eq "$1" ] 2>/dev/null; then
    echo "$1.0.0";
  elif [ ! "$(echo "$1" | cut -d. -f3)" ]; then
    echo "$1.0";
  else
    echo "$1";
  fi;
}
do_versioncheck() {
  [ "$(file_getprop anykernel.sh supported.versions)" ] || return 1;
  local android_ver hi_ver lo_ver parsed_ver supported supported_ver;
  ui_print "Checking Android version...";
  supported_ver=$(file_getprop anykernel.sh supported.versions | tr -d '[:space:]');
  android_ver=$(file_getprop /system/build.prop ro.build.version.release);
  parsed_ver=$(int2ver $android_ver);
  if echo $supported_ver | grep -q '-'; then
    lo_ver=$(int2ver "$(echo $supported_ver | cut -d- -f1)");
    hi_ver=$(int2ver "$(echo $supported_ver | cut -d- -f2)");
    if echo -e "$hi_ver\n$lo_ver\n$parsed_ver" | sort -g | grep -n "$parsed_ver" | grep -q '^2:'; then
      supported=1;
    fi;
  else
    for ver in $(echo $supported_ver | sed 's;,; ;g'); do
      if [ "$(int2ver $ver)" == "$parsed_ver" ]; then
        supported=1;
        break;
      fi;
    done;
  fi;
  if [ "$supported" ]; then
    ui_print "$android_ver" " ";
  else
    abort " " "Unsupported Android version. Aborting...";
  fi;
}
