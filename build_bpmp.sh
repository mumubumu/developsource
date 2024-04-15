export _NV_INSTALL_LICENSE_BYPASS_="Destination Tegra Dominance"
export NV_GIT_TOP=$(pwd)
export TEGRA_TOP=$(pwd)
export TOP=$TEGRA_TOP
export FLASH_OUTDIR=$TOP/out/bsp_images
export NV_OUTDIR=$PWD
export PDK_TOP=$PWD
export NV_WORKSPACE=$(pwd)
export CROSS_COMPILE=$NV_GIT_TOP/toolchains/aarch64--glibc--stable-2022.03-1/bin/aarch64-linux-

cd $PDK_TOP/drive-foundation/platform-config/bpmp_dtsi/t23x
make clean; make
#dtc -I dtb -O dts -f $PDK_TOP/drive-foundation/platform-config/bpmp_dt/t23x/tegra234-bpmp-3663-0001-a01.dtb -o $PDK_TOP/tmp_p3663_bpmp.dts
cp   $PDK_TOP/drive-foundation/platform-config/bpmp_dt/t23x/tegra234-bpmp-3663-0001-a01.dtb $PDK_TOP/drive-foundation/tools/flashtools/flashing_kernel/flashing_fw/t23x/
