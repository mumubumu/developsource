#!/bin/bash
set -e 


export _NV_INSTALL_LICENSE_BYPASS_="Destination Tegra Dominance"
export TEGRA_TOP=$(pwd)
export TOP=$TEGRA_TOP
export FLASH_OUTDIR=$TOP/out/bsp_images
export FLASH_OUTDIR_ECC=$TOP/out/bsp_images_ecc
export NV_OUTDIR=$PWD
export PDK_TOP=$PWD
export NV_WORKSPACE=$(pwd)
export TN_TARGETFS_PWD=$TOP/drive-linux/filesystem


cd $PDK_TOP/drive-foundation/platform-config/bpmp_dtsi/t23x
make clean;make
cp $PDK_TOP/drive-foundation/platform-config/bpmp_dt/t23x/tegra234-bpmp-3898-0001-b00.dtb $PDK_TOP/drive-foundation/tools/flashtools/flashing_kernel/flashing_fw/t23x/

#create offline packet
echo "Create offline packet"
# A_1_gos0-fs A_1_kernel A_1_kernel-dtb A_fsi-fw A_1_ramdisk
if [ $1 ]; then
	python3 ./bootburn.py -u B_1_kernel-dtb  -b p3898-b00 -B qspi -D --customer-data $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/customer_data_orin_c.json $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/nv-customer-data-schema.json 

else
###################### CHAIN A ####################### 
  cd $PDK_TOP
  python3 replace_ecc.py $PDK_TOP/drive-foundation/platform-config/hardware/nvidia/platform/t23x/automotive/pct/drive_av/linux/common_profile.mk n

  cd $PDK_TOP/drive-foundation/
  ./make/bind_partitions -d -g -b p3898-b00 -u eco_ufs_boot linux --oot_kernel clean
  ./make/bind_partitions -d -g -b p3898-b00 -u eco_ufs_boot linux --oot_kernel
  cp $PDK_TOP/drive-foundation/tools/flashtools/flashing_kernel/kernel/t23x/tegra234-p3663-0001-a01-flashing_base.dtb   $PDK_TOP/drive-foundation/tools/flashtools/flashing_kernel/kernel/t23x/tegra234-p3898-b00-linux-flashing.dtb

  cd $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn
  python3 ./create_bsp_images.py --chain A -b p3898-b00 -B qspi -r 1 -g $FLASH_OUTDIR -D --customer-data $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/customer_data_orin_c.json $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/nv-customer-data-schema.json --hsm rsa 

###################### CHAIN B ######################### 
  cd $PDK_TOP
  python3 replace_ecc.py $PDK_TOP/drive-foundation/platform-config/hardware/nvidia/platform/t23x/automotive/pct/drive_av/linux/common_profile.mk y


  cd $PDK_TOP/drive-foundation/
  ./make/bind_partitions -d -g -b p3898-b00 -u eco_ufs_boot linux --oot_kernel clean
  ./make/bind_partitions -d -g -b p3898-b00 -u eco_ufs_boot linux --oot_kernel
  cp $PDK_TOP/drive-foundation/tools/flashtools/flashing_kernel/kernel/t23x/tegra234-p3663-0001-a01-flashing_base.dtb   $PDK_TOP/drive-foundation/tools/flashtools/flashing_kernel/kernel/t23x/tegra234-p3898-b00-linux-flashing.dtb

  cd $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn
  python3 ./create_bsp_images.py --chain B -b p3898-b00 -B qspi -r 1 -g $FLASH_OUTDIR_ECC -D --customer-data $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/customer_data_orin_c.json $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/nv-customer-data-schema.json --hsm rsa 

#################### CHAIN B to CHAIN A ####################### 
	cp $FLASH_OUTDIR_ECC/670-63898-0000-200_TS1/flash-images/B*  $FLASH_OUTDIR/670-63898-0000-200_TS1/flash-images
  sed -n '5,39p' $FLASH_OUTDIR_ECC/670-63898-0000-200_TS1/flash-images/FileToFlash.txt >> $FLASH_OUTDIR/670-63898-0000-200_TS1/flash-images/FileToFlash.txt
	rm -rf $FLASH_OUTDIR_ECC 

	cp $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/customer_data_orin_c.json $FLASH_OUTDIR/tools/flashtools/board_configs/customer_data_orin.json
	cp $TEGRA_TOP/drive-foundation/tools/flashtools/storage_configs/t23x/ufs-provision-p3663.cfg $FLASH_OUTDIR/tools/flashtools/board_configs/
	cp $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn_t23x_py/nv-customer-data-schema.json $FLASH_OUTDIR/tools/flashtools/board_configs/
	cp $TEGRA_TOP/drive-foundation/tools/flashtools/bootburn/offline_flash* $FLASH_OUTDIR/


fi
