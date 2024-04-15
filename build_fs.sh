#!/bin/bash
export _NV_INSTALL_LICENSE_BYPASS_="Destination Tegra Dominance"
export NV_GIT_TOP=$(pwd)
export TEGRA_TOP=$(pwd)
export TOP=$TEGRA_TOP
export NV_OUTDIR=$PWD
export PDK_TOP=$PWD
export NV_WORKSPACE=$(pwd)
export PROD_SUFFIX=""
export NVRTKERNELNAME="$(basename $NV_WORKSPACE/drive-linux/kernel/preempt_rt${PROD_SUFFIX}/modules/*rt*-tegra)"

set -e

function app_build(){ 
    echo "start build application"
    cd $TOP/drive-linux/samples/switch_update/6_build_bp/
    if [ -f CMakeCache.txt ]; then
    	rm CMake* -rf
    	rm cmake*
    fi
    cmake ../2_DownloadImage_src/
    make clean
    make 
    cd $TOP/drive-linux/samples/openwfd/
    make NV_WINSYS=egldevice clean
    make NV_WINSYS=egldevice
    
    #TODO NVSIPL build failed
    cd $TOP/drive-linux/samples/nvmedia/nvsipl/
    make  clean
    make -j8
    cd $TOP/drive-linux/samples/nvmedia/nvsipl/test/tn_eol_multicast/
    make clean
    make
    cp -v ./nvsipl_multicast $TOP/drive-linux/filesystem/tn_targetfs/other/eol/tn_eol_multicast
    
    cd $TOP/drive-linux/samples/ccplex_sf/EplDemo/
    make clean && make
#    sudo cp ./DemoAppSwErr $TN_TARGETFS_PWD/tn_targetfs/usr/bin
    cd $TOP/drive-linux/samples/ccplex_sf/Fsi-ccplex-com/DemoAppCom/
    make clean && make
    cd $TOP/drive-linux/samples/ccplex_sf/Fsi-ccplex-com/DemoAppAll/
    make clean && make
    cd $TOP/drive-linux/samples/ccplex_sf/Fsi-ccplex-com/CcplexApp/
    make clean && make
    cd $TOP/drive-linux/samples/ccplex_sf/EplDemo/
    make clean && make
    sudo chown root:root -R $TOP/drive-linux/filesystem/tn_targetfs/opt/m0/
    sudo chown root:root -R $TOP/drive-linux/filesystem/tn_targetfs/opt/m/
    sudo chown root:root -R $TOP/drive-linux/filesystem/tn_targetfs/other/
#
}

#app_build
#cd $PDK_TOP
#./build_kernel.sh

#for initramfs
if [ ! -d $NV_WORKSPACE/drive-linux/filesystem/ramfs/ ]; then
	mkdir $NV_WORKSPACE/drive-linux/filesystem/ramfs/
