# No Shebang, Please execute this script with /bin/bash in Linux or /bin/ksh in QNX
#
# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.

#
# nv_fanctrl_orin
#



life_time=`mnand_hs -d /dev/vblkdev20 -ext | grep 0256 | awk '{print $13}'`

echo "EMMC life time is ${life_time}" >> /var/log/kern.log
echo "EMMC erase block info:" >> /var/log/kern.log
mnand_hs -d /dev/vblkdev20 -as >> /var/log/kern.log
echo "UFS health report:" >> /var/log/kern.log				
ufsparm_arm_64bit micron_health_report /dev/vblkdev0 >> /var/log/kern.log
 

exit 0;






debug_level=0
target_os=`uname`

if [[ $target_os == "Linux" ]]; then
	DT_NODE_PATH="/proc/device-tree"
	SUDO_CMD="sudo"
	FANCTRL_APP_PATH="/usr/bin/fanctrl_orin"
else
	DT_NODE_PATH="/dev/nvdt"
	SUDO_CMD="on"
	FANCTRL_APP_PATH="/proc/boot/fanctrl_orin"
fi

if [ "$1" != "" ]; then
	export SOCK=$1
fi

#if [ -e "${DT_NODE_PATH}/board_config/fan_control_daemon/status" ]; then
#	read FAN_CONTROL_DAEMON < ${DT_NODE_PATH}/board_config/fan_control_daemon/status
#	if [[ ${FAN_CONTROL_DAEMON} != "okay" ]]; then
#		echo "Unsupported platform for fan control daemon APP..."
#		exit 0
#	fi
#fi

if [ -e "${DT_NODE_PATH}/board_config/TEGRA_IP_ADDRESS" ]; then
	read MY_IP_ADDRESS < ${DT_NODE_PATH}/board_config/TEGRA_IP_ADDRESS
	# if there is usecase to use ethernet interface, need ip address of own.
	MY_IP_OPTION="-i ${MY_IP_ADDRESS}"
	echo "Read my IP address"
else
	echo "Unable to find my IP address, use default..."
fi

if [ -e "${DT_NODE_PATH}/board_config/aurix/AURIX_IP_ADDRESS" ]; then
	read AURIX_IP_ADDRESS < ${DT_NODE_PATH}/board_config/aurix/AURIX_IP_ADDRESS
	# if there is usecase to use ethernet interface, need ip address of own.
	SERVER_IP_OPTION="-s ${AURIX_IP_ADDRESS}"
	echo "Read Aurix IP address"
else
	echo "Unable to find Aurix IP address, use default..."
fi


if [[ $debug_level -eq 3 ]]; then
	VERVOSE_OPTION="-v 3"
elif [[ $debug_level -eq 2 ]]; then
	VERVOSE_OPTION="-v 2"
elif [[ $debug_level -eq 1 ]]; then
	VERVOSE_OPTION="-v 1"
else
	VERVOSE_OPTION=""
fi

if [[ $target_os == "QNX" ]]; then
	# Wait until vlan200 interface is configured when vlan is used
	if_up -a vlan200
fi

# Run fanctrl_orin APP
$SUDO_CMD $FANCTRL_APP_PATH $VERVOSE_OPTION $MY_IP_OPTION $SERVER_IP_OPTION
