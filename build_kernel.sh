export _NV_INSTALL_LICENSE_BYPASS_="Destination Tegra Dominance"
export TEGRA_TOP=$(pwd)
export TOP=$TEGRA_TOP
export FLASH_OUTDIR=$TOP/out/bsp_images
export NV_OUTDIR=$PWD
export PDK_TOP=$PWD
export NV_WORKSPACE=$(pwd)
export NV_OUTDIR=$PDK_TOP/foundation
export NV_GIT_TOP=$(pwd)
export ARCH=arm64
export CROSS_COMPILE=$NV_GIT_TOP/toolchains/aarch64--glibc--stable-2022.03-1/bin/aarch64-linux-
export LOCALVERSION="-rt-tegra"
export KERNEL_SRC=$NV_GIT_TOP/drive-linux/kernel/source/oss_src
export INSTALL_MOD_PATH=$TOP/drive-linux/kernel/source/oss_src/out-linux
export INSTALL_MOD_STRIP=1
export PROD_SUFFIX=""
export NVRTKERNELNAME="$(basename $NV_WORKSPACE/drive-linux/kernel/preempt_rt${PROD_SUFFIX}/modules/*rt*-tegra)"

#export NV_BUILD_KERNEL_OPTIONS="5.10 rt_patches"
#export TN_IPD_B=1

set -e
cd $KERNEL_SRC
bash $KERNEL_SRC/kernel/scripts/generic-rt-patch.sh apply-patches
if [ ! -d ./out-linux ]; then
	mkdir out-linux
fi


make -C kernel O=${PWD}/out-linux clean
make -C kernel O=${PWD}/out-linux defconfig
#make -j16 -C kernel/tools/spi O=${PWD}/out-linux
#make -j16 -C kernel/tools/testing/selftests/ftrace/ O=${PWD}/out-linux
#make -j80 -C kernel O=${PWD}/out-linux Image
#rm $KERNEL_SRC/out-linux/arch/arm64/boot/dts/nvidia/tegra234-p3663-0001-a01-linux-driveav-gos.dtb
#make -j16 -C kernel O=${PWD}/out-linux dtbs 
make -j80 -C kernel O=${PWD}/out-linux


ln -s -f ${PWD}/nvidia-oot /tmp/nv-oot
ln -s -f ${PWD}/nvgpu /tmp/nvgpu
ln -s -f ${PWD}/hwpm /tmp/hwpm

#make -j80 -C ${PWD}/out-linux M=/tmp/hwpm/drivers/tegra/hwpm  srctree.hwpm=/tmp/hwpm V=1 clean CONFIG_TEGRA_OOT_MODULE=m CONFIG_TEGRA_VIRTUALIZATION=y
make -j80 -C ${PWD}/out-linux M=/tmp/hwpm/drivers/tegra/hwpm  srctree.hwpm=/tmp/hwpm V=1 modules CONFIG_TEGRA_OOT_MODULE=m CONFIG_TEGRA_VIRTUALIZATION=y

#make -j80 -C ${PWD}/out-linux M=/tmp/nv-oot srctree.nvidia-oot=/tmp/nv-oot scrtree.nvidia=/tmp/nv-oot srctree.hwpm=/tmp/hwpm V=1 KBUILD_EXTRA_SYMBOLS=/tmp/hwpm/drivers/tegra/hwpm/Module.symvers clean CONFIG_TEGRA_OOT_MODULE=m CONFIG_TEGRA_VIRTUALIZATION=y
make -j80 -C ${PWD}/out-linux M=/tmp/nv-oot srctree.nvidia-oot=/tmp/nv-oot scrtree.nvidia=/tmp/nv-oot srctree.hwpm=/tmp/hwpm V=1 KBUILD_EXTRA_SYMBOLS=/tmp/hwpm/drivers/tegra/hwpm/Module.symvers modules CONFIG_TEGRA_OOT_MODULE=m CONFIG_TEGRA_VIRTUALIZATION=y

