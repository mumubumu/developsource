#!/bin/bash
# Copyright (c) 2016-2021, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.

#
# nv_tacp_init - Runs setup cmds for tacp
#
/etc/systemd/scripts/tn_eth_init.sh

#check nvidia nvidia-modeset kernel module status 
for NEED_RETRY_INSMOD in 1 2 3
do
	NUM_KO=`lsmod | grep nvidia | wc -l`
	if [[ $NUM_KO -eq 8 ]]; then
		break
	else
		echo "RETRY NVIDIA Kernel MODULE " > /dev/kmsg
		if [ -e /dev/nvgpu/igpu0/power ]; then
			# Power on GPU
			eval "echo 1 > /dev/nvgpu/igpu0/power"
		fi
        insmod /lib/modules/$(uname -r)/extra/opensrc-disp/nvidia.ko NVreg_RmMsg="@2" rm_firmware_active="all" NVreg_RegistryDwords="RMHdcpKeyglobZero=1"
        insmod /lib/modules/$(uname -r)/extra/opensrc-disp/nvidia-modeset.ko
		sleep 0.1
	fi
done

#partition size check
update_size=`df | grep vblkdev54 | awk '{print $2}'`
mlog_size=`df | grep vblkdev23 | awk '{print $2}'`
other_size=`df | grep vblkdev56 | awk '{print $2}'`
mcalib_size=`df | grep vblkdev50 | awk '{print $2}'`

if [[ $update_size < 10000000 ]];then
                echo "resize update partition" > /dev/console
                /usr/sbin/resize2fs /dev/vblkdev54
fi
if [[ $mlog_size < 10000000 ]];then
                echo "resize mlog partition" > /dev/console
                /usr/sbin/resize2fs /dev/vblkdev23
fi
if [[ $other_size < 1000000 ]];then
                echo "resize other partition" > /dev/console
                /usr/sbin/resize2fs /dev/vblkdev56
fi
if [[ $mcalib_size < 10000000 ]];then
                echo "resize mcalib partition" > /dev/console
                /usr/sbin/resize2fs /dev/vblkdev50
fi

if [ -f /app/etc/run_routing.sh ] ; then
	/app/etc/run_routing.sh
	if [ -f /app/etc/run_routing_42.sh ] ; then
		/app/etc/run_routing_42.sh
	fi
fi

#clear log 
#bash -c "systemctl kill --kill-who=main --signal=SIGUSR2 systemd-journald.service";
#bash -c "rm /var/log/journal/* -rf";
#bash -c "systemctl restart systemd-journald";
#bash -c "journalctl --vacuum-size=100M"

bash -c 'cat /dev/null > /var/log/kern.log'
bash -c 'cat /dev/null > /var/log/auth.log'
bash -c "echo > /var/log/wtmp"

#backup
/etc/systemd/scripts/tn_backup.sh

exit 0;

DEVICE_TREE="/proc/device-tree"
BOARD_CONFIG=$DEVICE_TREE/board_config
AURIX_CONFIG=$BOARD_CONFIG/aurix

echo "Initialize Tegra to Aurix communication protocol..."

read target_board < /proc/device-tree/model
read pct_configuration < /proc/device-tree/pct-info/pct-name

function waitforlink() {
    local _interface=$1
    local COUNTER=0
    local -r RETRY_LIMIT=61
    local printed_warning=0
    local printed_error=0

    ###Check if interface link is up
    ###Autoneg worst case link up time is 5sec
    ###Setting retry limit to 6sec
    while [ $COUNTER -lt $RETRY_LIMIT ]; do
        if [ -f /sys/class/net/${_interface}/carrier ] ; then
            read -r carrier </sys/class/net/${_interface}/carrier
            if [ $? != 0 ] ; then
                if [ ${printed_error} == 0 ] ; then
                    echo "Error: could not read carrier for \"${_interface}\""
                    printed_error=1
                fi
            elif [ $carrier -ne 0 ]; then
                break
            fi
        else
            #this shouldn't happen
            # we'll continue iterating just in case the iterface shows up later?
            if [ ${printed_warning} == 0 ] ; then
                echo "warning missing carrier for interface \"${_interface}\""
                printed_warning=1
            fi
        fi
        sleep 0.1
        let COUNTER=COUNTER+1
    done
    if [ $COUNTER -ge $RETRY_LIMIT ]; then
        #shouldn't ever happen
        echo "Warning: nv_tacp_init - wait for carrier timed out"
    fi
}

