
export TOP=${PWD}
export CONFIG_PATH=${TOP}/tools/flashtools/board_configs/
cd tools/flashtools/bootburn
sudo python3 ./flash_bsp_images.py -b p3663-a01 -D -P ../../../*-* -U ${CONFIG_PATH}/ufs-provision-p3663.cfg
