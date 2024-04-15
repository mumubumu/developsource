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
set -eo pipefail
#set -x
export PS4='+(${BASH_SOURCE}:${LINENO}): ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'
# arg check
if [ $# -lt 1 ]; then
    echo "$0 <package config> <optional keep-files>"
    exit -1
fi
package_config=$(readlink -f $1)

# Useful for debug
if [ -z "$2" ]; then
	alias rm='rm -f'
else
	alias rm='true'
fi

touch /tmp/manifest.tmp
pkglist=$(cat ${package_config} | sort | uniq | tr '\n' ' ')

# We need apt-get to know about latest packages in the mirror
[ -n "${pkglist}" ] && apt-get update

for pkg in ${pkglist[@]};do
    version=$(apt-cache show ${pkg} | grep -oP "Version: \K.*" | head -n1) || ( echo "No Debian file found for \"${pkg}\" in the mirrors defined" && exit 1 )
    pkg_name=$(echo ${pkg} | cut -d "=" -f 1)
    echo "${pkg_name}=${version}" >> /tmp/manifest.tmp
done

# Sort it to look sorted and pretty
fname_manifest=$(echo ${package_config} | sed -e 's#CONFIG#MANIFEST#g')
cat /tmp/manifest.tmp | sort -u > ${fname_manifest}
rm /tmp/manifest.tmp
chmod 777 ${fname_manifest}