function configure_eth() {
    local _interface=$1
    local _address=$2
    local _netmask=$3

    dot_exist=`echo $_interface | grep "\."`

    if [ -z "$dot_exist" ] ; then
        echo "Creating $_interface:0 with setting ip $_address"
        ifconfig $_interface up
        waitforlink $_interface
        ifconfig $_interface:0 $_address netmask $_netmask up
        return 0
    else
        vlan_id=`echo $_interface | cut -d "." -f 2`
        m_iface=`echo $_interface | cut -d "." -f 1`

        if [ -n "$vlan_id" ] ; then
            ifconfig $m_iface up
            waitforlink $m_iface
            echo "Creating VLAN iface $m_iface.$vlan_id"
            ip link add link $m_iface name $_interface type vlan id $vlan_id
            echo "Setting ip $_address to $_interface"
            ifconfig $_interface $_address netmask $_netmask up
            return 0
        fi
    fi
}

# +----------------------------------------------------------------+
# |                                                                |
# |  +----------------------------+                                |
# |  |            VM 0            |         +----------------+     |
# |  |     +-------------+        |         |      VM 1      |     |
# |  |     |   +-------> | 12.0.0.1         |                |     |
# |  | +---+     NAT     |   +----+         +---+            |     |
# |  | |   |   <-------+ +---+    |         |   |12.0.0.2    |     |
# |  | |   +----+--------+   |hv0 +---------+hv0|            |     |
# |  | |        |            |    |         |   |            |     |
# |  | |  +-----+--+         +----+         +---+            |     |
# |  | |  |eth0.200| 10.42.0.28   |         |                |     |
# |  | |  +--------+              |         |                |     |
# |  | +--+eth0    | dhcp ip      |         |                |     |
# |  +----+--------+--------------+         +----------------+     |
# |         |                                                      |
# |         +---------------+Xavier A                              |
# |                         |                                      |
# |                   +---------------------------+                |
# |                   |                           |                |
# |                   |          Switch           |                |
# |                   +---------------------------+                |
# |                       |         |                              |
# |            +----------+  +------------+                        |
# |            |             |            |                        |
# |        +-----+           |   Aurix    |              DDPX      |
# |        | Port|           +------------+                        |
# +--------+-----+-------------------------------------------------+
#         External Network

function hv_bridge_config(){
    local _interface=$1
    local _address=$2
    local _netmask=$3

    configure_eth $_interface $_address $_netmask

    VM_ID=$(cat /sys/class/tegra_hv/vmid)

    echo "Creating Bridge for HV and eth0"
    if [ $VM_ID -eq 0 ]; then
       dhclient eth0
       sysctl -w net.ipv4.ip_forward=1
       iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
       iptables -t nat -A POSTROUTING -o eth0.200 -j MASQUERADE
       iptables -A FORWARD -j ACCEPT
       ifconfig hv0 12.0.0.1/24
    else
       ifconfig hv0 12.0.0.2/24 up
       route add default gw 12.0.0.1
    fi
}

#script start

if [ -e $AURIX_CONFIG ];then
    read aurix_ip < $AURIX_CONFIG/AURIX_IP_ADDRESS
    read tacp_interface < $AURIX_CONFIG/aurix_linux_interface
    read tegra_ip < $BOARD_CONFIG/TEGRA_IP_ADDRESS
else
    echo "Could not get Aurix IP information at $AURIX_CONFIG"
    exit -1
fi

BCM_STATUS=/proc/device-tree/board_config/switch_bcm89553/status
if [ -e ${BCM_STATUS} ]; then
    read DT_BCM < ${BCM_STATUS}
    if [ "${DT_BCM}" == "okay" ]; then
        echo "Execute 3898 specific vlan setup script"
        /bin/bash /etc/systemd/scripts/nv_3898_vlan_setup.sh ifw > /tmp/3898-vlan.txt 2>&1
    fi
fi

nfs_fs=`cat /proc/cmdline | grep nfs`

if [[ "$tacp_interface" == "eth"* ]] || [[ "$tacp_interface" == "mgbe"* ]] ||
    [[ "$tacp_interface" == "eqos"* ]]
    then
    if [ "$pct_configuration" == "ll" ] && [ -z "$nfs_fs" ] ; then
        hv_bridge_config $tacp_interface $tegra_ip "255.255.255.0"
    else
        configure_eth $tacp_interface $tegra_ip "255.255.255.0"
    fi
fi

# Print appropriate error if ping fails
if [ -z $aurix_ip ]; then
    #send 4 pings, pass if any make it
    ping -c 4 -i 0.2 -W 2 $aurix_ip > /dev/null 2>&1
    if [ $? != 0 ]; then
        echo "aurix communication failed"
    else
        echo "aurix ($aurix_ip) communication ok"
    fi
fi