fi
cd $NV_WORKSPACE/drive-linux/filesystem/ramfs
sudo rm ./* -rf
sudo cpio -imdu --quiet < ../initramfs.cpio
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.builtin.bin ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.dep         ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.dep.bin     ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.symbols     ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.symbols.bin ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.devname     ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.alias       ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.alias.bin   ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/modules.softdep     ./lib/modules/${NVRTKERNELNAME}/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/firmware/tegra/ivc_ext.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/firmware/tegra/ivc_ext.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/virt/tegra/tegra_hv.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/virt/tegra/tegra_hv.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/block/tegra_oops_virt_storage/tegra_hv_vblk_oops.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/block/tegra_oops_virt_storage/tegra_hv_vblk_oops.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/block/tegra_virt_storage/tegra_vblk.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/block/tegra_virt_storage/tegra_vblk.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/firmware/tegra/tegra_bpmp.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/firmware/tegra/tegra_bpmp.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/staging/platform/tegra/gte/tegra194_gte.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/staging/platform/tegra/gte/tegra194_gte.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/nvpps/nvpps.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/nvpps/nvpps.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/pinctrl/tegra/pinctrl-tegra234.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/pinctrl/tegra/pinctrl-tegra234.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/drivers/net/ethernet/nvidia/nvethernet/nvethernet.ko ./lib/modules/${NVRTKERNELNAME}/extra/drivers/net/ethernet/nvidia/nvethernet/nvethernet.ko
sudo mkdir -p ./lib/modules/${NVRTKERNELNAME}/kernel/drivers/spi/
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/kernel/drivers/spi/spi-tegra114.ko   ./lib/modules/${NVRTKERNELNAME}/kernel/drivers/spi/spi-tegra114.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/kernel/drivers/spi/spidev.ko          ./lib/modules/${NVRTKERNELNAME}/kernel/drivers/spi/spidev.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/kernel/drivers/nvme/host/nvme-core.ko ./lib/modules/${NVRTKERNELNAME}/kernel/drivers/nvme/host/nvme-core.ko
sudo cp -v $NV_WORKSPACE/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/kernel/drivers/nvme/host/nvme.ko ./lib/modules/${NVRTKERNELNAME}/kernel/drivers/nvme/host/nvme.ko
sudo touch ./proc/mounts
sudo cp -v ../tn_ramfs/init ./init
sudo cp -v ../tn_ramfs/bin/* ./bin/
sudo cp -v ../tn_ramfs/lib/* ./lib/

sudo find . | sudo  cpio -o -Hnewc > ../initramfs.cpio


sudo -E /usr/bin/python3 -B /opt/nvidia/driveos/common/filesystems/build-fs/17/bin/build_fs.py -w ${NV_WORKSPACE}/ -i ${NV_WORKSPACE}/drive-linux/filesystem/targetfs-images/driveos-filesystem.json -o ${NV_WORKSPACE}/drive-linux/filesystem/targetfs-images/



#if [ ! -d ./temp ]; then
#	mkdir ./temp
#fi

#sudo mount ${NV_WORKSPACE}/drive-linux/filesystem/targetfs.img ./temp

#sudo cp -rd ${NV_WORKSPACE}/drive-linux/kernel/source/oss_src/out-linux/lib/modules/5.10.120-rt70-tegra/* ./temp/lib/modules/5.10.120-rt70-tegra/
#sudo cp  ${NV_WORKSPACE}/drive-linux/filesystem/tn_targetfs/nv_bugfix/include/* ./temp/usr/include/
#sudo cp  ${NV_WORKSPACE}/drive-linux/filesystem/tn_targetfs/nv_bugfix/lib/* ./temp/usr/lib/
#sudo cp  ${NV_WORKSPACE}/drive-linux/filesystem/tn_targetfs/nv_bugfix/usr/local/bin/* ./temp/usr/local/bin/
#sudo cp  ${NV_WORKSPACE}/drive-linux/kernel/source/oss_src/out-linux/spidev_test ./temp/usr/bin
#sudo cp  ${NV_WORKSPACE}/drive-linux/kernel/source/oss_src/out-linux/spidev_fdx ./temp/usr/bin
#sudo cp  ${NV_WORKSPACE}/drive-linux_src/NVIDIA-kernel-module-source-TempVersion/kernel-open/*.ko ./temp/lib/modules/5.10.120-rt70-tegra/extra/opensrc-disp/
#sudo cp  ${NV_WORKSPACE}/drive-linux_src/TSM_Driver_Linux/*.ko ./temp/lib/modules/5.10.120-rt70-tegra/kernel/lib/
#sudo cp  ${NV_WORKSPACE}/drive-linux_src/TsmS_Driver/*.ko ./temp/lib/modules/5.10.120-rt70-tegra/kernel/lib/
#sync

#sudo -E umount ${NV_WORKSPACE}/drive-linux/filesystem/mowi_ph/

#sudo umount ./temp
#if [ -d ./temp ]; then
#	rm -rf ./temp
#fi

#if [ -d ${NV_WORKSPACE}/drive-linux/filesystem/mowi_ph/ ]; then
#	rm -rf ${NV_WORKSPACE}/drive-linux/filesystem/mowi_ph/
#fi

#sudo ln -sf ${NV_WORKSPACE}/drive-linux/filesystem/targetfs-images/driveos-user-rfs.img ${NV_WORKSPACE}/drive-linux/filesystem/targetfs-images/targetfs.img

#if [ $# -gt 0 ]; then
#        AURIX_UART=/dev/ttyUSB$1
#else
#        AURIX_UART=/dev/ttyUSB10
#fi
#echo $AURIX_UART
#sudo echo "tegrarecovery x1 on" > $AURIX_UART
#sudo echo "tegrareset x1" > $AURIX_UART


