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
sudo mkdir -p ${FILESYSTEM_WORK_DIR}/etc/ssh
pushd ${FILESYSTEM_WORK_DIR}/etc/ssh
sudo touch ssh_host_rsa_key ssh_host_dsa_key ssh_host_ecdsa_key ssh_host_ed25519_key
popd
