#!/bin/bash
set -e 


export _NV_INSTALL_LICENSE_BYPASS_="Destination Tegra Dominance"
export TEGRA_TOP=$(pwd)
export TOP=$TEGRA_TOP
export FLASH_OUTDIR=$TOP/out/bsp_images
export NV_OUTDIR=$PWD
export PDK_TOP=$PWD
export NV_WORKSPACE=$(pwd)
export NV_BUILD_WORK_LOAD=ORIN_MAXP_H
export TN_TARGETFS_PWD=$TOP/drive-linux/filesystem


cd $PDK_TOP/drive-foundation/platform-config/bpmp_dtsi/t23x
make clean

cd $PDK_TOP/drive-foundation/
./make/bind_partitions -d -g -w MAXP-A-990-D-04 -b p3663-a01 -u eco_ufs_boot linux --oot_kernel clean


