export TOP=$PWD
export LOCATION_PATH=$PWD
export ARCH=arm64
export CROSS_COMPILE=$TOP/toolchains/aarch64--glibc--stable-2022.03-1/bin/aarch64-buildroot-linux-gnu-
export LOCALVERSION="-tegra"

FLASHING_KERNEL_PATH=$TOP/drive-foundation/tools/flashtools/flashing_kernel/src/t23x/drive-oss-t186-flashing_kernel-src
FLASHING_KERNEL_DTB=$FLASHING_KERNEL_PATH/drive-oss-src/out-t234-linux/out/arch/arm64/boot/dts/nvidia/tegra234-p3898-b00-flashing_base.dtb

cd $TOP/drive-foundation/tools/flashtools/flashing_kernel/src/t23x
if [ ! -d "$FLASHING_KERNEL_PATH" ]; then
	echo "untar flashing kernel source code"
	sudo chown 777  ./drive-oss-t186-flashing_kernel-src.tar.bz2
	tar xvf ./drive-oss-t186-flashing_kernel-src.tar.bz2
	mkdir -p $FLASHING_KERNEL_PATH/drive-oss-src/out-t234-linux/out
fi
cd $FLASHING_KERNEL_PATH/drive-oss-src

if [ -f "$FLASHING_KERNEL_DTB" ]; then
	rm $FLASHING_KERNEL_DTB
fi
make -C kernel O=${PWD}/out-t234-linux/out clean
make -C kernel O=${PWD}/out-t234-linux/out tegra_defconfig
make -C kernel O=${PWD}/out-t234-linux/out dtbs
make -C kernel O=${PWD}/out-t234-linux/out Image
#make -C kernel-5.10 O=${PWD}/out-t234-linux/out modules_install
sudo cp -rvf $FLASHING_KERNEL_PATH/drive-oss-src/out-t234-linux/out/arch/arm64/boot/dts/nvidia/tegra234-p3898-b00-flashing_base.dtb $TOP/drive-foundation/tools/flashtools/flashing_kernel/kernel/t23x/tegra234-p3898-b00-flashing_base.dtb
#dtc -I dtb -O dts -f $FLASHING_KERNEL_PATH/drive-oss-src/out-t234-linux/out/arch/arm64/boot/dts/nvidia/tegra234-p3663-0001-a01-flashing_base.dtb -o $LOCATION_PATH/tmp_p3663_flashing.dts
