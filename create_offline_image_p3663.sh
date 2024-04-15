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
make clean;make
cp $PDK_TOP/drive-foundation/platform-config/bpmp_dt/t23x/tegra234-bpmp-3663-0001-a01.dtb $PDK_TOP/drive-foundation/tools/flashtools/flashing_kernel/flashing_fw/t23x/



cd $PDK_TOP/drive-foundation/
#make -f make/Makefile.bind PCT=linux BOARD=p3663-a01 clean
#make -f make/Makefile.bind PCT=linux BOARD=p3663-a01
./make/bind_partitions -d -g -w MAXP-A-990-D-04 -b p3663-a01 -u eco_ufs_boot  linux --oot_kernel clean
./make/bind_partitions -d -g -w MAXP-A-990-D-04 -b p3663-a01 -u eco_ufs_boot  linux --oot_kernel
#make/bind_partitions -b p3663-a01 linux


cd $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn

#create offline packet
echo "Create offline packet"
# A_1_gos0-fs A_1_kernel A_1_kernel-dtb A_fsi-fw A_1_ramdisk
if [ $1 ]; then
	python3 ./bootburn.py -u A_fsi-fw  -b p3663-a01 -B qspi -D --customer-data $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/customer_data_orin_c.json $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/nv-customer-data-schema.json 

else
	python3 ./create_bsp_images.py --hsm rsa --chain A -E -b p3663-a01 -B qspi -r 1 -g $FLASH_OUTDIR -D --customer-data $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/customer_data_orin_c.json $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/nv-customer-data-schema.json 	
	cp $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/customer_data_orin_c.json $FLASH_OUTDIR/tools/flashtools/board_configs/customer_data_orin.json
	cp $TEGRA_TOP/drive-foundation/tools/flashtools/storage_configs/t23x/ufs-provision-p3663.cfg $FLASH_OUTDIR/tools/flashtools/board_configs/
	cp $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/nv-customer-data-schema.json $FLASH_OUTDIR/tools/flashtools/board_configs/
	cp $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn/offline_flash* $FLASH_OUTDIR/
fi
