
export TOP=${PWD}
export CONFIG_PATH=${TOP}/tools/flashtools/board_configs/
cd tools/flashtools/bootburn
sudo python3 ./flash_bsp_images.py -b p3663-a01 -D -P ../../../*-* --customer-data ${CONFIG_PATH}/customer_data_orin.json ${CONFIG_PATH}/nv-customer-data-schema.json
