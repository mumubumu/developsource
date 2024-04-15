#!/bin/bash
set -e 

if [[ $# -lt  1 ]];then
	echo "Need input param"
	exit 1;
fi

if [[ $1 == "N" ]];then
	cp -v ./orinn/offline_flash_0*      ./drive-foundation/tools/flashtools/bootburn/
	cp -v ./orinn/*.cfg                 ./drive-foundation/platform-config/hardware/nvidia/platform/t23x/automotive/pct/drive_av/linux/  
	cp -v ./orinn/create_offline_image* ./
	cp -v ./orinn/replace_ecc.py        ./
	cp -v ./orinn/nv_fanctrl_orin.sh    ./drive-linux/filesystem/tn_targetfs/
	cp -v ./orinn/nv_tacp_init.sh       ./drive-linux/filesystem/tn_targetfs/etc/systemd/scripts/nv_tacp_init.sh
	cp -v ./orinn/fota_sysinfo.json     ./drive-linux/filesystem/tn_targetfs/
elif [[ $1 == "X" ]];then
	cp -v ./orinx/offline_flash_0*      ./drive-foundation/tools/flashtools/bootburn/
	cp -v ./orinx/*.cfg                 ./drive-foundation/platform-config/hardware/nvidia/platform/t23x/automotive/pct/drive_av/linux/  
	cp -v ./orinx/create_offline_image* ./
	cp -v ./orinx/nv_fanctrl_orin.sh    ./drive-linux/filesystem/tn_targetfs/
	cp -v ./orinx/nv_tacp_init.sh       ./drive-linux/filesystem/tn_targetfs/etc/systemd/scripts/nv_tacp_init.sh
	cp -v ./orinx/fota_sysinfo.json     ./drive-linux/filesystem/tn_targetfs/
else
	echo "Input error"
	exit 1;
fi
