#!/bin/bash
# Copyright (c) 2022, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
#
# NVIDIA CORPORATION, its affiliates and its licensors retain all intellectual
# property and proprietary rights in and to this software, related
# documentation and any modifications thereto.  Any use, reproduction,
# disclosure or distribution of this software and related documentation
# without an express license agreement from NVIDIA CORPORATION or its
# affiliates is strictly prohibited.
set -e
#set -x
export PS4='+(${BASH_SOURCE}:${LINENO}): ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'
# arg check
if [ $# -ne 1 ] && [ $# -ne 2 ]; then
    echo "$0 <manifest> [<package_metadata>]"
    exit -1
fi
manifest=$(readlink -f $1)
record_package_metadata="${2:-"None"}"

# Everything we need is there already, just replace newline to spaces
cmdline=$(cat ${manifest} | sed ':a;N;$!ba;s/\n/ /g')

if [ -n "${cmdline}" ];then
    # Update the database to understand the manifest
    apt-get update

    if [[ "${record_package_metadata}" != "None" ]]; then
        # Store a list of packages already installed on host
        echo "Recording packages installed on host..."
        file_buffer="base_packages:\n"
        packages=$(dpkg-query --show --showformat='${db:Status-Status}:::${Package}\n' 2> /dev/null)
        while IFS= read -r line; do
            status=${line%:::*}
            package=${line#*:::}
            if [[ ${status} == "installed" ]]; then
                file_buffer+="    - ${package}\n"
            fi
        done <<< "$packages"
        echo "Done"
    fi

    # No prompts console or ncurses
    export DEBIAN_FRONTEND=noninteractive
    # We are ready, call apt-get with no-recommends, un-auth
    # In this phase, the packages to be updated are updated, new packages are installed
    # Existing packages without updates, no change to them
    apt-get --no-install-recommends --allow-unauthenticated --assume-yes install ${cmdline}

    if [[ "${record_package_metadata}" != "None" ]]; then
        # Store metadata of all packages installed on host
        echo "Processing installed packages..."
        file_buffer+="\ninstalled_packages:\n"
        packages=$(dpkg-query --show --showformat=':::${db:Status-Status}:::${Package}:::${Installed-Size}:::${Depends}:::\n${db-fsys:Files}\n' 2> /dev/null)
        state="deb_info"
        while IFS= read -r line; do
            if [[ "$line" == ":::"* ]]; then
                state="deb_info"
                temp=${line#*:::}
                status=${temp%%:::*}  && temp=${temp#*:::}
                if [[ ${status} == "installed" ]]; then
                    package=${temp%%:::*} && temp=${temp#*:::}
                    size=${temp%%:::*}    && temp=${temp#*:::}
                    depends=${temp%%:::*}

                    file_buffer+="    ${package}:\n"
                    file_buffer+="        size: ${size}\n"
                    file_buffer+="        depends: ${depends}\n"
                fi
            else
                old_state=${state}
                state="deb_files"
                if [[ "$old_state" != "deb_files" ]]; then
                    file_buffer+="        files:\n"
                fi
                file_buffer+="            - ${line}\n"
            fi
        done <<< "$packages"
        echo "Done"
        printf "${file_buffer}" > ${record_package_metadata}
    fi
fi