#make -j80 -C ${PWD}/out-linux M=/tmp/nvgpu/drivers/gpu/nvgpu  srctree.nvidia-oot=/tmp/nv-oot srctree.nvidia=/tmp/nv-oot KBUILD_EXTRA_SYMBOLS=/tmp/nv-oot/Module.symvers V=1 clean CONFIG_TEGRA_OOT_MODULE=m CONFIG_TEGRA_VIRTUALIZATION=y
make -j80 -C ${PWD}/out-linux M=/tmp/nvgpu/drivers/gpu/nvgpu  srctree.nvidia-oot=/tmp/nv-oot srctree.nvidia=/tmp/nv-oot KBUILD_EXTRA_SYMBOLS=/tmp/nv-oot/Module.symvers V=1 modules CONFIG_TEGRA_OOT_MODULE=m CONFIG_TEGRA_VIRTUALIZATION=y

mkdir -p out-linux/src-rt
cp -al kernel ./out-linux/src-rt/
./kernel/scripts/build-module-mlnx.sh ./mlnx-drivers ./out-linux/ ./out-linux/src-rt/kernel ./out-linux-mlnx-build

make -j80 -C kernel O=${PWD}/out-linux modules_install

make -j80 -C ${PWD}/out-linux M=/tmp/nv-oot modules_install
make -j80 -C ${PWD}/out-linux M=/tmp/hwpm/drivers/tegra/hwpm modules_install
make -j80 -C ${PWD}/out-linux M=/tmp/nvgpu/drivers/gpu/nvgpu modules_install

cat nvidia-oot/Module.symvers >> out-linux/Module.symvers
rsync -avzpq nvidia-oot/include/ out-linux/include
rsync -avzpq nvidia-oot/drivers/gpu/host1x/include out-linux/drivers/gpu/host1x/include

rm /tmp/nv-oot
rm /tmp/hwpm
rm /tmp/nvgpu

#make -C kernel O=$INSTALL_MOD_PATH modules_install


rm -fv $NV_WORKSPACE/drive-linux/kernel/preempt_rt${PROD_SUFFIX}/images/*
cp -v $KERNEL_SRC/out-linux/arch/arm64/boot/Image $KERNEL_SRC/out-linux/vmlinux $KERNEL_SRC/out-linux/System.map $TEGRA_TOP/drive-linux/kernel/preempt_rt/images/

rm -rf $NV_WORKSPACE/drive-linux/kernel/preempt_rt${PROD_SUFFIX}/modules/*
cp -a ${PWD}/out-linux/lib/modules/*  $NV_WORKSPACE/drive-linux/kernel/preempt_rt${PROD_SUFFIX}/modules/
cp -a ${PWD}/out-linux-mlnx-build/mlnx-drivers-build/debian/mlnx-ofed-kernel-modules/lib/modules/* $NV_WORKSPACE/drive-linux/kernel/preempt_rt${PROD_SUFFIX}/modules/

#dtc -I dtb -O dts -f $KERNEL_SRC/out-linux/arch/arm64/boot/dts/nvidia/tegra234-p3663-0001-a01-linux-driveav-gos.dtb -o $PDK_TOP/tmp_p3663_gos.dts

#cp -vrf $KERNEL_SRC/out-linux/arch/arm64/boot/dts/nvidia/tegra234-p3663-0001-a01-linux-driveav-gos.dtb $TEGRA_TOP/drive-linux/kernel/preempt_rt/


cd $TOP/drive-linux_src/NVIDIA-kernel-module-source-TempVersion
./build.sh

if [ ! -d $TEGRA_TOP/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/opensrc-disp ]; then
	mkdir $TEGRA_TOP/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/opensrc-disp
fi

cp $TOP/drive-linux_src/NVIDIA-kernel-module-source-TempVersion/kernel-open/nvidia-drm.ko   $TEGRA_TOP/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/opensrc-disp/nvidia-drm.ko
cp $TOP/drive-linux_src/NVIDIA-kernel-module-source-TempVersion/kernel-open/nvidia-modeset.ko   $TEGRA_TOP/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/opensrc-disp/nvidia-modeset.ko
cp $TOP/drive-linux_src/NVIDIA-kernel-module-source-TempVersion/kernel-open/nvidia.ko   $TEGRA_TOP/drive-linux/kernel/preempt_rt/modules/${NVRTKERNELNAME}/extra/opensrc-disp/nvidia.ko

cd $TOP/drive-linux_src/TSM_Driver_Linux
./build.sh

cd $TOP/drive-linux_src/TsmS_Driver
./build.sh

