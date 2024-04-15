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
sudo mkdir -p ${FILESYSTEM_WORK_DIR}/usr/share/keyrings/
if ls ${FILESYSTEM_WORK_DIR}/var/cuda*repo*/*.gpg 2>/dev/null; then
    sudo cp ${FILESYSTEM_WORK_DIR}/var/cuda*repo*/*.gpg ${FILESYSTEM_WORK_DIR}/usr/share/keyrings/
fi
if ls ${FILESYSTEM_WORK_DIR}/var/cudnn*repo*/*.gpg 2>/dev/null; then
    sudo cp ${FILESYSTEM_WORK_DIR}/var/cudnn*repo*/*.gpg ${FILESYSTEM_WORK_DIR}/usr/share/keyrings/
fi
