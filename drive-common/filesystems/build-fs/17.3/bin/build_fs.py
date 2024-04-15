#!/usr/bin/python3
#
# Copyright (c) 2020-2023, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
#
# NVIDIA CORPORATION, its affiliates and its licensors retain all intellectual
# property and proprietary rights in and to this software, related
# documentation and any modifications thereto.  Any use, reproduction,
# disclosure or distribution of this software and related documentation
# without an express license agreement from NVIDIA CORPORATION or its
# affiliates is strictly prohibited.

import errno
import sys
import os
import json
import yaml
import tempfile
import shutil
import atexit
import shlex
import math
import re
import logging
from utils import (md5, raise_error_and_exit, get_compression_tool, is_text,
                   deep_dict_update)
from executor import Executor
import gzip
from copy import copy
from collections import OrderedDict
from distutils.version import LooseVersion
from optparse import OptionParser
from subprocess import PIPE
import importlib.util

# ==============================
# Tool Dependencies and Versions
# ==============================
VERSION = "17.3.0-deca34cb"
SAFETY_AFFIX = ""
NV_BUILD_FS_STRICT_DEP = os.environ.get('NV_BUILD_FS_STRICT_DEP', "0")
if SAFETY_AFFIX == "-safety":
    NV_BUILD_FS_STRICT_DEP = "1"
if int(NV_BUILD_FS_STRICT_DEP):
    COPYTARGET_VERSION = "1.4.10-5dd999b6"
else:
    COPYTARGET_VERSION = "1"

# ============================
# Default Constants
# ============================
NV_FS_BASE_DIR = "/opt/nvidia/driveos/common/filesystems/"
BUILD_FS_DIR = (NV_FS_BASE_DIR + "/build-fs" + SAFETY_AFFIX
                + "/" + VERSION + "/")
BUILD_FS_ENV = BUILD_FS_DIR + "build-fs.config"
COPYTARGET = (NV_FS_BASE_DIR + "/copytarget" + SAFETY_AFFIX
              + "/" + COPYTARGET_VERSION + "/copytarget.py")
BACKUP_TAG = ".backup"
TAR_PRESERVE_ARGS = "-p --same-owner --numeric-owner --xattrs "
CP_PRESERVE_ARGS = "-a "
TARGETFS_DIR = "/targetfs/"
NOT_EXISTS = "/not/exists/"
TAR_EXT = ".tar"
TAR_REGEX = r"[^ ]+\.tar(.gz|.Z|.bz2|.xz|.lzma|)$"
TAR_COMPRESS = ".bz2"
IMG_EXT = ".img"
IMG_REGEX = r"[^ ]+\.img($|.tar)"
YAML_EXT = ".yaml"
ROOTFS_MOUNT_DIR = "/rootfs_mount/"
LINUX_DEFAULT_IMAGE_SIZE = 17179869184   # 16 GB
QNX_DEFAULT_IMAGE_SIZE = 4294967296     # 4 GB
PYTHON3 = "/usr/bin/python3"
PASSWD_FILE = "/etc/passwd"
LOG_EXT = ".log"

# ============================
# Linux Specific
# ============================
DEB_HELPER_DIR = "/helpers/"
QEMU_PATH = "/usr/bin/"
QEMU_BIN = "/usr/bin/qemu-aarch64-static"
HOSTNAME_FILE = "/etc/hostname"
HOSTS_FILE = "/etc/hosts"
LOCALHOST_IP = "127.0.0.1"
SOURCES_LIST = "/etc/apt/sources.list"
RESOLV_CONF = "/etc/resolv.conf"
TMPDIR = "/tmp/"
LINUX_ROOTFS_MANIFEST_DIR = "/etc/nvidia/rootfilesystem-manifest/"
LINUX_ROOTFS_MANIFEST_LINK = "driveos-rfs.MANIFEST.json"
EXT_DATA = [
    (1, 65536, 2097151, "floppy", 4096, 8192, 128, 0),
    (2, 2097152, 3145727, "floppy", 4096, 8192, 128, 1024),
    (3, 3145728, 33554431, "small", 4096, 4096, 128, 1024),
    (4, 33554432, 268435455, "small", 4096, 4096, 128, 4096),
    (5, 268435456, 536870911, "small", 4096, 4096, 128, 8192),
    (6, 536870912, 1073741823, "default", 4096, 16384, 256, 4096),
    (7, 1073741824, 2147483647, "default", 4096, 16384, 256, 8192),
    (8, 2147483648, 4294967295, "default", 4096, 16384, 256, 16384),
    (9, 4294967296, 4398046511103, "default", 4096, 16384, 256, 32768),
    (10, 4398046511104, 17592186044415, "big", 4096, 32768, 256, 32768),
    (11, 17592186044416, -1, "huge", 4096, 65536, 256, 32768),
]
EXT_BACKWARDS_COMPAT_OPT = " -O ^metadata_csum "
SYS_UID_MIN, SYS_UID_MAX = 1, 999
SYS_GID_MIN, SYS_GID_MAX = 1, 999

# ============================
# QNX Specific
# ============================
IFS_IMG_EXT = ".bin"
BUILD_FILE_EXT = ".build"

# Ensure Environment can only be overridden by build-fs config
# And export variables that might be required for helper scripts
set_vars = ['MY_DIR', 'VERSION', 'BUILD_FS_DIR', 'QEMU_PATH', 'PYTHON3']
unset_vars = [
    'QEMU_PATH', 'BUILD_FS_DIR', 'REQUIRED_VARIABLES', 'COPYTARGET', 'PYTHON3'
    ]


class Environment:
    """
    Class for controlling Build-FS runtime environment.
    """
    @staticmethod
    def exit_if_not_defined(variables):
        """
        Exits Build-FS if variable is not defined in its environment.

        Parameters
        ----------
        variables   : list
                      List of environment variable names.
        """
        for variable in variables:
            if os.getenv(variable) is None:
                raise_error_and_exit(variable + " is not defined.")

    @staticmethod
    def set(variable, value):
        """
        Sets the variable to a value in Build-FS's environment.

        Parameters
        ----------
        variable    : str
                      Environment variable name.
        value       : str
                      Environment variable value.
        """
        os.environ[variable] = value

    @staticmethod
    def get(variable):
        """
        Returns the value for the variable in Build-FS's environment.

        Parameters
        ----------
        variable    : str
                      Environment variable name.

        Returns
        -------
        str
            Environment variable value.
        """
        return os.getenv(variable)

    @staticmethod
    def set_exist(variables):
        """
        Exports given local variables to Build-FS's environment.

        Parameters
        ----------
        variables   : list
                      List of local variable names(str).
        """
        for variable in variables:
            Environment.set(variable, eval(variable))

    @staticmethod
    def source(file_path, section=None):
        """
        Exports variables provided in Build-FS's CONFIG file(json).

        Parameters
        ----------
        file_path   : str
                      Path to Build-FS CONFIG json file.
        section     : str
                      Section of the CONFIG file to be exported.
                      (default is None). If None, source the
                      environment variables outside every section
                      in the CONFIG.

        """
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                jsondata = json.load(f, object_pairs_hook=OrderedDict)
            except ValueError as error:
                logging.error(error)
                raise_error_and_exit(
                        "Invalid JSON syntax in file: '"
                        + os.path.abspath(file_path) + "'")
            if section is None:
                config = jsondata
            elif section in jsondata:
                config = jsondata[section]
            else:
                raise_error_and_exit(
                        "Section: '" + section + "' not present in the"
                        + " environment file: " +
                        os.path.abspath(file_path))
            for key in config.keys():
                if isinstance(config[key], str):
                    os.environ[key] = os.path.expandvars(config[key])

    @staticmethod
    def unset(variables):
        """
        Function shall unset variables in Build-FS's environment.

        Parameters
        ----------
        variables   : str
                      List of environment variables to be unset.
        """
        for variable in variables:
            if variable in os.environ.keys():
                del os.environ[variable]


class BuildFS:
    """
    Class for Generating Filesystem Image.
    """
    def __init__(self, options, json_file=None, json_str=None):
        """
        Build-FS Constructor.

        Parameters
        ----------
        options     : Values
                      Values class is from optparse module.
                      Controls workflow of Build-FS.
                      Obtained from function build_fs.define_options.
        json_file   : str
                      Path to the json file given as input to Build-FS.
        json_str   : str
                     Json file represented as a string.

        Returns
        -------
        BuildFS
            Build-FS object is returned.
        """
        self.options = options
        self.json_file = json_file
        if options.work_folder:
            self.work_dir = options.work_folder
        else:
            self.work_dir = tempfile.mkdtemp() + "/"
        if options.filesystem_work_folder:
            self.filesystem_work_dir = options.filesystem_work_folder
        else:
            self.filesystem_work_dir = self.work_dir + TARGETFS_DIR
        os.makedirs(self.filesystem_work_dir, exist_ok=True)
        self.executor = Executor(self.work_dir, self.filesystem_work_dir)
        self.parser = FileParser(json_file=json_file, json_str=json_str)
        self.output_name = self.parser.get_output()
        self.leaf_output_name = self.output_name
        self.output = "{outfolder}/{output_name}".format(
                outfolder=options.output_folder,
                output_name=self.output_name)
        self.json_manifest_file = "{outfolder}/{op}.MANIFEST.json".format(
                outfolder=options.output_folder,
                op=self.output_name)
        os.makedirs(options.output_folder, exist_ok=True)
        self.filesystem_type = self.parser.get_filesystem_type()
        self.fs_base = self.parser.get_base()
        self.p_config = None
        if self.fs_base:
            self.fs_base = os.path.expandvars(
                                self.parser.get_base())
            if not os.path.isabs(self.fs_base):
                # Config file having Base=<config> entry must either be
                # an absolute path or it's calling config must be a file
                # and not a stream (json_file!= None)
                if not self.json_file:
                    raise_error_and_exit(
                        "Base cannot be set a relative path when input "
                        + "config is a stream.")
                self.fs_base = os.path.join(os.path.dirname(self.json_file),
                                            self.fs_base)
            if is_text(self.fs_base):
                self.p_config = self.fs_base
                self.fs_base = None
        self.rfs_ops = RootfsOperations(
                self.fs_base, self.output,
                self.parser.get_filesystem_cleanup_paths(), self.work_dir,
                self.filesystem_work_dir)
        self.leased_space = FSLeasedSpace(options.size_limits_file, self)
        self.pre_install_exec = ScriptExecutor(
                self.parser.get_pre_installs(), self.executor)
        self.cpt_exec = CopyTargetExecutor(
                self.parser.get_copytargets(), self.options.nv_workspace,
                self.options.copytarget_source_type,
                self.filesystem_work_dir)
        self.args = (" --filesystem-type {fstype} ").format(
                            fstype=self.filesystem_type)
        self.mount_point_config = None
        if self.parser.get_mount_point_config() is not None:
            self.mount_point_config = self.parser.get_mount_point_config()
        self.digest_metadata_config = None
        if self.parser.get_digest_metadata() is not None:
            self.digest_metadata_config = self.parser.get_digest_metadata()
            self.digest_metadata_config["metadataFileDirectory"] = \
                "{output_dir}/metadata/{output_name}/"\
                .format(output_dir=options.output_folder,
                        output_name=self.output_name)
        self.cpt_exec.set_args(self.args)
        self.is_leaf_build_FS = True
        self.is_root_build_FS = True
        self.post_install_exec = ScriptExecutor(
                self.parser.get_post_installs(), self.executor)
        if self.p_config:
            self.is_root_build_FS = False
            self.p_options = copy(self.options)
            self.p_options.filesystem_work_folder = (
                                self.filesystem_work_dir)
            self.p_build_fs = self.init_p_build_fs()
            self.p_build_fs.mount_point_config = self.mount_point_config
            self.p_build_fs.digest_metadata_config = \
                self.digest_metadata_config
            self.p_build_fs.leaf_output_name = self.leaf_output_name
            if self.p_build_fs.leased_space.uses_base is True:
                self.leased_space.uses_base = True
                self.leased_space.change_target_size_file(
                    options.
                    output_folder + "/fs_layer_size_" + self.
                    output_name + ".yaml")
                self.leased_space.base_file = \
                    self.p_build_fs.leased_space.base_file
            else:
                self.p_build_fs.leased_space.change_target_size_file(
                    self.leased_space.target_size_file)
            self.p_build_fs.is_leaf_build_FS = False
        else:
            self.p_options = None
            self.p_build_fs = None
        self.log_level = getattr(logging, options.log_level.upper())
        atexit.register(self.cleanup)

    def init_p_build_fs(self):
        return BuildFS(self.p_options, self.p_config)

    def pre_build(self):
        """Execute Pre-Build steps."""
        return

    def build(self):
        """Execute Build steps."""
        return

    def post_build(self):
        """Execute Post-Build steps."""
        return

    def process_output(self):
        """Execute Process-Output steps."""
        return

    def delete_workspace(self):
        """Removes Build-FS WORK_DIR and its contents."""
        # Execute rm -rf on workdir
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def cleanup(self):
        """Executes cleanup required on Build-FS normal/erroneous exit."""
        logging.info("\nExecuting Cleanup Routine for Build-FS on Exit.\n")
        if self.filesystem_work_dir:
            self.executor.cleanup_arm64_chroot()
        keep_workdir = Environment.get('KEEP_BUILD_FS_WORKDIR')
        if self.work_dir != "" and keep_workdir != "1":
            self.delete_workspace()

    def convert_to_manifest(self):
        """
        Creates a version frozen MANIFEST.json file to the input CONFIG.json
        file, with which identical filesystem image could be regenerated.
        """
        return

    def process_parent(self):
        # Process only if Parent Build-FS object exists
        if self.p_build_fs:
            # Recurse on Parent Build-FS
            self.p_build_fs.pre_build()
            self.p_build_fs.build()
            self.p_build_fs.post_build()
            if self.options.generate_intermediate == "yes":
                self.p_build_fs.process_output()

    def process_associated_fs(self):
        # Process empty base-class associated build-fs objs
        return


class LinuxBuildFS(BuildFS):
    """
    Inherited class for generating Linux filesystem image.
    """
    def __init__(self, options, json_file=None, build_fs_dir=NOT_EXISTS,
                 json_str=None):
        """
        LinuxBuildFS Constructor.

        Parameters
        ----------
        options         : Values
                          Values class is from optparse module.
                          Controls workflow of Build-FS.
                          Obtained from function build_fs.define_options.
        json_file       : str
                          Path to the json file given as input to Build-FS.
        build_fs_dir   : str
                          Path to Build-FS helper scripts.
                          (default is /not/exists/)
        json_str   : str
                     Json file represented as a string.

        Returns
        -------
        LinuxBuildFS
            LinuxBuildFS object is returned.
        """
        self.build_fs_dir = build_fs_dir
        super().__init__(options, json_file=json_file, json_str=json_str)
        self.filesystem_mount_dir = self.work_dir + ROOTFS_MOUNT_DIR
        os.mkdir(self.filesystem_mount_dir, mode=0o755)

        self.parser = LinuxFileParser(json_file=json_file, json_str=json_str)
        self.rfs_ops = LinuxRootfsOperations(
                self.fs_base, self.output,
                self.parser.get_filesystem_cleanup_paths(), self.work_dir,
                self.filesystem_work_dir, self.filesystem_mount_dir,
                self.parser.get_mirrors(), self.executor,
                fs_include_paths=self.parser.get_filesystem_include_paths())
        self.deb_manager = DebianPackageManager(
                self.parser.get_ubuntu_distro(),
                self.parser.get_debian_packages(),
                self.executor, build_fs_dir + DEB_HELPER_DIR)
        self.ug_manager = UserGroupManager(
                self.parser.get_users(), self.parser.get_groups(),
                self.parser.get_memberships(), self.executor)
        self.image_final_size = self.parser.get_image_size()
        self.image = ExtImage(self.output, 0, self.image_final_size)
        self.deb_manifest_file = "{outfolder}/{manifestname}.manifest".format(
                outfolder=options.output_folder,
                manifestname=self.output_name)
        # Process associated configs
        # Initialize linux-specific associated build-fs objs
        # init_a_build_fs sets up both a_*configs and a_build_fs
        self.a_build_fs = []
        self.a_build_fs_configs = []
        self.init_a_build_fs()
        if self.options.generate_target_size_file == "yes":
            self.deb_manager.package_metadata = "/tmp/debPackagesRecord.txt"
            if Environment.get("NV_CHECK_FS_DEBIAN_SIZE_LIMITS") == "no":
                self.deb_manager.package_metadata = None
                logging.warning("The logic to check the sizes of Debians "
                                "has been disabled because "
                                "'NV_CHECK_FS_DEBIAN_SIZE_LIMITS' is set to "
                                "'no'")
            self.deb_manager.leased_space = self.leased_space

    def init_p_build_fs(self):
        return LinuxBuildFS(
                    self.p_options, self.p_config, self.build_fs_dir)

    def init_a_build_fs(self):
        """
        Execute process for associated configs
        Execute build-fs for the stored associated configs.
        Process associated configs and store the absolute paths of
        associated configs here.
        parser obj pre-checked before calling this init
        """
        a_configs = self.parser.get_associated_fs()
        # If no associated configs, return
        if not a_configs:
            logging.info("No Associated filesystem builds requested in {}"
                         .format(self.json_file))
            return

        # Populate associated configs list
        for config in a_configs:
            config = os.path.expandvars(config)
            # Config file having Base=<config> entry must either be
            # an absolute path or it's calling config must be a file
            # and not a stream (json_file!= None)
            if not os.path.isabs(config):
                if not self.json_file:
                    raise_error_and_exit(
                        "Associated config entry cannot be set a relative "
                        + "path when input config is a stream.")
            self.a_build_fs_configs.append(os.path.join(os.path.dirname(
                self.json_file), config))

        # Process the associated configs
        logging.info("Instancing build-fs on associated configs "
                     + "in {}.".format(self.json_file))
        a_fs_work_dir_index = 0
        for a_config in self.a_build_fs_configs:
            # Clone associater's options obj to associatee obj
            a_options = copy(self.options)
            # Associated FS work_dir is a subdir AssociatedBuilds to main
            # workspace. Update work_folder and filesystem_work_folder
            # from cloned options
            a_options.work_folder = os.path.join(
                    self.work_dir + "/AssociatedBuilds",
                    str(a_fs_work_dir_index))
            a_options.filesystem_work_folder = "{}{}".format(
                    a_options.work_folder, TARGETFS_DIR)
            # Create new dir for every associated FS & label it by indices
            os.makedirs(a_options.filesystem_work_folder, exist_ok=True)
            a_fs_work_dir_index += 1
            # Set json_path to config being processed
            a_options.json_path = a_config
            # Pass updated associated FS options to create new associated
            # build-fs obj
            build_fs = LinuxBuildFS(
                    a_options, json_file=a_config, build_fs_dir=BUILD_FS_DIR)
            # Add to list of build-fs objections (used for executing builds of
            # associated filesystems.
            self.a_build_fs.append(build_fs)

    def pre_build(self):
        """Execute Pre-Build steps."""
        if self.mount_point_config is not None:
            self.args += (" --mount-point {mp} ").format(
                mp=self.mount_point_config["MountPoint"])
            self.cpt_exec.set_args(self.args)
            if not self.mount_point_config["DestinationIncludesMountPoint"]:
                self.cpt_exec.add_env_var(
                    "NV_COPYTARGET_DESTINATION_INCLUDES_MOUNTPOINT=False")
        if self.options.spreadsheet_file is not None:
            spreadsheet_arg = \
                self.options.spreadsheet_file + ":" + self.leaf_output_name
            self.args += (" --create-spreadsheet {spreadsheet} ").format(
                spreadsheet=spreadsheet_arg)
            if self.options.spreadsheet_meta is not None:
                self.args += (" --spreadsheet-metadata {metadata} ").format(
                    metadata=self.options.spreadsheet_meta)
            self.cpt_exec.set_args(self.args)
        if self.options.generate_target_size_file == "yes":
            if self.is_root_build_FS or self.leased_space.uses_base:
                self.leased_space.reset_target_size_file()
            self.args += (" --target-size-file={} "
                          .format(self.leased_space.target_size_file))
            self.cpt_exec.set_args(self.args)
        Executor.setup_multi_binary_exec()
        self.rfs_ops.extract_rootfs()
        self.process_parent()
        self.rfs_ops.update_fs_apt_sources_list()
        self.rfs_ops.update_resolv_conf()
        self.pre_install_exec.execute_scripts()

    def build(self):
        """Execute Build steps."""
        self.deb_manager.generate_debian_manifest()
        self.deb_manager.build_filesystem()
        if self.ug_manager is not None:
            self.ug_manager.create_users()
            self.ug_manager.create_groups()
            self.ug_manager.set_user_memberships()
        if self.parser.get_hostname():
            self.rfs_ops.edit_hosts(self.parser.get_hostname())
        self.cpt_exec.execute_host_scripts()

    def post_build(self):
        """Execute Post-Build steps."""
        self.rfs_ops.restore_fs_apt_sources_list()
        self.rfs_ops.restore_resolv_conf()
        if self.post_install_exec is not None:
            self.post_install_exec.execute_scripts()
        # Apply SELinux attributes now as FS content is finalized
        self.rfs_ops.apply_selinux_attributes(self.parser.get_selinux_info())
        self.convert_to_manifest()
        if self.options.manifest_only:
            exit(0)
        mf_dir = self.filesystem_work_dir + LINUX_ROOTFS_MANIFEST_DIR
        os.makedirs(mf_dir, exist_ok=True)
        for mf in os.listdir(mf_dir):
            os.remove(os.path.join(mf_dir, mf))
        shutil.copy2(self.json_manifest_file,
                     self.filesystem_work_dir + LINUX_ROOTFS_MANIFEST_DIR)
        # Create driveos-rfs.MANIFEST.json symlink to current manifest.
        manifest_basename = os.path.basename(self.json_manifest_file)
        manifest_dirname = self.filesystem_work_dir + LINUX_ROOTFS_MANIFEST_DIR
        os.symlink(manifest_basename,
                   manifest_dirname + LINUX_ROOTFS_MANIFEST_LINK)
        # Create fstab and copy to filesystem
        self.update_filesystem_fstab()
        # Process FSInclude list
        self.rfs_ops.process_fsinclude_rootfs()
        # Execute cleanup only after filesystem content is final
        self.rfs_ops.cleanup_rootfs()
        if self.options.generate_intermediate == "yes":
            self.leased_space.check_target_size_manifest(self.output_name)
        else:
            if self.is_leaf_build_FS:
                self.leased_space.check_target_size_manifest(self.output_name)

    def process_output(self):
        """Execute Process-Output steps."""
        if self.options.create_tar == "yes":
            self.rfs_ops.compress_rootfs()
        if self.options.create_image == "yes":
            filesystem_blks = ExtImage.get_blocks(
                    self.filesystem_work_dir,
                    self.image.block_size)
            self.image.tree_blocks = filesystem_blks
            self.image.create_image()
            self.rfs_ops.copy_to_image()
        # For creating empty image
        if os.path.exists(self.filesystem_work_dir + SOURCES_LIST):
            output = self.deb_manager.get_full_debian_manifest(
                        output_format="human")
            with open(self.deb_manifest_file, 'w', encoding='utf-8') as mfd:
                mfd.writelines(output)
        self.process_associated_fs()

    def cleanup(self):
        """Executes cleanup required on Build-FS normal/erroneous exit."""
        logging.info(
                "\nExecuting Cleanup Routine for Linux Build-FS on Exit.\n")
        if self.filesystem_mount_dir:
            Executor.execute_on_host(
                    'umount', self.filesystem_mount_dir,
                    exit_on_failure=False, silent=True,
                    stderr=PIPE)
        super().cleanup()

    def convert_to_manifest(self):
        """
        Creates a version frozen MANIFEST.json file to the input CONFIG.json
        file, with which identical filesystem image could be regenerated.
        """
        # clone jsondata
        json_data_manifest = OrderedDict(self.parser.json_data)
        # Use read-modify-write copy of jsondata
        old_mf = None
        if self.p_build_fs is not None:
            old_mf = self.p_build_fs.json_manifest_file
        elif self.fs_base is not None:
            mf_dir = self.filesystem_work_dir + LINUX_ROOTFS_MANIFEST_DIR
            if os.path.exists(mf_dir):
                old_mf = sorted(os.listdir(mf_dir))[0]
                old_mf = os.path.join(mf_dir, old_mf)

        json_data_manifest[
                "Mirrors"] = LinuxRootfsOperations.get_full_mirrors(
                                    self.parser.get_mirrors())
        json_data_manifest[
                "CopyTargets"] = CopyTargetExecutor.get_full_cpt(
                                    self.parser.get_copytargets())
        if old_mf:
            old_mf_parser = LinuxFileParser(old_mf)
            json_data_manifest[
                    "OS"] = old_mf_parser.get_os()
            json_data_manifest[
                    "Base"] = old_mf_parser.get_base()
            json_data_manifest[
                    "Mirrors"
                    ] = LinuxRootfsOperations.get_full_mirrors(
                            old_mf_parser.get_mirrors()
                            + self.parser.get_mirrors())
            json_data_manifest[
                    "Users"] = deep_dict_update(
                                        old_mf_parser.get_users(),
                                        self.parser.get_users().copy())
            json_data_manifest[
                    "Groups"] = deep_dict_update(
                                        old_mf_parser.get_groups(),
                                        self.parser.get_groups().copy())
            json_data_manifest[
                    "Memberships"] = deep_dict_update(
                                        old_mf_parser.get_memberships(),
                                        self.parser.get_memberships().copy(),
                                        list_action="append")
            json_data_manifest[
                    "CopyTargets"
                    ] = CopyTargetExecutor.get_full_cpt(
                            old_mf_parser.get_copytargets()
                            + self.parser.get_copytargets())
            json_data_manifest[
                    "FilesystemCleanup"
                    ] = list(set(
                            old_mf_parser.get_filesystem_cleanup_paths()
                            + self.parser.get_filesystem_cleanup_paths()))
            json_data_manifest[
                    "Mounts"
                    ] = deep_dict_update(
                            old_mf_parser.get_mounts(),
                            self.parser.get_mounts().copy())
            json_data_manifest[
                    "FilesystemInclude"
                    ] = list(set(
                            old_mf_parser.get_filesystem_include_paths()
                            + self.parser.get_filesystem_include_paths()))
            json_data_manifest[
                    "AssociatedFilesystems"
                    ] = list(set(
                            old_mf_parser.get_associated_fs()
                            + self.parser.get_associated_fs()))
            json_data_manifest[
                    "DebianPackages"
                    ] = self.deb_manager.get_full_debian_manifest(
                                                output_format="build-fs")
            json_data_manifest[
                    "PreInstalls"] = deep_dict_update(
                                            old_mf_parser.get_pre_installs(),
                                            self.parser.get_pre_installs())
            json_data_manifest[
                    "PostInstalls"] = deep_dict_update(
                                            old_mf_parser.get_post_installs(),
                                            self.parser.get_post_installs())
        # Write out JSON data
        with open(self.json_manifest_file, 'w', encoding='utf-8') as jfd:
            json.dump(json_data_manifest, jfd, indent=4)

    def update_filesystem_fstab(self):
        """
        Takes the filesystem base /etc/fstab, updates it as per build-fs CONFIG
        and copies it to the filesystem.
        """
        # Check if Mounts block, if absent, skip over
        _mount_dict = self.parser.get_mounts()
        if not _mount_dict:
            logging.info("Mounts block not present, skipping fstab updates.")
            return

        # Start with copy of fstab
        _fstab = self.filesystem_work_dir + "/etc/fstab"
        _fstab_new = _fstab + ".build-fs"
        Executor.execute_on_host(
                "cp -v", "{} {}".format(_fstab, _fstab_new),
                exit_on_failure=True)

        # Add mount entries
        fd_fstab = open(_fstab_new, 'a')
        logging.info("Updating fstab {} based on build-fs config".format(
            _fstab_new))
        FS_FSCK_AFTER_RFS = 2
        FS_NO_DUMP = 0
        for mount_point in _mount_dict.keys():
            _fs = _mount_dict[mount_point]
            fstab_entry = "{}\t{}\t{}\t{}\t{}\t{}".format(
                    _fs["Device"],
                    mount_point, _fs["Type"], _fs["MountOptions"],
                    FS_NO_DUMP, FS_FSCK_AFTER_RFS)
            fd_fstab.write("\n{}".format(fstab_entry))
        fd_fstab.write("\n")
        fd_fstab.close()

        # Copy back updated fstab
        Executor.execute_on_host(
                "mv -v", "{} {}".format(_fstab_new, _fstab),
                exit_on_failure=True)

    def process_associated_fs(self):
        """
        Execute process for associated configs
        Execute build-fs for the stored associated configs.
        """
        # If no associated configs, return
        if not self.a_build_fs:
            logging.info("No Associated filesystem builds requested.")
            return

        # Process the associated configs
        logging.info(
                "Executing build-fs on associated configs in {}.".format(
                    self.json_file))
        for build_fs in self.a_build_fs:
            build_fs.pre_build()
            build_fs.build()
            build_fs.post_build()
            build_fs.process_output()


class QNXBuildFS(BuildFS):

    def __init__(self, options, json_file=None, qnx_build_file=None,
                 qnx_passwd_file=None, qnx_group_file=None, json_str=None):
        """
        QNXBuildFS Constructor.

        Parameters
        ----------
        options         : Values
                          Values class is from optparse module.
                          Controls workflow of Build-FS.
                          Obtained from function build_fs.define_options.
        json_file       : str
                          Path to the json file given as input to Build-FS.
        qnx_build_file  : str
                          Path to build file used for creating QNX image,
                          required by mkxfs.
        qnx_passwd_file : str
                          Path to QNX passwd file, for finding the UID of the
                          files to be copied by CopyTarget.
        qnx_group_file  : str
                          Path to QNX group file, for finding the GID of the
                          files to be copied by CopyTarget.
        json_str   : str
                     Json file represented as a string.

        Returns
        -------
        QNXBuildFS
            QNXBuildFS object is returned.
        """
        self.qnx_passwd_file = qnx_passwd_file
        self.qnx_build_file = qnx_build_file
        self.qnx_group_file = qnx_group_file
        options.filesystem_work_folder = "/"
        super().__init__(options, json_file=json_file, json_str=json_str)
        if self.qnx_build_file is None:
            self.qnx_build_file = self.output + BUILD_FILE_EXT
            if os.path.exists(self.qnx_build_file):
                os.remove(self.qnx_build_file)
        self.cpt_exec.qnx_build_file = self.qnx_build_file
        self.parser = QNXFileParser(json_file=json_file, json_str=json_str)
        self.manifest_file = self.output + '.manifest'
        self.rfs_ops = None
        bfh = self.parser.get_buildfile_header_files()
        self.cpt_exec.buildfile_header_files = bfh

        self.image_type = self.parser.get_image_type()
        if self.p_build_fs is None:
            # If user has NOT provided a buildfile header, insert a default
            # buildfile header
            for h in bfh:
                if not isinstance(h, str):
                    raise_error_and_exit(
                            "BuildFileHeaderFiles: Value " +
                            "must be a list of strings")
            self.cpt_exec.buildfile_header_arg = \
                ' '.join(["--buildfile-header-file={}"
                         .format(h) for h in bfh])
        else:
            # Build-FS header is only defined for the first call to
            # CopyTarget
            self.cpt_exec.buildfile_header_arg = ""
        # For QNX, parent directories should not be automatically created
        # when it is not explicitly listed in CopyTarget Manifest. However,
        # --autocreate-parent-directories is set to True to handle cases
        # where CopyTarget needs to create buildfiles where a parent
        # directory is already defined. Mkxfs is called with -D option
        # to ensure that all directories are listed in CopyTarget manifest.
        self.args += (" --user-identifier-dictionary={passwd} " +
                      " --group-identifier-dictionary={group} " +
                      " --autocreate-parent-directories=True " +
                      " --create-buildfile={buildfile}").format(
                      passwd=self.qnx_passwd_file,
                      group=self.qnx_group_file,
                      buildfile=self.qnx_build_file)
        if self.image_type == "XFS":
            # Build XFS Image
            self.image = QNX6Image(
                self.output, self.qnx_build_file, self.manifest_file,
                self.log_level)
        elif self.image_type == "IFS":
            self.cpt_exec.default_workspace = '" "'
            self.image = QNXIFSImage(
                self.output, self.qnx_build_file, self.manifest_file,
                self.log_level)
        else:
            raise_error_and_exit(
                "Urecognized type: {}".format(self.image_type))

        self.cpt_exec.set_args(self.args)

    def init_p_build_fs(self):
        if self.qnx_build_file is None:
            self.qnx_build_file = self.output + BUILD_FILE_EXT
            if os.path.exists(self.qnx_build_file):
                os.remove(self.qnx_build_file)
        return QNXBuildFS(
                    self.p_options, self.p_config, self.qnx_build_file,
                    self.qnx_passwd_file, self.qnx_group_file)

    def pre_build(self):
        """Execute Pre-Build steps."""
        if self.mount_point_config is not None:
            self.args += (" --mount-point {mp} ").format(
                mp=self.mount_point_config["MountPoint"])
            self.cpt_exec.set_args(self.args)
            if not self.mount_point_config["DestinationIncludesMountPoint"]:
                self.cpt_exec.add_env_var(
                    "NV_COPYTARGET_DESTINATION_INCLUDES_MOUNTPOINT=False")
        if self.digest_metadata_config is not None:
            if self.is_leaf_build_FS:
                logging.info("Digest of files in FS will be created. "
                             "Config used: {}"
                             .format(json.dumps(self.digest_metadata_config)))
            # Delete the Golden Metadata file (if it exists)
            if os.path.exists(os.path.expandvars(
                    self.digest_metadata_config["goldenDigestFile"])):
                logging.debug("Removed existing Golden metadata file {}"
                              .format(os.path.expandvars(
                                  self.digest_metadata_config[
                                      "goldenDigestFile"])))
                os.remove(os.path.expandvars(
                    self.digest_metadata_config["goldenDigestFile"]))
            digestMetadataConfigJSON = os.path.join(
                self.work_dir, "digestMetadata.config.json")
            with open(digestMetadataConfigJSON, "w", encoding="utf-8") as f:
                json.dump(self.digest_metadata_config, f)
            self.args += (" --digest-metadata-config {dm} ").format(
                dm=digestMetadataConfigJSON)
            self.cpt_exec.set_args(self.args)
        if self.options.spreadsheet_file is not None:
            spreadsheet_arg = \
                self.options.spreadsheet_file + ":" + self.leaf_output_name
            self.args += (" --create-spreadsheet {spreadsheet} ").format(
                spreadsheet=spreadsheet_arg)
            if self.options.spreadsheet_meta is not None:
                self.args += (" --spreadsheet-metadata {metadata} ").format(
                    metadata=self.options.spreadsheet_meta)
            self.cpt_exec.set_args(self.args)
        if self.options.generate_target_size_file == "yes":
            if self.is_root_build_FS or self.leased_space.uses_base:
                self.leased_space.reset_target_size_file()
            self.args += (" --target-size-file={} "
                          .format(self.leased_space.target_size_file))
            self.cpt_exec.set_args(self.args)
        self.process_parent()
        self.pre_install_exec.execute_scripts()

    def build(self):
        """Execute Build steps."""
        self.cpt_exec.execute_host_scripts()

    def post_build(self):
        """Execute Post-Build steps."""
        if self.image_type == "XFS":
            self.update_image_size_in_build_file()
        if self.post_install_exec is not None:
            self.post_install_exec.execute_scripts()
        self.convert_to_manifest()
        if self.options.generate_intermediate == "yes":
            self.leased_space.check_target_size_manifest(self.output_name)
        else:
            if self.is_leaf_build_FS:
                self.leased_space.check_target_size_manifest(self.output_name)

    def process_output(self):
        """Execute Process-Output steps."""
        if self.options.create_image == "yes":
            self.image.createImage()

    def convert_to_manifest(self):
        """
        Creates a version frozen MANIFEST.json file to the input CONFIG.json
        file, with which identical filesystem image could be regenerated.
        """
        # clone json_data
        json_data_manifest = OrderedDict(self.parser.json_data)
        old_json_manifest = None
        # If Parent CONFIG's generated MANIFEST.json exists, use that
        if self.p_build_fs is not None:
            old_json_manifest = self.p_build_fs.json_manifest_file
        elif self.fs_base is not None:
            base = self.fs_base
            old_json_manifest = re.sub(r"(\.tar\.[^.]*|/)$", "", base)
            old_json_manifest = old_json_manifest + '.MANIFEST.json'

        if old_json_manifest:
            old_json_mf_parser = QNXFileParser(old_json_manifest)
            l1 = old_json_mf_parser.get_copytargets()
            l2 = self.parser.get_copytargets()
            json_data_manifest[
                    "CopyTargets"
                    ] = l1 + l2
            json_data_manifest[
                    "Base"] = old_json_mf_parser.get_base()
        # Write out JSON data
        with open(self.json_manifest_file, 'w', encoding='utf-8') as jfd:
            json.dump(json_data_manifest, jfd, indent=4)

    def cleanup(self):
        """Executes cleanup required on Build-FS normal/erroraneous exit."""
        logging.info("\nExecuting Cleanup Routine for QNX Build-FS on Exit.\n")
        if self.work_dir != "":
            self.delete_workspace()

    def update_image_size_in_build_file(self):
        with open(self.qnx_build_file, "a") as f:
            f.write("[num_sectors={}]\n".format(
                    str(math.ceil(self.parser.get_image_size() / 512))))


class UserGroupManager:
    """
    Class for managing Users, Groups, Memberships in target Linux filesystem
    using useradd, groupadd, usermod binaries in the target filesystem
    via chroot.
    """
    def __init__(self, users=None, groups=None, memberships=None,
                 executor=None):
        """
        UserGroupManager Constructor.

        Parameters
        ----------
        users       : dict
                      dict of users to be added to target filesystem.
                      key --> username, val --> (uid, passwd).
                      (default is None)
        groups      : dict
                      dict of groups to be added to target filesystem.
                      key --> groupname, val --> gid.
                      (default is None)
        memberships : dict
                      dict of memberships to be added to users in target
                      filesystem.
                      key --> username, val --> list(groupnames)
                      (default is None)
        executor    : Executor
                      Executor object for running commands on the target
                      filesystem chroot.
                      (default is None)

        Returns
        -------
        UserGroupManager
            UserGroupManager object is returned
        """
        self.users = users
        self.groups = groups
        self.user_memberships = memberships
        self.executor = executor

    def username_from_uid(self, uid):
        """
        get username from UID in the target filesystem

        Parameters
        ----------
        user_id     : str
                      uid in the target filesystem.

        Returns
        -------
        username    : str
                      Returns username based on the UID in the target
                      filesystem.
        """
        shell_stream = self.executor.execute_for_arm64(
                            'grep', '-E ":' + uid + ':"' +
                            ' /etc/passwd',
                            stdout=PIPE, stderr=PIPE,
                            exit_on_failure=False)
        output = shell_stream["stdout"].decode("utf-8")
        output = output.split("\t")[0]
        username = output.split(":")[0]
        return username

    def groupname_from_gid(self, gid):
        """
        get groupname from GID in the target filesystem

        Parameters
        ----------
        gid    : str
                 gid in the target filesystem.

        Returns
        -------
        groupname    : str
                       Returns groupname based on the gid in the target
                       filesystem.
        """
        shell_stream = self.executor.execute_for_arm64(
                            'grep', '-E ":' + gid + ':"'
                            + ' /etc/group',
                            stdout=PIPE, stderr=PIPE,
                            exit_on_failure=False)
        output = shell_stream["stdout"].decode("utf-8")
        output = output.split("\t")[0]
        groupname = output.split(':')[0]
        return groupname

    def if_username_exists(self, username):
        """
        Checks if the user exists in the target filesystem.

        Parameters
        ----------
        username    : str
                      username to be checked in the target filesystem.

        Returns
        -------
        bool
            Returns True/False if the username 'exists'/'doesn't exist' in the
            target filesystem.
        """
        shell_stream = self.executor.execute_for_arm64(
                            'grep', '-q -E "^' + username + ':"' +
                            ' /etc/passwd',
                            stdout=PIPE, stderr=PIPE,
                            exit_on_failure=False)
        return not shell_stream["rc"]

    def if_user_id_exists(self, uid):
        """
        Checks if the user exists in the target filesystem.

        Parameters
        ----------
        uid    : str
                 uid to be checked in the target filesystem.

        Returns
        -------
        bool
            Returns True/False if the UID 'exists'/'doesn't exist' in the
            target filesystem.
        """
        shell_stream = self.executor.execute_for_arm64(
                            'grep', '-q -E ":' + uid + ':"' +
                            ' /etc/passwd',
                            stdout=PIPE, stderr=PIPE,
                            exit_on_failure=False)
        return not shell_stream["rc"]

    def if_groupname_exists(self, groupname):
        """
        Checks if the group exists in the target filesystem.

        Parameters
        ----------
        groupname   : str
                      groupname to be checked in the target filesystem.

        Returns
        -------
        bool
            Returns True/False if the group 'exists'/'doesn't exist' in the
            target filesystem.
        """
        shell_stream = self.executor.execute_for_arm64(
                            'grep', '-q -E "^' + groupname + ':"'
                            + ' /etc/group',
                            stdout=PIPE, stderr=PIPE,
                            exit_on_failure=False)
        return not shell_stream["rc"]

    def if_group_id_exists(self, gid):
        """
        Checks if the group exists in the target filesystem.

        Parameters
        ----------
        gid   : str
                gid to be checked in the target filesystem.

        Returns
        -------
        bool
            Returns True/False if the group corresponding to GID
            'exists'/'doesn't exist' in the target filesystem.
        """
        shell_stream = self.executor.execute_for_arm64(
                            'grep', '-q -E ":' + gid + ':"'
                            + ' /etc/group',
                            stdout=PIPE, stderr=PIPE,
                            exit_on_failure=False)
        return not shell_stream["rc"]

    def parse_user(self, user):
        """
        Parses the user object in the CONFIG data, and returns the required
        data for adding/modifying that user in the target filesystem.

        Parameters
        ----------
        user        : str
                      key for getting the user object from
                      all users in the CONFIG data.

        Returns
        -------
        list
            Returns a list in the format
            {command, args, user_passwd}
            command: is either useradd/usermod if user doesn't exist/exists.
            args: arguments to the useradd/usermod command.
            user_passwd: password of the user in the target filesystem.
            username: username of the user in the target filesystem.
        """
        user_obj = self.users.get(user)
        if isinstance(user_obj, list):
            uid = user_obj[0]
            user_passwd = user_obj[1]
            # Users entry v1, user itself is the username
            username = user
        elif isinstance(user_obj, dict):
            # Parse uid, username and password
            uid = user_obj.get("UID", None)
            username = user_obj.get("Username", None)
            user_passwd = user_obj.get("Password", None)
            # Defaults set for local user account
            DEFAULT_SHELL, DEFAULT_HOME_PATH, usertype = \
                "/bin/bash", "/home/{}".format(username), ""
            # Classify into system or local user account
            if uid and int(uid) >= SYS_UID_MIN \
                    and int(uid) <= SYS_UID_MAX:
                # Set defaults for system user
                usertype = "--system"
                DEFAULT_SHELL = "/bin/false"
                DEFAULT_HOME_PATH = ""
            # Parse Shell, Home, ExtraOpts
            user_shell = user_obj.get("Shell", DEFAULT_SHELL)
            home_path = user_obj.get("Home", DEFAULT_HOME_PATH)
            # ExtraOpts for special flags like set home but dont
            # create, create user but make user inactive, etc.
            # For experts use only
            extra_opts = user_obj.get("ExtraOpts", None)

        # Mandatory args for usermod/useradd
        # Need usertype only for useradd usecase
        # Need new username for usermod
        useradd_args = "{} ".format(usertype)
        usermod_args = "-l {} ".format(username)

        # only if user's shell is set apply it
        if user_shell:
            useradd_args += "-s {} ".format(user_shell)
            usermod_args += "-s {} ".format(user_shell)

        # only if user's home is set, apply it
        if home_path:
            useradd_args += "-m -d {} ".format(home_path)
            usermod_args += "-m -d {} ".format(home_path)

        # --comment is user a/c's nickname
        useradd_args += "--comment {} ".format(username)
        usermod_args += "--comment {} ".format(username)

        # Check if extra_opts is set, append it
        if extra_opts:
            useradd_args += "{} ".format(extra_opts)
            usermod_args += "{} ".format(extra_opts)

        if uid:
            useradd_args += "-u {} ".format(uid)
        if uid and self.if_user_id_exists(uid):
            command = 'usermod'
            prev_username = self.username_from_uid(uid)
            args = usermod_args + prev_username
            user_obj["PrevUsername"] = prev_username
        else:
            command = 'useradd'
            args = useradd_args + username

        return command, args, user_passwd, username

    def parse_user_self_group(self, user):
        """
        Parses the user object in the CONFIG data, and returns the required
        corresponding self group (For eg. when a normal user 'test' is added,
        the group 'test' is created with same gid as user's uid.)
        data modifying that group in the target filesystem.

        Parameters
        ----------
        user        : str
                      key for getting the user object from
                      all users in the CONFIG data.

        Returns
        -------
        list
            Returns a list in the format
            {command, args}
            command: returns 'groupmod' or None.
            args: arguments to groupmod or None.
        """
        # If user in legacy format (array), return false.
        user_obj = self.users.get(user)
        if not isinstance(user_obj, dict):
            return None, None
        # If PrevUsername is not present,
        # return false as we are in user addition case.
        prev_username = user_obj.get("PrevUsername", None)
        if not prev_username:
            return None, None
        # Check if group named by prev_username
        # (what if username belongs to system user?)
        if not self.if_groupname_exists(prev_username):
            return None, None
        # Group exists and requires renaming so Update groupname args
        username = user_obj.get("Username", None)
        args = "-n {} {}".format(username, prev_username)

        return "groupmod", args

    def set_passwd(self, user_passwd, username):
        """
        Parses the password data from JSON block to understand cleartext or
        hashed-password. Depending on case security action is taken to set
        password correctly.

        Parameters
        ----------
        user_passwd     : str or dict
                          Password string or dictionary password from JSON.
        username        : str
                          username corresponding to which passwd is set.

        Returns
        -------
        int
            0 on success, -1 on failure.
        """
        # Update password if it's a string
        if isinstance(user_passwd, str):
            pass_string = 'echo {user}:{user_passwd}' + \
                          ' | chpasswd'
            passwd_update_cmd = pass_string.format(
                user_passwd=user_passwd, user=username)
            ret = self.executor.execute_for_arm64(
                'bash', '-c ' + '\"' + passwd_update_cmd + '\"', silent=True)
        elif isinstance(user_passwd, dict):
            passwd_args = "-p {} {}".format(
                    user_passwd["HashedPassword"], username)
            ret = self.executor.execute_for_arm64(
                'usermod', passwd_args, silent=True)
        else:
            logging.error("Bad password entry from CONFIG.")
            return -1
        return ret["rc"]

    def create_users(self):
        """Adds required users to target filesystem."""
        if not self.users:
            return
        for user in self.users.keys():
            command, args, user_passwd, username = self.parse_user(user)
            # Create/update users
            self.executor.execute_for_arm64(command, args)

            # Get self group data from parsing, command here is different from
            # above
            command, args = self.parse_user_self_group(user)
            if command:
                self.executor.execute_for_arm64(command, args)

            # set password for username
            if user_passwd:
                self.set_passwd(user_passwd, username)

    def parse_group(self, group):
        """
        Parses the group object in the CONFIG data, and returns the required
        data for adding/modifying that group in the target filesystem.

        Parameters
        ----------
        user        : str
                      groupname key for getting the group object from
                      all groups in the CONFIG data.

        Returns
        -------
        list
            Returns a list in the format
            {command, args}
            command: is either groupadd/groupmod if group doesn't exist/exists.
            args: arguments to the groupadd/groupmod command.
        """
        group_obj = self.groups.get(group)
        if isinstance(group_obj, str):
            gid = group_obj
            group_name = group
        elif isinstance(group_obj, dict):
            gid = group_obj.get("GID", None)
            group_name = group_obj.get("Groupname", None)
            # ExtraOpts for special flags like use non-unique
            # group-ids, force groupadd operation.
            # For experts use only
            extra_opts = group_obj.get("ExtraOpts", None)

        groupadd_args = ""
        groupmod_args = " -n {} ".format(group_name)

        # classify group to system/local
        if gid and int(gid) >= SYS_GID_MIN \
                and int(gid) <= SYS_GID_MAX:
            # If system, append --system flag
            # which applies to groupadd only
            groupadd_args += "--system "

        # Check if extra_opts is set, append it
        if extra_opts:
            groupadd_args += "{} ".format(extra_opts)
            groupmod_args += "{} ".format(extra_opts)

        if gid:
            groupadd_args += "-g {} ".format(gid)
        if gid and self.if_group_id_exists(gid):
            command = 'groupmod'
            prev_groupname = self.groupname_from_gid(gid)
            args = groupmod_args + prev_groupname
        else:
            command = 'groupadd'
            args = groupadd_args + group_name

        return command, args

    def create_groups(self):
        """Adds required groups to target filesystem."""
        if not self.groups:
            return
        for group in self.groups.keys():
            command, args = self.parse_group(group)
            self.executor.execute_for_arm64(command, args)

    def set_user_memberships(self):
        """Adds users to required groups in the target filesystem."""
        if not self.user_memberships:
            return
        for user in self.user_memberships.keys():
            grouplist = str(self.user_memberships.get(user)).strip('[]')\
                .replace('\'', '').replace(' ', '')
            # Remove trailing comma to use usermod
            self.executor.execute_for_arm64(
                    'usermod', '-a -G ' + grouplist + ' ' + user)


class CopyTargetExecutor:
    """
    Class for handling CopyTarget.
    """
    def __init__(self, copy_targets, default_workspace=NOT_EXISTS,
                 default_cp_src_type=None, filesystem_work_dir=NOT_EXISTS,
                 args=''):
        """
        CopyTargetExecutor Constructor.

        Parameters
        ----------
        copy_target_env_vars: str
                              Environemtn variables passed to CopyTarget Script
        copy_targets        : list
                              List of Copytargets to be executed, where
                              Copytargets are of type:
                              str: <path to copytarget yaml/script>
                              or
                              dict: For using defaults, Remove the field from
                                    the dict.
                              {
                                "Manifest": "<path to copytarget yaml/script>
                                            (str)",
                                "NvWorkspace": "<path from which files listed
                                                in copytarget are copied>
                                                (str)",
                                "SourceType": "<Copytarget source type>
                                               (str)",
                                "Args": {
                                    "Add": "<args to be added from copytarget
                                            cmdline>
                                           (str)",
                                    "Del": "<args to be deleted from copytarget
                                            cmdline>
                                            (str)"
                                    }
                              }
        default_workspace   : str
                              Default NvWorkspace to be used in copytarget
                              cmdline. (default is /not/exists/)
        default_cp_src_type : str
                              Default Copytarget source type to be used in
                              copytarget cmdline. (default is None)
        filesystem_work_dir : str
                              Destination directory for CopyTarget.
                              (default is /not/exists/)
        args                : str
                              Default Args to be passed to CopyTarget.
                              (default is "")

        Returns
        -------
        CopyTargetExecutor
            CopyTargetExecutor object is returned
        """
        self.copy_target_env_vars = ""
        self.copy_targets = copy_targets
        self.default_workspace = default_workspace
        self.default_cp_src_type = default_cp_src_type
        self.filesystem_work_dir = filesystem_work_dir
        self.set_args(args)
        self.buildfile_header_arg = ""
        self.buildfile_header_files = None
        self.qnx_build_file = None

    @property
    def args(self):
        """
        Property function for getting value of self.args in string format.

        Returns
        -------
        str
            Args printed in string format.
        """
        return self.print_args(self.__args)

    @args.setter
    def args(self, args):
        """
        Property function for setting value of self.args.

        Parameters
        ----------
        args    : str
                  Default set of args to be passed to copytarget cmdline.
        """
        self.set_args(args)

    @staticmethod
    def print_args(args):
        """
        Pretty print args in string format.

        Parameters
        ----------
        args    : dict
                  Argument list in the format
                  { "str option1": "value1",
                    "bool option2": True/False,
                    .
                    .
                  }

        Returns
        -------
        str
            Args printed in string format.

        """
        ret = ""
        for key, val in args.items():
            if isinstance(val, bool) and val is True:
                val = ""
            ret += " " + str(key) + " " + str(val)
        return ret

    def add_env_var(self, assignment):
        """
        Adds environment variables that needs to be passed to CopyTarget

        Parameters
        ----------
        assignment : str
                     A assignment in form of <KEY>=<VALUE>
        """
        self.copy_target_env_vars += assignment.strip() + " "

    def set_args(self, args):
        """
        Implementation to set self.args as a list of arguments.
        Maintained for backwards compatibility before property function was
        created.

        Parameters
        ----------
        args    : str
                  Default set of args to be passed to copytarget cmdline.
        """
        arg_list = shlex.split(args)
        self.__args = OrderedDict(
                            (k, True if v.startswith('-') else v)
                            for k, v in zip(arg_list, arg_list[1:]+["--"])
                            if k.startswith('-'))

    def add_args(self, args, seed_args=None):
        """
        Add arguments to copytarget cmdline and returns the updated args. If
        seed_args is not None, arguments are added/updated to seed_args, else
        args are added/updated to Default args.

        Parameters
        ----------
        args        : str
                      Args to be added to copytarget cmdline, with values, in a
                      space separated string:
                      " --str_option1 val1 --bool_option2 --str_option3 val3"
        seed_args   : dict
                      Argument list in the format
                      { "--str_option1": "value1",
                        "--bool_option2": True/False,
                        .
                        .
                      }
                      (default is None)

        Returns
        -------
        dict
            Dict of args with updated values.
        """
        arg_list = shlex.split(args)
        if seed_args:
            added_args = seed_args.copy()
        else:
            added_args = self.__args.copy()
        added_args.update({k: True if v.startswith('-') else v
                          for k, v in zip(arg_list, arg_list[1:]+["--"])
                          if k.startswith('-')})
        return added_args

    def del_args(self, options, seed_args=None):
        """
        Delete arguments from copytarget cmdline and returns the updated args.
        If seed_args is not None, arguments are deleted from seed_args, else
        args are deleted from Default args.

        Parameters
        ----------
        options     : str
                      Args to be deleted from copytarget cmdline, without
                      values, in a space separated string:
                      " --option1 -o --option2 "
        seed_args   : dict
                      Argument list in the format
                      { "--str_option1": "value1",
                        "--bool_option2": True/False,
                        .
                        .
                      }
                      (default is None)

        Returns
        -------
        dict
            Dict of args with updated values. (Removed options).
        """
        option_list = shlex.split(options)
        if seed_args:
            dele_args = seed_args.copy()
        else:
            dele_args = self.__args.copy()
        for option in option_list:
            if option in dele_args:
                del dele_args[option]
        return dele_args

    @property
    def copy_targets(self):
        """
        Property function for getting value of self.__copy_targets.
        """
        return self.__copy_targets

    @copy_targets.setter
    def copy_targets(self, copytargets):
        """
        Property function for setting value of self.args.
        This setter ensures, only list items can be assigned to copytarget.
        """
        if isinstance(copytargets, list):
            self.__copy_targets = copytargets
        else:
            raise_error_and_exit(
                    "CopyTarget: Attempt to assign non 'list' type to"
                    + " copytargets.")

    def validate_cp_object(self, script):
        """
        Validator function that ensures the copytarget entry conforms
        to Build-FS syntax expectations
        Parameters
        ----------
        script      : str, dict

        Returns
        -------
        boolean
            Returns whether script object conforms to expected syntax
        """
        if isinstance(script, str):
            return
        elif isinstance(script, dict):
            if "Manifest" not in script:
                raise_error_and_exit(
                        "CopyTargets: 'Manifest' is a required property" +
                        ", for dict entry inside 'CopyTargets'")
            string_fields = [
                'Manifest',
                'NvWorkspace',
                'SourceType'
            ]
            dict_fields = [
                'Args'
            ]

            for field in string_fields:
                if field in script and not isinstance(script[field], str):
                    raise_error_and_exit(
                        "CopyTargets: '" + field + "' value must be a string")

            for field in dict_fields:
                if field in script and not isinstance(script[field], dict):
                    raise_error_and_exit(
                        "CopyTargets: '" + field + "' value must be a dict")

            if 'Args' in script:
                args_string_fields = [
                    'Add'
                    'Del'
                ]
                for field in args_string_fields:
                    if field in script and not isinstance(script[field], str):
                        raise_error_and_exit(
                            "CopyTargets'Args: '" + field + "' value " +
                            "must be a string")

    def parse_cp_object(self, script):
        """
        Parse copytarget object and returns the required items for invoking
        copytarget for that manifest.

        Parameters
        ----------
        script      : str, dict
                      str: <path to copytarget yaml/script>
                      or
                      dict: For using defaults, Remove the field from the dict.
                      {
                        "Manifest": "<path to copytarget yaml/script> (str)",
                        "NvWorkspace": "<path from which files listed in
                                        copytarget are copied> (str)",
                        "SourceType": "<Copytarget source type> (str)",
                        "Args": {
                            "Add": "<args to be added from copytarget
                                    cmdline> (str)",
                            "Del": "<args to be deleted from copytarget
                                    cmdline> (str)"
                        }
                      }


        Returns
        -------
        tuple
            (manifest, cp_src_type, nv_workspace, args)
            Where,
            manifest    : Copytarget YAML file/shell script.
            cp_src_type : Copytarget source type.
            nv_workspace: NV_WORKSPACE value.
            args        : Copytarget arguments.
        """
        self.validate_cp_object(script)
        if isinstance(script, str):
            return (os.path.expandvars(script), self.default_cp_src_type,
                    os.path.expandvars(self.default_workspace), self.args)
        elif isinstance(script, dict):
            manifest = os.path.expandvars(script['Manifest'])
            cp_src_type = script.get('SourceType',
                                     self.default_cp_src_type)
            cp_src_type = os.path.expandvars(cp_src_type)
            nv_workspace = script.get('NvWorkspace',
                                      self.default_workspace)
            nv_workspace = os.path.expandvars(nv_workspace)
            args = script.get('Args', {})
            args_add = os.path.expandvars(args.get('Add', ""))
            args_del = os.path.expandvars(args.get('Del', ""))
            args = self.add_args(args_add)
            args = self.del_args(args_del, args)

            return (manifest, cp_src_type, nv_workspace,
                    self.print_args(args))
        else:
            raise_error_and_exit(
                    "CopyTarget: Item is not a str or dict: " + str(script))

    def execute_host_scripts(self):
        """Execute CopyTarget scripts."""
        # execute the copytarget executables
        if not self.copy_targets:
            if self.buildfile_header_arg != "":
                with open(self.qnx_build_file, "w") as f:
                    for hf in self.buildfile_header_files:
                        with open(os.path.expandvars(hf), 'r') as h:
                            f.write(h.read())
                    f.write('\n')
            return
        global COPYTARGET
        if Environment.get('COPYTARGET'):
            COPYTARGET = Environment.get('COPYTARGET')
        if Environment.get('PYTHON3'):
            PYTHON3 = Environment.get('PYTHON3')

        buildfile_header_arg = self.buildfile_header_arg

        for script in self.copy_targets:
            (script, cp_src_type, nv_workspace, args) = \
                    self.parse_cp_object(script)
            if YAML_EXT in script:
                cmd_string = ("{ENV}{CP} {FWD} {WS} {M} --source-type {CST} " +
                              "{BFH} {A}").format(
                                      ENV=self.copy_target_env_vars,
                                      CP=COPYTARGET,
                                      FWD=self.filesystem_work_dir,
                                      WS=nv_workspace,
                                      M=script,
                                      CST=cp_src_type,
                                      BFH=buildfile_header_arg,
                                      A=args)
                Executor.execute_on_host(PYTHON3, cmd_string)
                # Buildfile Header only supplied on first call
                buildfile_header_arg = ""
            else:
                logging.warning(
                        "WARNING: Please switch " + script + " into yaml.")
                Executor.execute_on_host('bash', os.path.expandvars(script)
                                         + ' ' + self.filesystem_work_dir)

    @staticmethod
    def get_full_cpt(copy_targets):
        full_cpt = []
        for script in copy_targets:
            if isinstance(script, str):
                full_cpt.append(OrderedDict([('Manifest', script)]))
            else:
                full_cpt.append(script)

        return full_cpt


class ScriptExecutor:
    """
    Wrapper class for executing existing scripts on host, existing script on
    target and copying scripts from host to target and executing them.
     """

    def __init__(self, scripts_list, executor=None):
        """
        ScriptExecutor Constructor.

        Parameters
        ----------
        scripts_list    : dict
                          Pre-Install/Post-Install scripts and their run
                          target. Of the format:
                          key --> "<path to script>",
                          value --> one from ["host", "target", "target_copy"]
        executor        : Executor
                          For executing scripts on target filesystem chroot.
                          (default is None)

        Returns
        -------
        ScriptExecutor
            ScriptExecutor object is returned.
        """
        self.scripts_list = scripts_list
        self.executor = executor

    def execute_scripts(self):
        """Executes the Scripts"""
        if not self.scripts_list:
            return

        Environment.set('WORK_DIR', self.executor.work_dir)
        Environment.set('FILESYSTEM_WORK_DIR',
                        self.executor.filesystem_work_dir)
        for script in self.scripts_list.keys():
            script_abs = os.path.expandvars(script)
            # Assumes that all run-once scripts are bashable
            if self.scripts_list.get(script) == 'target':
                self.executor.execute_for_arm64('bash', script_abs)
            elif self.scripts_list.get(script) == 'host':
                # Assuming it does not need to be copied workspace
                self.executor.execute_on_host('bash', script_abs)
            elif self.scripts_list.get(script) == 'target_copy':
                # Copy the script to target tmpdir and execute
                # and then delete.
                shutil.copy2(script_abs,
                             self.executor.filesystem_work_dir + TMPDIR)
                self.executor.execute_for_arm64(
                        'bash', TMPDIR + os.path.basename(script_abs))
                os.remove(self.executor.filesystem_work_dir + TMPDIR
                          + os.path.basename(script_abs))
            else:
                raise_error_and_exit(
                        "Unsupported execution location: '"
                        + self.scripts_list.get(script)
                        + "'. Expecting a value from the list:"
                        + " [host, target, target_copy]")


class DebianPackageManager:
    """
    Class for Managing Debian Packages in the target filesystem.
    """
    pkg_list_config_name = 'pkglist.debCONFIG'
    pkg_list_manifest_name = 'pkglist.debMANIFEST'
    pkg_list_config = TMPDIR + pkg_list_config_name
    pkg_list_manifest = TMPDIR + pkg_list_manifest_name

    def __init__(
            self, distro=None, config=None, executor=None,
            helper_dir=NOT_EXISTS):
        """
        DebianPackageManager Constructor.

        Parameters
        ----------
        distro      : str
                      Ubuntu distro name.
                      (default is None)
        config      : list
                      List of Debian Packages.
                      (default is None)
        executor    : Executor
                      For running commands in the target filesystem directory.
                      (default is None)
        helper_dir  : str
                      Path to helper scripts for installing packages in the
                      target filesystem directory.
                      (default is /not/exists/)

        Returns
        -------
        DebianPackageManager
            DebianPackageManager object is returned
        """
        self.ubuntu_distro = distro
        self.debian_config = []
        self.debian_manifest = None
        self.executor = executor
        self.filesystem_work_dir = executor.filesystem_work_dir
        self.helper_dir = helper_dir
        self.gen_manifest_name = "generate_manifest.sh"
        self.install_pkgs_name = "install_packages.sh"
        self.gen_manifest = "{helper_dir}/{manifest}".format(
                                    helper_dir=self.helper_dir,
                                    manifest=self.gen_manifest_name)
        self.install_pkgs = "{helper_dir}/{installpkgs}".format(
                                    helper_dir=self.helper_dir,
                                    installpkgs=self.install_pkgs_name)
        self.package_metadata = None
        self.leased_space = None
        self.debian_module = {}

        # Iterate through all entries in the debian config and extract the
        # package and module name (if provided)
        for debian in config:
            if isinstance(debian, str):
                self.debian_config.append(debian)
                self.debian_module[debian] = "unknown"
            elif isinstance(debian, dict):
                self.debian_config.append(debian["Package"])
                self.debian_module[debian["Package"]] = str(
                    debian["Module"]).lower()
            else:
                raise_error_and_exit("The 'DebianPackages' entry '{}' in "
                                     "BuildFS JSON config is incorrect"
                                     .format(debian))

    def generate_debian_manifest(self):
        """
        Generates manifest for the Debian packages, which are version locked
        Debian packages, using generate_manifest.sh helper script.
        """
        if not self.debian_config:
            logging.info(
                    "No debian packages requested in CONFIG. Skipping MANIFEST"
                    + " generation.")
            return

        # Write out pkg_list
        with open(self.filesystem_work_dir + self.pkg_list_config,
                  'w', encoding='utf-8') as pkgfd:
            pkgfd.writelines('\n'.join(self.debian_config))
            pkgfd.write('\n')

        # Execute generate-manifest script
        shutil.copy(self.gen_manifest, self.filesystem_work_dir + TMPDIR)
        self.executor.execute_for_arm64(
                TMPDIR + self.gen_manifest_name, self.pkg_list_config)

        # Read back debmanifest
        # https://www.webucator.com/how-to/how-read-file-with-python.cfm
        with open(self.filesystem_work_dir + self.pkg_list_manifest,
                  'r', encoding='utf-8') as mfd:
            self.debian_manifest = mfd.read().splitlines()

        os.remove(self.filesystem_work_dir + self.pkg_list_config)
        os.remove(self.filesystem_work_dir + TMPDIR + self.gen_manifest_name)

    def build_filesystem(self):
        """
        Installs Debian packages from the manifest generated, using
        install_packages.sh helper script.
        """
        if not self.debian_manifest:
            logging.info(
                    "No debian packages requested in MANIFEST. Skipping"
                    + " package install.")
            return

        # Write out pkglist from manifest
        with open(self.filesystem_work_dir + self.pkg_list_manifest,
                  'w', encoding='utf-8') as mfd:
            mfd.writelines('\n'.join(self.debian_manifest))
            mfd.write('\n')

        # Copy script and execute on arm64 space
        shutil.copy2(self.install_pkgs, self.filesystem_work_dir + TMPDIR)
        self.executor.execute_for_arm64(
                TMPDIR + os.path.basename(self.install_pkgs),
                self.pkg_list_manifest + " " + str(self.package_metadata))

        if self.package_metadata is not None:
            self.leased_space.processDebianPackageManifest(
                self.filesystem_work_dir + str(self.package_metadata),
                self.debian_module)
            os.remove(self.filesystem_work_dir + str(self.package_metadata))
        os.remove(self.filesystem_work_dir + self.pkg_list_manifest)
        os.remove(self.filesystem_work_dir + TMPDIR + self.install_pkgs_name)
        self.executor.execute_for_arm64('apt-get', 'clean')

    def get_full_debian_manifest(self, output_format="build-fs"):
        """
        Generates the list of DebianPackages installed in the filesystem
        alongside its version and description.

        Parameters
        ----------
        output_format       : enum["build-fs", "human"]
                              Choose output format between Human
                              understandable and for adding to Build-FS
                              DebianPackages section in MANIFEST.json
        Returns:
        --------
        str
            if output_format is "human"
                shell output of dpkg -l on the target filesystem.
        list
            else if output format is "build-fs"
                shell output of dpkg-query -Wf '${Package}=${Version}\n'
                on the target filesystem.
        """
        if output_format == "human":
            shell_stream = self.executor.execute_for_arm64(
                    'dpkg', '-l', stdout=PIPE)
            output = shell_stream["stdout"].decode("utf-8")
        elif output_format == "build-fs":
            shell_stream = self.executor.execute_for_arm64(
                    'dpkg-query', "-Wf '${Package}=${Version}\n'",
                    stdout=PIPE)
            output = shell_stream["stdout"].decode("utf-8")[:-1]
            output = output.split("\n")
        else:
            raise_error_and_exit(
                    "DebianPackageManager: Unknown output_format '"
                    + output_format + "' requested for Debian manifest.")

        return output


class RootfsOperations:
    """
    Base Class for executing file operations on target filesystem directory.
    """
    def __init__(
            self, base, output=None, cleanup_paths=None,
            work_dir=NOT_EXISTS,
            filesystem_work_dir=NOT_EXISTS):
        """
        RootfsOperations Constructor.

        Parameters
        ----------
        base                : str
                              Path to Base filesystem.
        output              : str
                              Output filesystem name, without file extenstion.
                              (default is None)
        cleanup_paths       : list
                              List of paths to be removed from filesystem,
                              before image creation.
                              (default is None)
        work_dir            : str
                              Path to Build-FS work directory.
                              (default is /not/exists/)
        filesystem_work_dir : str
                              Path to target filesystem directory.
                              (default is /not/exists/)

        Returns
        -------
        RootfsOperations
            RootfsOperations object is returned
        """
        # Base tarball and output paths
        self.filesystem_output = os.path.expandvars(output)
        # get absolute paths if they are relative
        if not os.path.isabs(self.filesystem_output):
            self.filesystem_output = os.path.abspath(
                    self.filesystem_output)
        self.cleanup_paths = cleanup_paths
        self.work_dir = work_dir
        self.filesystem_work_dir = filesystem_work_dir
        self.supported_base = "folder, tarball"
        if base:
            self.base = os.path.expandvars(base)
            if not os.path.isabs(self.base):
                self.base = os.path.abspath(self.base)
        else:
            self.base = None

    # Extracts rootfs tarball to shared path
    def extract_rootfs_tar(self):
        """Extracts Base Rootfs tarball to target filesystem directory."""
        # Compression is auto detected
        extract_path_args = '-C ' + self.filesystem_work_dir + ' -xf ' \
                            + self.base
        compress_args = ' -I ' + get_compression_tool(fil=self.base) + ' '
        # Extract rootfs
        Executor.execute_on_host(
                'tar', TAR_PRESERVE_ARGS + compress_args + extract_path_args)

    # Copies rootfs base folder to shared path
    def extract_rootfs_folder(self):
        """Copies Base rootfs folder to target filesystem directory."""
        Executor.execute_on_host(
                "cp", "-a " + self.base + "/. "
                + self.filesystem_work_dir)

    # Deletes rootfs in shared path
    def delete_rootfs(self):
        """Deletes the target filesystem directory."""
        # Execute rm -rf on filesystem dir
        shutil.rmtree(self.filesystem_work_dir, ignore_errors=True)

    # Compress_rootfs in shared path
    def compress_rootfs(self):
        """
        Compresses the target filesystem directory into a '.tar.bz2' tarball.
        """
        # commandline for tar
        compress_path_args = '-C ' + self.filesystem_work_dir + ' -cf ' \
                             + self.filesystem_output + TAR_EXT \
                             + TAR_COMPRESS + ' .'
        compress_args = ' -I ' + get_compression_tool(comp=TAR_COMPRESS) + ' '
        Executor.execute_on_host('tar', TAR_PRESERVE_ARGS
                                 + compress_args + compress_path_args)

    # Cleanup of rootfs from cleanup paths data
    def cleanup_rootfs(self):
        """
        Removes residual temporary files from filesystem based on listing
        in cleanup_paths.
        """
        # If cleanup_paths is None then there is no deletion
        if not self.cleanup_paths:
            return

        # Proceed to cleanup
        # Use host-side rm, apply -r when path ends with /
        for path in self.cleanup_paths:
            fpath = "{}/{}".format(self.filesystem_work_dir, path)
            if str(fpath).endswith("/"):
                rm_args = " -rf"
            else:
                rm_args = " -f"
            Executor.execute_on_host(
                    'rm {}'.format(rm_args), fpath, exit_on_failure=False)

    # Rootfs type check and call extraction functions
    def extract_rootfs(self):
        """
        Function checks the type of Base, and appropriately
        calls the correct extract function.
        """
        if not self.base:
            return
        if os.path.isdir(self.base):
            self.extract_rootfs_folder()
        elif re.compile(TAR_REGEX).match(self.base):
            self.extract_rootfs_tar()
        else:
            raise_error_and_exit(
                    "Unknown Base file format: '"
                    + self.base + "', Please provide"
                    + " Base in a Build-FS supported format.")


class LinuxRootfsOperations(RootfsOperations):
    """
    Derived class from RootfsOperations to handle Linux only activities.
    """
    def __init__(
            self, base, output, cleanup_paths,
            work_dir=NOT_EXISTS, filesystem_work_dir=NOT_EXISTS,
            filesystem_mount_dir=NOT_EXISTS, mirror_uris=None, executor=None,
            fs_include_paths=None):
        """
        LinuxRootfsOperations Constructor.

        Parameters
        ----------
        base                : str
                              Path to Base filesystem.
        output              : str
                              Output filesystem name, without file extenstion.
        cleanup_paths       : list
                              List of paths to be removed from filesystem,
                              after completion.
        fs_include_paths    : list
                              List of paths to be added mandatory to the
                              filesystem from the work_dir regardless of
                              cleanup.
                              (default is None)
        work_dir            : str
                              Path to Build-FS work directory.
                              (default is /not/exists/)
        filesystem_work_dir : str
                              Path to target filesystem directory.
        filesystem_mount_dir: str
                              Path to directory where base filesystem image
                              shall be mounted.
        mirror_uris         : list
                              List of Debian Mirror URIs
        executor            : Executor
                              For running commands in the target filesystem
                              directory.
                              (default is None)

        Returns
        -------
        LinuxRootfsOperations
            LinuxRootfsOperations object is returned
        """
        super().__init__(
                base, output, cleanup_paths, work_dir,
                filesystem_work_dir)
        self.fs_include_paths = fs_include_paths
        self.filesystem_mount_dir = filesystem_mount_dir
        self.mirror_uris = mirror_uris
        self.resolv_conf_md5 = ""
        self.apt_sources_md5 = ""
        self.executor = executor
        self.folder_mirror_index = 0
        self.mirror_mount_index = 0
        self.installer_debian_index = 0
        self.mirror_restore_list = []

    def update_fs_apt_sources_list(self):
        """
        Updates the target filesystem apt sources with given mirror_uris.
        If self.mirror_uris is None, apt sources are not updated.
        If updated, stores md5sum of the sources list, to track updation
        during Debian package installation/CopyTarget execution.
        """
        if self.mirror_uris is None:
            self.mirror_uris = []
        if not os.path.exists(self.filesystem_work_dir + SOURCES_LIST):
            return
        shutil.move(self.filesystem_work_dir + SOURCES_LIST,
                    self.filesystem_work_dir + SOURCES_LIST + BACKUP_TAG)
        apt_src_list = self.work_dir + '/' + os.path.basename(SOURCES_LIST)

        with open(apt_src_list, 'w', encoding='utf-8') as fd_apt_src:
            for ln in self.mirror_uris:
                if type(ln) is not OrderedDict:
                    fd_apt_src.write(ln + "\n")
                else:
                    if ln['Type'] == "debian_mirror":
                        sources_list = ln['Path'] + "\n"
                        fd_apt_src.write(sources_list)
                    elif ln['Type'] == "local_debian_folder":
                        sources_list = \
                            self.create_local_debian_folder(
                                "/var/mirror-", ln['Type'], ln['Path'])
                        fd_apt_src.write(sources_list)
                    elif ln['Type'] == "local_debian_mirror":
                        sources_list = \
                            self.create_local_debian_mirror(
                                "/mnt/", ln['Type'], ln['Path'])
                        fd_apt_src.write(sources_list)
                    elif ln['Type'] == "debian":
                        sources_list = \
                            self.run_installer_debian(
                                "/var/debian-",
                                ln['Type'], ln['Path'])
                        fd_apt_src.write(sources_list)
                    else:
                        raise_error_and_exit(
                            "Unknown Mirror Type: " + ln['Type'])

        shutil.copy2(apt_src_list, self.filesystem_work_dir + SOURCES_LIST)
        self.apt_sources_md5 = md5(self.filesystem_work_dir + SOURCES_LIST)

    def restore_fs_apt_sources_list(self):
        """
        Restore the target filesystem apt sources to the default before
        Build-FS operations, if apt sources have been updated by
        Build-FS CONFIG.
        If md5sum differs from the original saved version, it is implied
        file got updated from Debian package installation/CopyTarget
        execution, and file is not restored.
        """
        # Restore backed-up version
        tgt_sources_list = self.filesystem_work_dir + SOURCES_LIST
        if not os.path.exists(tgt_sources_list):
            return
        if md5(tgt_sources_list) == self.apt_sources_md5:
            shutil.move(tgt_sources_list + BACKUP_TAG,
                        tgt_sources_list)
        else:
            logging.info(
                    "LinuxRootfsOperations: Not restoring sources.list."
                    + " File has been modified since backup,")

        # clean up mirror related stuff.
        host_apt_admindir = ""
        for host_mirror_path in self.mirror_restore_list:
            # For readability just need mirror type here.
            mirror_type, mirror_item = host_mirror_path

            # Cleanup type folder : Delete host_mirror_path
            if mirror_type == "local_debian_folder":
                mirror_type, host_mirror_path = host_mirror_path
                shutil.rmtree(host_mirror_path)
            elif mirror_type == "local_debian_mirror":
                host_mirror_mount_path = mirror_item
                target_mirror_mount_path = \
                    host_mirror_mount_path.replace(
                        self.filesystem_work_dir, "")
                # to survive subsequent execute_for_arm64 calls
                for n in self.executor.target_mount_list:
                    if target_mirror_mount_path == n[1]:
                        self.executor.target_mount_list.remove(n)
                        break
                # Finally delete the mirror directory
                shutil.rmtree(host_mirror_mount_path)
            elif mirror_type == "debian":
                debian_package_name = mirror_item.split("|")[0]
                target_apt_admindir = mirror_item.split("|")[1]
                host_apt_admindir = mirror_item.split("|")[2]
                # remove super debian and so all the leaf debians
                dpkg_param = \
                    '--admindir ' + target_apt_admindir + \
                    ' --purge ' + debian_package_name
                self.executor.execute_for_arm64(
                    'dpkg', dpkg_param, stdout=PIPE)
            else:
                raise_error_and_exit(
                    " ERROR - Unknown Mirror Type during clean up: "
                    + mirror_type)

        # if ever host_apt_admindir was created we
        # need to remove it
        if host_apt_admindir != "":
            shutil.rmtree(host_apt_admindir)

        # Clean up restore list.
        self.mirror_restore_list.clear()

    @staticmethod
    def get_full_mirrors(mirrors):
        if not isinstance(mirrors, list):
            raise_error_and_exit(
                    "Expected arg_type 'list' for mirrors.")
        full_mirr = []
        for mir in mirrors:
            if isinstance(mir, str):
                full_mirr.append(OrderedDict([
                                    ("Path", mir),
                                    ("Type", "debian_mirror")]))
            else:
                full_mirr.append(mir)
        full_mirr = list(
                    OrderedDict(
                        (v['Path'], v) for v in full_mirr).values())
        return full_mirr

    def update_resolv_conf(self):
        """
        Updates resolv.conf of target filesystem, to provide internet
        access during chroot.
        Stores md5sum of the resolv conf, to track updation
        during Debian package installation/CopyTarget execution.
        """
        # Re-use host's resolv.conf for resolution
        if not os.path.exists(self.filesystem_work_dir + RESOLV_CONF):
            return
        shutil.move(self.filesystem_work_dir + RESOLV_CONF,
                    self.filesystem_work_dir + RESOLV_CONF + BACKUP_TAG)
        shutil.copy2(RESOLV_CONF, self.filesystem_work_dir + RESOLV_CONF)
        self.resolv_conf_md5 = md5(self.filesystem_work_dir + RESOLV_CONF)

    def restore_resolv_conf(self):
        """
        Restores resolv.conf of target filesystem, to default before
        Build-FS operations.
        If md5sum differs from the original saved version, it is implied
        file got updated from Debian package installation/CopyTarget
        execution, and file is not restored.
        """
        # For targetfs shipped resolv.conf is automatically populated
        # by systemd-resolved
        tgt_resolv_cnf = self.filesystem_work_dir + RESOLV_CONF
        if not os.path.exists(tgt_resolv_cnf):
            return
        if md5(tgt_resolv_cnf) == self.resolv_conf_md5:
            shutil.move(self.filesystem_work_dir + RESOLV_CONF + BACKUP_TAG,
                        self.filesystem_work_dir + RESOLV_CONF)
        else:
            logging.info(
                    "LinuxRootfsOperations: Not restoring resolv.conf. File "
                    + "has been modified since backup.")

    def copy_to_image(self):
        """
        Copies target filesystem contents to the mounted target filesystem
        image.
        """
        # Create a virtual image file of type partition_type
        # Parameters for the virtual disk are decided on final partition size
        Executor.execute_on_host("mount", self.filesystem_output + IMG_EXT
                                 + " " + self.filesystem_mount_dir)
        # No alternative to cp -a
        Executor.execute_on_host(
                "cp", CP_PRESERVE_ARGS + self.filesystem_work_dir
                + "/. " + self.filesystem_mount_dir)
        Executor.execute_on_host("umount", self.filesystem_mount_dir)

    def copy_from_image(self):
        """
        Copies target filesystem contents from mounted Base image to
        target filesystem directory.
        """
        if re.compile(TAR_REGEX).match(self.base):
            extract_path_args = '-C ' + self.work_dir + ' -xf ' + self.base
            compress_args = ' -I ' + get_compression_tool(fil=self.base) + ' '
            Executor.execute_on_host('tar', compress_args + extract_path_args)
            base_name = os.path.basename(self.base)
            tar_index = base_name.find(TAR_EXT)
            base_img_name = base_name[0:tar_index]
            base_img = self.work_dir + base_img_name
        else:
            base_img = self.base

        Executor.execute_on_host(
                "mount", base_img + " " + self.filesystem_mount_dir)
        # No alternative to cp -a
        Executor.execute_on_host(
                "cp", CP_PRESERVE_ARGS + self.filesystem_mount_dir + "/. "
                + self.filesystem_work_dir)
        Executor.execute_on_host(
                "umount", self.filesystem_mount_dir)

    def extract_rootfs(self):
        """
        Function checks the type of Base, and appropriately
        calls the correct extract function.
        """
        if not self.base:
            return
        if re.compile(IMG_REGEX).match(self.base):
            self.copy_from_image()
        else:
            super().extract_rootfs()

    def edit_hosts(self, hostname):
        """
        Function that edits the hostname of the target filesystem image.

        Parameters
        ----------
        hostname    : str
                      Required hostname of the target filesystem.
        """
        # For targetfs to have a particular hostname
        hostname_file = self.filesystem_work_dir + HOSTNAME_FILE
        hosts_file = self.filesystem_work_dir + HOSTS_FILE
        with open(hostname_file, 'r', encoding='utf-8') as hf:
            old_hostname = hf.readline()
        with open(hostname_file, 'w', encoding='utf-8') as hf:
            hf.write(hostname + '\n')

        with open(hosts_file, 'r', encoding='utf-8') as hf:
            entries = hf.readlines()

        with open(hosts_file, 'w', encoding='utf-8') as hf:
            hf.write(LOCALHOST_IP + '\tlocalhost\n')
            hf.write(LOCALHOST_IP + '\t' + hostname + '\n')
            for line in entries:
                is_oldentry = re.compile(
                        LOCALHOST_IP + r"\s*" + old_hostname).match(line)
                is_localentry = re.compile(
                        LOCALHOST_IP + r"\s*localhost").match(line)
                if not is_oldentry and not is_localentry:
                    hf.write(line)

    def create_local_debian_folder(self,
                                   target_mirror_base_name,
                                   mirror_type,
                                   mirror_path):
        # choose first untaken index (undefined directory)
        while os.path.exists(self.filesystem_work_dir +
                             target_mirror_base_name +
                             str(self.folder_mirror_index) + '/'):
            self.folder_mirror_index += 1

        target_mirror_path = target_mirror_base_name + \
            str(self.folder_mirror_index) + '/'
        host_mirror_path = self.filesystem_work_dir + target_mirror_path

        # Create mirror directory in targetfs.
        os.mkdir(host_mirror_path, mode=0o755)

        # Feed restore list for cleanup.
        self.mirror_restore_list.append((mirror_type, host_mirror_path))

        debian_source_dir = os.path.expandvars(mirror_path)
        if not os.path.exists(debian_source_dir):
            raise_error_and_exit(
                    "Invalid/Missing Debian source dir: " + debian_source_dir)

        # Copy all debians into targetfs mirror directory.
        debian_is_present = False
        files = os.listdir(debian_source_dir)
        for f in files:
            # handle Debians
            if f.endswith(".deb"):
                abs_debian = os.path.join(debian_source_dir, f)
                try:
                    shutil.copy2(abs_debian, host_mirror_path)
                except EnvironmentError:
                    raise_error_and_exit(
                            "Could not copy " +
                            "Debian from source path: " + abs_debian + " " +
                            "to the targetfs mirror path: " + host_mirror_path)
                debian_is_present = True
            else:
                logging.warning("Ignoring non-debian file in source path ")
        if not debian_is_present:
            raise_error_and_exit("Source path: " + debian_source_dir + " " +
                                 "contains no Debian files.")

        # Run scanpackages for this directory.
        sources_list = self.dpkg_scanpackages(self.filesystem_work_dir,
                                              target_mirror_path)
        return sources_list

    def dpkg_scanpackages(self,
                          filesystem_work_dir,
                          target_mirror_path):
        host_mirror_path = filesystem_work_dir + target_mirror_path
        packages_file_name = os.path.join(host_mirror_path, "Packages")
        dpkg_dev_path = Environment.get('DPKG_DEV_PATH')
        if dpkg_dev_path:
            dpkg_scanpackages = os.path.join(
                    dpkg_dev_path, 'dpkg-scanpackages')
        else:
            dpkg_scanpackages = 'dpkg-scanpackages'

        shell_stream = self.executor.execute_on_host(dpkg_scanpackages,
                                                     "-m " +
                                                     host_mirror_path +
                                                     " /dev/null",
                                                     stdout=PIPE)
        output = shell_stream["stdout"].decode("utf-8")
        # dpkg-scanpackages lists the file names in Packages relatively
        # to the dpkg-scanpackages execution directory.
        # Patching the filenames to the base-names.
        with open(os.path.join(host_mirror_path, "Packages"),
                  'w', encoding='utf-8') as f:
            # remove the Packages' file paths and add a leading "./"
            output = output.replace(filesystem_work_dir, "")
            output = output.replace(target_mirror_path, "./")
            f.write(output)

        # Zip Packages t oPackages.gz.
        with open(packages_file_name, 'rb') as f_in:
            with gzip.open(packages_file_name + ".gz", 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        if not os.path.exists(packages_file_name + ".gz"):
            raise_error_and_exit(packages_file_name +
                                 ".gz could not be created ...")

        # Remove the uncompressed Packages.
        os.remove(packages_file_name)

        sources_list = "\ndeb [trusted=yes] file://" + \
                       target_mirror_path + " ./\n"
        return sources_list

    def create_local_debian_mirror(self,
                                   target_mount_base_dir,
                                   mirror_type,
                                   mirror_path):
        host_mirror_mount_path = self.filesystem_work_dir +\
            target_mount_base_dir +\
            str(self.mirror_mount_index) + "/"
        while os.path.isdir(self.filesystem_work_dir + target_mount_base_dir +
                            str(self.mirror_mount_index) + "/"):
            self.mirror_mount_index += 1

        target_mirror_mount_path = target_mount_base_dir +\
            str(self.mirror_mount_index) + "/"
        host_mirror_mount_path = self.filesystem_work_dir +\
            target_mirror_mount_path

        # safe restore data
        self.mirror_restore_list.append((mirror_type, host_mirror_mount_path))

        os.mkdir(host_mirror_mount_path, mode=0o755)
        local_mirror_dir = os.path.expandvars(mirror_path)

        # add mount directory to executor
        self.executor.target_mount_list.append((local_mirror_dir,
                                                target_mirror_mount_path))
        sources_list = "\ndeb [trusted=yes] file://" +\
                       target_mirror_mount_path + " ./\n"
        return sources_list

    def run_installer_debian(self,
                             target_installer_debian_base_path,
                             mirror_type,
                             mirror_path):
        # Install installer debian on different admindir to
        # be able to run postinst script that installs
        # the leaf debians from within the installer debian.
        # As this is NOT required at the moment this might be
        # interesting in the future.
        # Steps:
        # create alternative /var/lib/dpkg directory
        #     sudo mkdir -p /var/lib/apt_admindir_installer_debian/
        # create an empty status dir
        #     touch /var/lib/apt_admindir_installer_debian/status
        # create emptry info and updates directories
        #     sudo mkdir -p /var/lib/apt_admindir_installer_debian/info/
        #     sudo mkdir -p /var/lib/apt_admindir_installer_debian/updates/
        # run dpkg
        #     --admindir= /var/lib/apt_admindir_installer_debian/
        #     --install installer_debian
        sources_list = ""
        host_source_intaller_debian = os.path.expandvars(mirror_path)

        while os.path.isdir(self.filesystem_work_dir +
                            target_installer_debian_base_path +
                            str(self.installer_debian_index) + '/'):
            self.installer_debian_index += 1

        target_installer_debian_path = \
            target_installer_debian_base_path +\
            str(self.installer_debian_index) + '/'

        host_installer_debian_path = self.filesystem_work_dir +\
            target_installer_debian_path

        if not os.path.isdir(host_installer_debian_path):
            os.mkdir(host_installer_debian_path, mode=0o755)

        shutil.copy2(host_source_intaller_debian, host_installer_debian_path)

        # make sure a seperate admin dir is created
        # this will allow us to install debians from within debians
        # although they cannot depend on each other.
        target_apt_admindir = "/var/lib/apt_admindir_installer_debian/"
        apt_admindir_param = '--admindir ' + target_apt_admindir

        self.create_apt_admin_dir(target_apt_admindir)

        host_apt_admindir = self.filesystem_work_dir + target_apt_admindir

        # What is our packages name ?
        # in case the debian has a different package name
        # than the debian file name we need to override it.
        # Defaulting to package name derived from debian
        # package file (until th first "_").
        debian_file_name = os.path.basename(host_source_intaller_debian)
        debian_package_name = debian_file_name.split("_")[0]

        dpkg_param = apt_admindir_param + ' --info ' +\
            target_installer_debian_path + debian_file_name
        shell_stream = self.executor.execute_for_arm64('dpkg',
                                                       dpkg_param,
                                                       stdout=PIPE)
        output = shell_stream["stdout"].decode("utf-8")
        for line in output.split("\n"):
            # find Package in control file (info).
            search_pattern = "Package: "
            if search_pattern in line:
                debian_package_name = \
                    line.replace(search_pattern, "").replace(" ", "")
                break

        # install the debian
        dpkg_param = apt_admindir_param + ' --install ' +\
            target_installer_debian_path + debian_file_name
        self.executor.execute_for_arm64('dpkg', dpkg_param, stdout=PIPE)

        # check via dpkg --contents about any sources.list
        # and append them to sources.list
        dpkg_param = apt_admindir_param + ' --contents ' +\
            target_installer_debian_path + debian_file_name
        shell_stream = \
            self.executor.execute_for_arm64('dpkg', dpkg_param, stdout=PIPE)
        output = shell_stream["stdout"].decode("utf-8")
        search_pattern = "/etc/apt/sources.list.d/"
        for line in output.split("\n"):
            if search_pattern in line:
                # check for file type ("-" is file) in first character of line.
                if line[0] == '-':
                    pos = line.find(search_pattern)
                    if pos >= 0:
                        host_installer_deban_sources_list = \
                            self.filesystem_work_dir + line[pos:]
                        host_installer_deban_sources_list_delete = \
                            host_installer_deban_sources_list + ".delete"
                        os.rename(host_installer_deban_sources_list,
                                  host_installer_deban_sources_list_delete)
                        with open(
                            host_installer_deban_sources_list_delete,
                                'r', encoding='utf-8') as fd_inst_deb_src_del:
                            with open(
                                host_installer_deban_sources_list,
                                    'w', encoding='utf-8') as fd_inst_deb_src:
                                rlines = fd_inst_deb_src_del.readlines()
                                for line in rlines:
                                    line = " ".join(line.split())
                                    line = \
                                        line.replace("deb file",
                                                     "deb [trusted=yes] file")
                                    fd_inst_deb_src.write(line)
                                    sources_list += line + "\n"
                        os.remove(host_installer_deban_sources_list_delete)
                        break

        # add paths to the cleanup list
        debian_remove_info = debian_package_name + "|" +\
            target_apt_admindir + "|" + host_apt_admindir

        self.mirror_restore_list.append((mirror_type, debian_remove_info))

        shutil.rmtree(host_installer_debian_path)
        return sources_list

    def create_apt_admin_dir(self, target_apt_admindir):
        host_apt_admindir = self.filesystem_work_dir + target_apt_admindir

        # for the first super debian we create the
        # admindir
        if not os.path.isdir(host_apt_admindir):
            os.mkdir(host_apt_admindir, mode=0o755)
            if not os.path.isdir(host_apt_admindir + "info/"):
                os.mkdir(host_apt_admindir + "info/", mode=0o755)
            if not os.path.isdir(host_apt_admindir + "updates/"):
                os.mkdir(host_apt_admindir + "updates/", mode=0o755)
            open(host_apt_admindir + "status", 'a').close()

    def apply_selinux_attributes(self, selinux_data):
        """
        Parses the selinux block data to obtain required inputs: setfiles tool,
        policy file, file_contexts file and execute command to apply selinux
        attributes to the filesystem in workdir.

        Parameters
        ----------
        selinux_data        : dict

        Returns
        -------
        int or None
            Returns errno.EINVAL if any selinux input is empty. Else
            returns nothing.
        """
        # Check for possible case that the optinal SELINUX block is absent.
        if not selinux_data:
            logging.info(
                    "SELinux section absent in the CONFIG."
                    + " Not applying SELinux attributes.")
            return

        # Tool, policy file and context file needed to set attributes.
        setfiles_tool = os.path.expandvars(selinux_data["SetFiles"])
        selinux_context_file = os.path.expandvars(selinux_data["ContextFile"])
        selinux_policy_file = os.path.expandvars(selinux_data["PolicyFile"])

        # Check if any attributes is None or empty
        if not setfiles_tool:
            if not selinux_policy_file:
                if not selinux_context_file:
                    logging.error(
                        "\nIncorrect SELinux block entry." +
                        "\nPlease refer to the build-fs " +
                        "documentation for the SELinux block syntax.")
                    return errno.EINVAL

        # Construct setfiles cmdline
        # FIXME: -E not supported in ubuntu provided setfiles
        # FIXME: Review with Mustafa and get sign-off of tool running
        setfiles_args = "-c {} -E -m -W -d -v -r {} {} {}"\
            .format(selinux_policy_file, self.filesystem_work_dir,
                    selinux_context_file, self.filesystem_work_dir)
        # Setfiles execution automatically checks for errors,
        # build-fs returns it on failure.
        self.executor.execute_on_host(
                setfiles_tool, setfiles_args, exit_on_failure=True)

    # Process FilesystemInclude list and apply semantics
    def process_fsinclude_rootfs(self):
        """
        If FilesystemInclude is defined, only the files from FS_WORK_DIR
        is copied to workspace passed to cleanup and then image or tarball
        creation.
        If FilesystemInclude is not defined then no-op.
        """
        # Check if FilesystemInclude does not exist, if yes then no-op.
        if not self.fs_include_paths:
            return

        # Move work_dir to .pre_fs_include dir if filesystem inclusions is not
        # required in the build only if fs_includes is reqd.
        PRE_FS_INCLUDE = os.path.realpath(
                self.filesystem_work_dir) + ".pre_fs_include"
        Executor.execute_on_host(
                "mv", "{} {}".format(
                    self.filesystem_work_dir,
                    PRE_FS_INCLUDE),
                exit_on_failure=True)

        # Add files from fs_include_paths if fs_include_paths is set
        CP_PRESERVE_ARGS = "-alf"
        for path in self.fs_include_paths:
            dest_dir = os.path.dirname(
                    "{}/{}".format(
                        self.filesystem_work_dir,
                        path.rstrip('/')))
            Executor.execute_on_host(
                    "mkdir -p", "{}".format(dest_dir),
                    exit_on_failure=True)
            Executor.execute_on_host(
                    "cp {}".format(CP_PRESERVE_ARGS), "{}/{} {}".format(
                        PRE_FS_INCLUDE, path, dest_dir), exit_on_failure=True)


class FSLeasedSpace:

    def __init__(self, size_limits_file, build_fs):
        self.target_size_file = (
                build_fs.options.output_folder + "/fs_size_" +
                build_fs.output_name + ".yaml")
        self.size_limits_file = size_limits_file

        self.build_fs = build_fs
        # This will be true if a the current or parent config BuildFS config
        # uses a FS tar or image as its base.
        self.uses_base = False
        self.base_file = None
        if build_fs.fs_base is not None:
            self.uses_base = True
            self.base_file = build_fs.fs_base
            self.target_size_file = build_fs.options.output_folder \
                + "/fs_layer_size_" + build_fs.output_name + ".yaml"

        if build_fs.options.generate_target_size_file != "yes":
            return

        global COPYTARGET
        if Environment.get('COPYTARGET'):
            COPYTARGET = Environment.get('COPYTARGET')
        spec = importlib.util.spec_from_file_location("copytarget",
                                                      COPYTARGET)
        self.copytarget = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.copytarget)
        self.file_size_records = self.copytarget.FileSizeRecords(
            self.target_size_file)
        self.file_size_dict = self.file_size_records.fileSizeDict
        self.file_records_dict = self.file_size_records.fileRecordsDict

        self.acceptable_units = ["bytes", "byte", "B"]
        self.include_debian_files = False

    def change_target_size_file(self, targetSizeFile):
        if self.build_fs.options.generate_target_size_file != "yes":
            return
        self.target_size_file = targetSizeFile
        self.file_size_records = self.copytarget.FileSizeRecords(
            self.target_size_file)
        self.file_size_dict = self.file_size_records.fileSizeDict
        self.file_records_dict = self.file_size_records.fileRecordsDict

    def reset_target_size_file(self):
        if self.build_fs.options.generate_target_size_file != "yes":
            return
        # Empty the contents if this file and write essential keys initialized
        # to zero
        with open(self.target_size_file, 'w', encoding='utf-8') as f:
            pass
        self.copytarget.FileSizeRecords(
            self.target_size_file).writeTargetSizeManifest()
        self.change_target_size_file(self.target_size_file)

    def raise_issue(self, issue):
        if Environment.get('NV_ERROR_ON_FS_SIZE_LIMITS') == 'false':
            logging.warning(issue)
        else:
            raise_error_and_exit(issue)

    def check_target_size_manifest(self, filesystem):
        if self.size_limits_file is None:
            return

        size_limits_dict = {}
        file_size_dict = {}
        with open(self.size_limits_file, "r",
                  encoding='utf-8') as sizeLimitsFileHandle:
            size_limits_dict = yaml.load(
                    sizeLimitsFileHandle,
                    Loader=yaml.BaseLoader)
        file_size_records = self.copytarget.FileSizeRecords(
            self.target_size_file)
        file_size_records.readTargetSizeManifest()
        file_size_dict = file_size_records.fileSizeDict

        # Check if total max size is less than the permitted limit
        if self.build_fs.filesystem_work_dir != "/":
            file_size_dict["totalFSSize"] = Executor.execute_on_host(
                'du', '-s ' + self.build_fs.filesystem_work_dir,
                stdout=PIPE, silent=True)["stdout"].split()[0].decode('utf-8')
            file_size_dict["totalFSSize"] = int(file_size_dict["totalFSSize"])
        logging.info("The contents added to '{}' filesystem takes up "
                     "'{}' bytes."
                     .format(filesystem, file_size_dict["totalFSSize"]))
        if "maxFSSize" not in size_limits_dict:
            self.raise_issue("The max size of filesystems has not been "
                             "defined in {}. Please define this using the "
                             "'maxFSSize' key and try again."
                             .format(self.size_limits_file))
            return
        if isinstance(size_limits_dict["maxFSSize"], dict):
            if (filesystem not in size_limits_dict["maxFSSize"] and
                    "others" not in size_limits_dict["maxFSSize"]):
                self.raise_issue("The max size of '{}' filesystem is not "
                                 "defined in {}."
                                 .format(filesystem, self.size_limits_file))
                return
            if filesystem in size_limits_dict["maxFSSize"]:
                max_FS_size = size_limits_dict["maxFSSize"][filesystem]
            else:
                max_FS_size = size_limits_dict["maxFSSize"]["others"]
        else:
            max_FS_size = size_limits_dict["maxFSSize"]
        max_FS_size = self.getIntValue(max_FS_size)
        file_size_dict["totalFSSizeLimit"] = max_FS_size
        if file_size_dict["totalFSSize"] > max_FS_size:
            self.raise_issue("The size of '{}' filesystem ('{}' bytes) is "
                             "more than the total permissible limit of "
                             "'{}' bytes."
                             .format(filesystem, file_size_dict["totalFSSize"],
                                     max_FS_size))
        else:
            logging.info("This is under the maximum "
                         "permissible limit of '{}' bytes."
                         .format(max_FS_size))

        # Check if the size consumed by each module is less than the
        # permitted limit
        if "modules" not in size_limits_dict or \
                size_limits_dict["modules"] is None:
            self.raise_issue("The size of filesystems for each module has "
                             "not been defined in {}. "
                             "Please define this using the 'modules' key and "
                             "try again."
                             .format(self.size_limits_file))
            return
        for module in file_size_dict["modules"]:
            module_size = file_size_dict["modules"][module]["moduleSize"]
            logging.info("'{}' module consumes '{}' bytes in the '{}' "
                         "filesystem."
                         .format(module, module_size, filesystem))
            if module not in size_limits_dict["modules"]:
                self.raise_issue("The max size of '{}' module is not defined "
                                 "in {}."
                                 .format(module, self.size_limits_file))
                return
            if isinstance(size_limits_dict["modules"][module], dict):
                if (filesystem not in size_limits_dict["modules"][module] and
                        "others" not in size_limits_dict["modules"][module]):
                    self.raise_issue("The max size of '{}' module for '{}' "
                                     "filesystem is not defined in {}."
                                     .format(module, filesystem,
                                             self.size_limits_file))
                    return
                if filesystem in size_limits_dict["modules"][module]:
                    max_module_size = size_limits_dict[
                        "modules"][module][filesystem]
                else:
                    max_module_size = size_limits_dict[
                        "modules"][module]["others"]
            else:
                max_module_size = size_limits_dict["modules"][module]
            if self.uses_base:
                # Ensure that only the size of a module in current layer
                # (without the size of base image is specified in the manifest)
                if not str(max_module_size).startswith("+"):
                    self.raise_issue("'{}' filesystem uses '{}' as the base. "
                                     "In such cases, the {} size limits file "
                                     "should only specify the sizes of '{}' "
                                     "module in current layer. "
                                     "Such fields should start with a '+' "
                                     "(e.g +512 bytes)"
                                     .format(filesystem, self.base_file,
                                             self.size_limits_file,
                                             module))
                    return
            max_module_size = self.getIntValue(max_module_size)
            file_size_dict["modules"][
                    module]["moduleSizeLimit"] = max_module_size
            if module_size > max_module_size:
                self.raise_issue("The size of '{}' module ('{}' bytes) is "
                                 "more than the total permissible limit of "
                                 "'{}' bytes for '{}' filesystem"
                                 .format(module, module_size,
                                         max_module_size, filesystem))
            else:
                logging.info("This is under the maximum "
                             "permissible limit of '{}' bytes."
                             .format(max_module_size))
        file_size_records.writeTargetSizeManifest()

    def processDebianPackageManifest(self, manifest, debian_module):
        logging.info("Processing debian package size information")
        packageManifestDict = OrderedDict()
        with open(manifest, "r", encoding='utf-8') as manifestHandle:
            packageManifestDict = self.file_size_records.orderedYAMLLoad(
                manifestHandle, Loader=yaml.SafeLoader)
        # Determine the owners for all debians in the manifest
        for debian in debian_module.copy():
            self.updateDependsModule(debian, debian_module,
                                     packageManifestDict)
        for package in packageManifestDict["installed_packages"]:
            if self.uses_base:
                # Ignore packages that were already installed in the base FS
                if package in packageManifestDict["base_packages"]:
                    continue
            if package not in debian_module:
                debian_module[package] = "unknown"
            self.addDebianPackage(package, debian_module[package],
                                  packageManifestDict[
                                      "installed_packages"][package]["size"],
                                  packageManifestDict[
                                      "installed_packages"][package]["files"])
        logging.info("Writing debian package size information to {}"
                     .format(self.target_size_file))
        self.file_size_records.writeTargetSizeManifest()

    def updateDependsModule(self, debian, debian_module, packageManifestDict):
        if debian not in packageManifestDict["installed_packages"]:
            # This can happen in case of optional dependencies
            debian_module[debian] = "unknown"
            return
        depends = packageManifestDict["installed_packages"][debian]["depends"]
        if depends is None:
            return
        depends = re.sub(r"\s+", "", depends)  # remove all white space
        depends = re.sub(r"\|", ",", depends)  # Replace | with a ,
        # Remove everything in brackets
        depends = re.sub(r"\([^()]*\)", "", depends)
        depends = depends.split(",")
        for dependent in depends:
            if dependent in debian_module:
                continue
            else:
                debian_module[dependent] = debian_module[debian]
                self.updateDependsModule(dependent, debian_module,
                                         packageManifestDict)

    def addDebianPackage(self, debian, module, size, fileList):
        if module not in self.file_size_dict["modules"]:
            self.file_size_dict["modules"][module] = OrderedDict()
            self.file_size_dict["modules"][module]["moduleSize"] = 0
            self.file_size_dict[
                    "modules"][module]["moduleSizeLimit"] = "unknown"
            self.file_size_dict["modules"][module]["numberOfFiles"] = 0
            self.file_size_dict["modules"][module]["numberOfDebians"] = 0
            self.file_size_dict["modules"][module]["debians"] = OrderedDict()
            self.file_size_dict["modules"][module]["files"] = OrderedDict()

        debianFileSize = 0
        if (size is None) or self.include_debian_files:
            for file in fileList:
                source = self.copytarget.CopyTarget.normpath(
                    self.build_fs.filesystem_work_dir + str(file))
                if not os.path.isfile(source):
                    continue
                fileSize = os.lstat(source).st_size
                debianFileSize += fileSize
                if self.include_debian_files:
                    if source not in self.file_records_dict:
                        self.file_size_dict["totalNumberOfFiles"] += 1
                        self.file_size_dict[
                            "modules"][module]["numberOfFiles"] += 1
                    self.file_size_dict[
                        "modules"][module]["files"][source] = fileSize
                    self.file_records_dict[source] = {"module": module,
                                                      "size": fileSize}

        if size is not None:
            size = int(size) * 1024
        else:
            # This handles the case where dpkg-query does not report
            # Installed-Size
            size = debianFileSize

        self.file_size_dict["totalFSSize"] += size
        if "totalNumberOfDebians" not in self.file_size_dict:
            self.file_size_dict["totalNumberOfDebians"] = 0
        self.file_size_dict["totalNumberOfDebians"] += 1
        self.file_size_dict["modules"][module]["moduleSize"] += size
        if "numberOfDebians" not in self.file_size_dict["modules"][module]:
            self.file_size_dict["modules"][module]["numberOfDebians"] = 0
        self.file_size_dict["modules"][module]["numberOfDebians"] += 1
        self.file_size_dict["modules"][module]["debians"][debian] = size

    def getIntValue(self, value):
        unitSpecified = False
        for unit in self.acceptable_units:
            if str(value).endswith(unit):
                unitSpecified = True
                break
        if not unitSpecified:
            raise_error_and_exit("The value '{}' specified in {} does not "
                                 "have a correct unit specified. "
                                 "It should be one of {}"
                                 .format(value, self.size_limits_file,
                                         self.acceptable_units))
        return int(re.findall('[0-9]+', str(value))[0])


class FileParser:
    """
    Class to parse the CONFIG or MANFIEST file input stores its JSONData.
    Provides get functions to return instances of each Fields.
    """
    required_fields = ['Output', 'OS']
    string_fields = [
            'Output', 'OS', 'FilesystemType', 'Base',
            'ImageSize', 'FileHashListDTS'
            ]
    array_fields = ['CopyTargets', 'FilesystemCleanup']
    dict_fields = ['PreInstalls', 'PostInstalls']

    def __init__(self, json_file=None, json_str=None):
        """
        FileParser Constructor.

        Parameters
        ----------
        json_file   : str
                      Path to input Build-FS CONFIG/MANIFEST file.
        json_str   : str
                     Json file represented as a string.

        Returns
        -------
        FileParser
            FileParser object is returned
        """
        self.json_file = json_file
        # Give first priority to json_str over json_file
        # and then fallback to json_file.
        try:
            if json_str:
                self.json_data = json.loads(
                                    json_str, object_pairs_hook=OrderedDict)
            elif json_file:
                with open(json_file, 'r', encoding='utf-8') as fd_json:
                    self.json_data = json.load(
                                        fd_json, object_pairs_hook=OrderedDict)
            else:
                raise_error_and_exit(
                        self.__class__.__name__ + ": Require input json_file "
                        + " or json_str")
        except ValueError as error:
            logging.error(error)
            raise_error_and_exit(
                    "Invalid JSON syntax in file: '"
                    + os.path.abspath(json_file) + "'")
        self.validate()
        self.init_default()

    def get_output(self):
        return self.json_data["Output"]

    def get_pre_installs(self):
        return self.json_data["PreInstalls"]

    def get_copytargets(self):
        return self.json_data["CopyTargets"]

    def get_mount_point_config(self):
        if self.json_data["FSMountPointConfg"] is None:
            return None
        else:
            if "MountPoint" not in self.json_data["FSMountPointConfg"]:
                raise_error_and_exit("'MountPoint' attribute is not "
                                     "initialized in 'FSMountPointConfg'")
            if "DestinationIncludesMountPoint" not in self.json_data[
                    "FSMountPointConfg"]:
                self.json_data["FSMountPointConfg"][
                    "DestinationIncludesMountPoint"] = False
            for attribute in self.json_data["FSMountPointConfg"]:
                if attribute not in ["MountPoint",
                                     "DestinationIncludesMountPoint"]:
                    raise_error_and_exit("'{}' is not a valid attribute "
                                         "for 'FSMountPointConfg'"
                                         .format(attribute))
            return self.json_data["FSMountPointConfg"]

    def get_digest_metadata(self):
        if self.json_data["DigestMetadataConfig"] is None:
            return None
        else:
            if "enabled" not in self.json_data["DigestMetadataConfig"]:
                raise_error_and_exit("'enabled' attribute is not initialized "
                                     "in the config for generating digest "
                                     "metadata.")
            if not self.json_data["DigestMetadataConfig"]["enabled"]:
                return None
            for required_attribute in ["authBlockSize", "goldenDigestFile"]:
                if required_attribute not in self.json_data[
                        "DigestMetadataConfig"]:
                    raise_error_and_exit("'{}' attribute is not initialized "
                                         "in the config for generating digest "
                                         "metadata."
                                         .format(required_attribute))
            for attribute in self.json_data["DigestMetadataConfig"]:
                if attribute not in ["enabled", "authBlockSize",
                                     "goldenDigestFile"]:
                    raise_error_and_exit("'{}' is not a valid attribute "
                                         "for 'DigestMetadataConfig'"
                                         .format(attribute))
        return self.json_data["DigestMetadataConfig"]

    def get_post_installs(self):
        return self.json_data["PostInstalls"]

    def get_os(self):
        return self.json_data["OS"]

    def get_filesystem_type(self):
        return self.json_data["FilesystemType"]

    def get_base(self):
        return self.json_data["Base"]

    def get_image_size(self):
        return int(self.json_data["ImageSize"])

    def get_filesystem_cleanup_paths(self):
        return self.json_data["FilesystemCleanup"]

    def init_default(self):
        """
        Initialize the json_data object with default values for all possible
        keys in the Build-FS CONFIG.
        """
        self.json_data.setdefault("Output", None)
        self.json_data.setdefault("PreInstalls", {})
        self.json_data.setdefault("CopyTargets", [])
        self.json_data.setdefault("FSMountPointConfg", None)
        self.json_data.setdefault("DigestMetadataConfig", None)
        self.json_data.setdefault("PostInstalls", {})
        self.json_data.setdefault("OS", None)
        self.json_data.setdefault("FilesystemType", "standard")
        self.json_data.setdefault("Base", None)
        self.json_data.setdefault("ImageSize", str(0))
        self.json_data.setdefault("FilesystemCleanup", [])

    def validate(self):
        """
        Validate the types of Fields in the input CONFIG/MANIFEST file.
        """
        for field in self.required_fields:
            if field not in self.json_data:
                raise_error_and_exit("Required Field: '" + field
                                     + "' absent from the input CONFIG file: '"
                                     + self.json_file + "'.")
        for field in self.string_fields:
            if field in self.json_data and not isinstance(
                    self.json_data[field],
                    str) and self.json_data[field] is not None:
                raise_error_and_exit(
                        field + ": Value is not a string in the input CONFIG"
                        + " file: '" + self.json_file + "'.")
        for field in self.array_fields:
            if field in self.json_data and not isinstance(
                    self.json_data[field],
                    list) and self.json_data[field] is not None:
                raise_error_and_exit(
                        field + ": Value is not a list in the input CONFIG"
                        + " file: '" + self.json_file + "'.")
        for field in self.dict_fields:
            if field in self.json_data and not isinstance(
                    self.json_data[field],
                    dict) and self.json_data[field] is not None:
                raise_error_and_exit(
                        field + ": Value is not a dict in the input CONFIG"
                        + " file: '" + self.json_file + "'.")


class QNXFileParser(FileParser):
    """
    Derivative of FileParser for QNX specific parsing and validation
    """

    string_fields = FileParser.string_fields + ['ImageType']
    array_fields = [
            x for x in FileParser.array_fields if x != 'FilesystemCleanup'
            ] + ['BuildFileHeaderFiles']

    def __init__(self, json_file=None, json_str=None):
        """
        QNXFileParser Constructor.

        Parameters
        ----------
        json_file   : str
                      Path to input Build-FS CONFIG/MANIFEST file.
        json_str   : str
                     Json file represented as a string.

        Returns
        -------
        QNXFileParser
            QNXFileParser object is returned
        """
        super().__init__(json_file=json_file, json_str=json_str)

    def get_image_type(self):
        return self.json_data["ImageType"]

    def get_buildfile_header_files(self):
        return self.json_data["BuildFileHeaderFiles"]

    def get_filesystem_cleanup_paths(self):
        raise_error_and_exit(
                "class '{}' has no attribute " +
                "'get_filesystem_cleanup_paths'".format(
                    self.__class__.__name__))

    def init_default(self):
        """
        Initialize the json_data object with default values for all possible
        keys in the Build-FS CONFIG.
        """
        self.json_data.setdefault("ImageSize", str(QNX_DEFAULT_IMAGE_SIZE))
        self.json_data.setdefault("ImageType", "XFS")
        self.json_data.setdefault("BuildFileHeaderFiles", [])
        super().init_default()

    def validate(self):
        """
        Validate ImageType Field for QNX
        """
        super().validate()
        if "ImageType" in self.json_data and \
                (self.json_data["ImageType"] not in ["IFS", "XFS"]):
            raise_error_and_exit("ImageType: '{}' not recognized\nfile: '{}'."
                                 .format(self.json_data['ImageType'],
                                         self.json_file))


class LinuxFileParser(FileParser):
    """
    Derivative of FileParser for Linux specific parsing and validation
    """

    string_fields = FileParser.string_fields + ['Distro']
    array_fields = FileParser.array_fields + [
            'Mirrors', 'DebianPackages',
            'FilesystemInclude', 'AssociatedFilesystems'
            ]
    dict_fields = FileParser.dict_fields + [
            'Users', 'Groups', 'Memberships', 'Mounts',
            'SELinux', 'Reserved'
            ]

    def __init__(self, json_file=None, json_str=None):
        """
        LinuxFileParser Constructor.

        Parameters
        ----------
        json_file   : str
                      Path to input Build-FS CONFIG/MANIFEST file.
        json_str   : str
                     Json file represented as a string.

        Returns
        -------
        LinuxFileParser
            LinuxFileParser object is returned
        """
        super().__init__(json_file=json_file, json_str=json_str)

    def get_ubuntu_distro(self):
        return self.json_data["Distro"]

    def get_mirrors(self):
        return self.json_data["Mirrors"]

    def get_debian_packages(self):
        return self.json_data["DebianPackages"]

    def get_users(self):
        return self.json_data["Users"]

    def get_groups(self):
        return self.json_data["Groups"]

    def get_memberships(self):
        return self.json_data["Memberships"]

    def get_selinux_info(self):
        return self.json_data["SELinux"]

    def get_hostname(self):
        return self.json_data["Hostname"]

    def get_mounts(self):
        return self.json_data["Mounts"]

    def get_associated_fs(self):
        return self.json_data["AssociatedFilesystems"]

    def get_filesystem_include_paths(self):
        return self.json_data["FilesystemInclude"]

    def init_default(self):
        """
        Initialize the json_data object with default values for all possible
        keys in the Build-FS CONFIG.
        """
        self.json_data.setdefault("Distro", None)
        self.json_data.setdefault("Mirrors", [])
        self.json_data.setdefault("DebianPackages", [])
        self.json_data.setdefault("Users", {})
        self.json_data.setdefault("Groups", {})
        self.json_data.setdefault("Memberships", {})
        self.json_data.setdefault("SELinux", {})
        self.json_data.setdefault("ImageSize", str(LINUX_DEFAULT_IMAGE_SIZE))
        self.json_data.setdefault("Hostname", None)
        self.json_data.setdefault("Mounts", {})
        self.json_data.setdefault("AssociatedFilesystems", [])
        self.json_data.setdefault("FilesystemInclude", [])
        super().init_default()


class Image:
    """
    Class for holding image information and providing utility functions
    """

    def __init__(self, image_path, noblks=0, input_stream="/dev/zero",
                 bs=1048576):
        """
        Image Constructor.

        Parameters
        ----------
        image_path      : str
                          Path to ouptut image file.
        noblks          : int
                          Size of target filesystem directory in blocks.
        input_stream    : str
                          Input dev stream. (default is /dev/zero.)
        bs              : int
                          Block Size of image. (default is 1048576)

        Returns
        -------
        Image
            Image object is returned
        """
        self.image_path = image_path + IMG_EXT
        self.noblks = noblks
        self.input_stream = input_stream
        self.bs = bs

    def create_image(self):
        """
        Creates output image file blob without any filesystem.
        """
        Executor.execute_on_host(
                "dd", "if=" + self.input_stream + " of=" + self.image_path
                + " bs=" + str(self.bs) + " count=" + str(self.noblks))

    @staticmethod
    def get_blocks(path, block_size):
        """
        Returns the total number of blocks required to store the filesystem
        contents

        Parameters
        ----------
        path        : str
                      Path to target filesystem directory.
        block_size  : int
                      Block Size of the filesystem

        Returns
        -------
        int
            Size of target filesystem in blocks(integer).
        """
        tot_blks = 0
        hardlink_dict = {}
        for dirpath, dirnames, filenames in os.walk(os.path.abspath(path)):
            for f in filenames + dirnames:
                fp = os.path.join(dirpath, f)
                lstat = os.lstat(fp)
                size = lstat.st_size
                if lstat.st_nlink > 1:
                    if lstat.st_ino in hardlink_dict:
                        size = 0
                    else:
                        hardlink_dict[lstat.st_ino] = 1
                tot_blks += size//block_size + (1 if size % block_size else 0)

        return tot_blks


class QNX6Image():
    """
    Class for generating QNX filesystem Image.
    """
    def __init__(self, image_path, qnx_build_file, manifest_file=None,
                 log_level=logging.WARNING):
        """
        QNX6Image Constructor.

        Parameters
        ----------
        image_path      : str
                          Path to output QNX6 image file.
        qnx_build_file  : str
                          Path to QNX Build File.
        manifest_file   : str
                          Path to output manifest file. (default is None.)
                          No manifest will be created if None.
        log_level       : int
                          Increase verbosity of mkxfs.
                          (default is logging.WARNING)

        Returns
        -------
        QNX6Image
            QNX6Image object is returned
        """
        self.image_path = image_path + IMG_EXT
        self.qnx_build_file = qnx_build_file
        self.manifest_file = manifest_file
        self.log_level = log_level
        self.mkxfs_options = " -nn -D -t qnx6fsimg "

    def createImage(self):
        """
        Create QNX6 Image with given details in object constructor.
        """
        # Create qnx6fsimg image
        logging.info("Creating '%s'..." % self.image_path)
        if self.manifest_file:
            self.mkxfs_options += " -f " + self.manifest_file
        if self.log_level <= logging.INFO:
            self.mkxfs_options += " -vv"
        if (not self.qnx_build_file):
            raise_error_and_exit("Build-FS was unable to determine the"
                                 "name of the QNX buildfile from the Build-FS "
                                 "config")
        Executor.execute_on_host(os.environ["QNX_HOST"] +
                                 "/usr/bin/mkxfs", self.mkxfs_options + " "
                                 + self.qnx_build_file + " "
                                 + self.image_path)
        logging.info("targetfs '%s' created." % self.image_path)


class QNXIFSImage():
    """
    Class for generating QNX IFS Image.
    """
    def __init__(self, image_path, qnx_build_file, manifest_file=None,
                 log_level=logging.WARNING):
        """
        QNXIFSImage Constructor.

        Parameters
        ----------
        image_path      : str
                          Path to output QNX IFS image file.
        qnx_build_file  : str
                          Path to QNX Build File.
        manifest_file   : str
                          Path to output manifest file. (default is None.)
                          No manifest will be created if None.
                          Currently unused
        log_level       : int
                          Increase verbosity of mkxfs.
                          (default is logging.WARNING)

        Returns
        -------
        QNXIFSImage
            QNXIFSImage object is returned
        """
        self.image_path = image_path + IFS_IMG_EXT
        self.qnx_build_file = qnx_build_file
        self.manifest_file = manifest_file
        self.log_level = log_level
        self.mkifs_options = ""

    def createImage(self):
        """
        Create QNX IFS Image with given details in object constructor.
        """
        # Create qnx6fsimg image
        logging.info("Creating '{}'...".format(self.image_path))
        if self.log_level <= logging.INFO:
            self.mkifs_options += " -v "
        Executor.execute_on_host(
                os.environ["QNX_HOST"] + "/usr/bin/mkifs",
                " -a {} ".format(os.path.basename(self.image_path)) +
                " ".join([self.mkifs_options,
                         self.qnx_build_file,
                         self.image_path]))
        logging.info("IFS '{}' created.".format(self.image_path))


class ExtImage(Image):
    """
    Class for generating EXT filesystem Image.
    Inherits from Image class.
    """
    def __init__(
                self, image_path, tree_blocks=0, final_size=8589934592,
                input_stream="/dev/zero", fs_type="ext4", reserve=0.1,
                overrides=None):
        """
        ExtImage Constructor.

        Parameters
        ----------
        image_path      : str
                          Path to output EXT filesystem image.
        tree_blocks     : int
                          Size of target filesystem directory in blocks.
                          (default is 0)
        final_size      : int
                          Expanded size of target filesystem in the target.
                          (default is 8589934592)
        input_stream    : str
                          input dev stream for image blob.
                          (default is /dev/zero.)
        fs_type         : str
                          EXT filesystem type. (default is ext4)
        reserve         : float
                          Filesystem size percentage in float, to be reserved
                          for filesystem metadata. (default is 0.1)

        Returns
        -------
        ExtImage
            ExtImage object is returned
        """
        self.fs_type = fs_type
        self.final_size = final_size
        self.reserve = reserve
        category_data = self.get_ext_category_data(self.final_size)
        self.usage_type = category_data["usage_type"]
        self.block_size = category_data["block_size"]
        self.byte_inode_ratio = category_data["byte_inode_ratio"]
        self.inode_size = category_data["inode_size"]
        self.journal_blocks = category_data["journal_blocks"]
        self.journal_size = math.ceil(
                (self.journal_blocks*self.block_size) / (1024*1024))
        self.inode_byte_ratio = 1 / self.byte_inode_ratio
        self.tree_blocks = tree_blocks

        super().__init__(image_path, tree_blocks, input_stream,
                         bs=self.block_size)

    @property
    def tree_blocks(self):
        """
        Property function for getting value of self.tree_blocks.

        Returns
        -------
        int
            Number of blocks required for filesystem tree.
        """
        return self.__tree_blocks

    @tree_blocks.setter
    def tree_blocks(self, value):
        """
        Property function for setting value of self.__tree_blocks.

        Parameters
        ----------
        value   : int
                  Size of the tree
        """
        self.__tree_blocks = value
        logging.debug("Block Size: %d", self.block_size)

        # Add No of Blocks required for the tree
        total_blocks = self.__tree_blocks
        logging.debug("Tree Blocks: %d", total_blocks)

        # Add reservation space for additional files
        total_blocks = math.ceil(
                (1 + self.reserve)*total_blocks)
        logging.debug("Tree + Reserve Blocks: %d", total_blocks)

        # Add No of Blocks required for the journal
        # Add twice the amount, since for an empty tree the image generated
        # is too small for creating a journal.
        total_blocks += 2*self.journal_blocks
        logging.debug("Tree + Reserve + Journal Blocks: %d", total_blocks)

        # Add No of Blocks required for inodes
        total_inodes = math.ceil(
                total_blocks * self.block_size * self.inode_byte_ratio)
        total_inode_blocks = math.ceil(
                (total_inodes * self.inode_size)/self.block_size)
        total_blocks += total_inode_blocks
        logging.debug("Tree + Journal + Reserve + Inodes Blocks: %d",
                      total_blocks)

        total_size = total_blocks * self.block_size
        if self.final_size < total_size:
            raise_error_and_exit((
                "Target filesystem tree size '{}'B + metadata size '{}'B " +
                "is larger than specified ImageSize '{}'B. ").format(
                    self.__tree_blocks * self.block_size,
                    (self.total_blocks - self.__tree_blocks)*self.block_size,
                    self.final_size))
        self.noblks = total_blocks

    def create_image(self):
        """
        Creates EXT Image with host mke2fs tool.
        Arguments are calculated with values provided in the constructor.
        """
        super().create_image()
        ver_out = Executor.execute_on_host(
                'e2fsck', '-V', stderr=PIPE)["stderr"]
        e2fsprogs_ver_out = Executor.execute_on_host(
                'grep', r'-oP "version\s*\K[0-9.]*"',
                stdin=ver_out, stdout=PIPE)["stdout"]
        mkfs_opts = "-t {fs_type} -i {byinode_ratio} -J size={journal_size}" \
                    " -I {inode_size} -b {block_size} -F {image_path}".format(
                            fs_type=self.fs_type,
                            byinode_ratio=self.byte_inode_ratio,
                            journal_size=self.journal_size,
                            inode_size=self.inode_size,
                            block_size=self.block_size,
                            image_path=self.image_path)
        e2fsprogs_ver = e2fsprogs_ver_out.decode('utf-8')
        logging.info("Version of e2fsprogs is : " + e2fsprogs_ver)
        if LooseVersion(e2fsprogs_ver) > LooseVersion("1.43"):
            mkfs_opts += EXT_BACKWARDS_COMPAT_OPT

        Executor.execute_on_host("mke2fs", mkfs_opts)

    @classmethod
    def get_ext_category_data(cls, size):
        """
        Get EXT category data based on how EXT filesystems
        are classified based on final size the filesystem image
        will have when flashed to the target. Classifcation
        is done with the rules provided by mke2fs.

        Parameters
        ----------
        size    : int
                  Final size the filesystem image will have when flashed on the
                  target.

        Returns
        -------
        dict
            category_data (dict) of the format
            {
                "min_size": int,
                "max_size": int,
                "usage_type": str,
                "block_size": int,
                "byte_inode_ratio": float,
                "inode_size": int,
                "journal_blocks": int,
            }
        """
        categories_data = cls.get_category_dict()
        for category in categories_data.keys():
            category_data = categories_data[category]
            min_size = category_data["min_size"]
            if category_data["max_size"] > 0:
                max_size = category_data["max_size"]
            else:
                max_size = sys.maxsize
            if size >= min_size and size <= max_size:
                return category_data

        # Control will reach here only if category isn't found
        return None

    @classmethod
    def get_category_dict(cls, ext_data=EXT_DATA):
        """
        Convert 2D category data into a dict for easy access

        Parameters
        ----------
        ext_data    : list
                      EXT Image classification 2D array of the format:
                      [
                        [category1, min_size, max_size, usage_type,
                         block_size, byte_inode_ratio, journal_blocks],
                        [category2, min_size, max_size, usage_type,
                         block_size, byte_inode_ratio, journal_blocks],
                         .
                         .
                      ]
                      (default is EXT4 data)

        Returns
        -------
        dict
            categories_data (dict) of the format:
            {
                "category1": {
                    "min_size": int,
                    "max_size": int,
                    "usage_type": "str",
                    "block_size": int,
                    "byte_inode_ratio": float,
                    "journal_blocks": int
                    },
                "category2": {
                    "min_size": int,
                    .
                    .
                    },
                .
                .
            }
        """
        categories_data = {}
        category_data = {}
        for row in ext_data:
            category_data["min_size"] = row[1]
            category_data["max_size"] = row[2]
            category_data["usage_type"] = row[3]
            category_data["block_size"] = row[4]
            category_data["byte_inode_ratio"] = row[5]
            category_data["inode_size"] = row[6]
            try:
                category_data["journal_blocks"] = row[7]
            except IndexError:
                category_data["journal_blocks"] = 0

            categories_data[row[0]] = category_data.copy()

        return categories_data


# Helper wrappers
def init_logger(log_level, output_dir):
    os.makedirs(os.path.abspath(
            output_dir), exist_ok=True)
    logging.basicConfig(
            level=getattr(logging, log_level.upper()),
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.StreamHandler()
                ]
            )


def define_options(parser):
    """
    Define options which control Build-FS Workflow,
    and parses Build-FS commandline.

    Parameters
    ----------
    parser      : OptionParser
                  OptionParser object for parsing commandline.

    Returns
    -------
    tuple
        (options, args)
        options (Values object), contains all the parsed options in cmdline.
        args (list), contains the remaining unparsed arguments.
    """
    parser.add_option(
            "-i", "--input", type="string", dest="json_path",
            help="Input config file.")
    parser.add_option(
            "-o", "--output", type="string", dest="output_folder",
            default="./", help="Output folder. Default option is ${PWD}.")
    parser.add_option(
            "-m", "--manifest-only", action="store_true", dest="manifest_only",
            default=False, help="Generate Manifest only. Do not install.")
    parser.add_option(
            "-v", "--version", action="callback", callback=print_version,
            help="Print Version and exit.")
    parser.add_option(
            "--create-tar", type="choice", choices=("yes", "no"),
            default="no", dest="create_tar",
            help="Create output tarball. Valid options are 'yes','no'."
            + " Option not applicable for OS: QNX"
            + "\nDefault option is 'no'.")
    parser.add_option(
            "--create-image", type="choice", choices=("yes", "no"),
            default="yes", dest="create_image",
            help="Create output image. Valid options are 'yes','no'."
            + " Default option is 'yes'.")
    parser.add_option(
            "-w", "--nv-workspace", type="string", dest="nv_workspace",
            help="Workspace from which files to be copied to the target"
            + " are obtained.")
    parser.add_option(
            "--copytarget-source-type", type="string",
            dest="copytarget_source_type", default="pdk_sdk_installed_path",
            help="Source type argument to be passed to copytarget tool."
            + " Default option is 'pdk_sdk_installed_path'.")
    parser.add_option(
            "--log-level", type="choice", choices=(
                "debug", "info", "warning", "error", "critical"),
            default="info", dest="log_level",
            help="Set log level for the tool")
    parser.add_option(
            "-f", "--filesystem-working-directory", type="string",
            dest="filesystem_work_folder", help="Filesystem working folder"
            + " will be the folder specified. If not specified, a tmp folder"
            + " is created which acts as working folder."
            + " Option not applicable for OS: QNX")
    parser.add_option(
            "--working-directory", type="string",
            dest="work_folder", help="Build-FS working folder"
            + " will be the folder specified. If not specified, a tmp folder"
            + " is created which acts as working folder.")
    parser.add_option(
            "--generate-intermediate", type="choice", choices=("yes", "no"),
            dest="generate_intermediate", default="yes",
            help="Create intermediate Parent CONFIG outputs. Valid options"
            + " are 'yes', 'no'. Default option is 'yes'.")
    parser.add_option(
            "--generate-target-size-file", dest="generate_target_size_file",
            type="choice", choices=("yes", "no"), default="no",
            help="Create a file (in YAML format) with details pertaining to "
            "the sizes of files copied to the filesystem.")
    parser.add_option(
            "--size-limits-file", dest="size_limits_file", default=None,
            help="Specify the path to a manifest that lists the maximum"
            "size of files that each module can use. Note: This option"
            "can only be used along with --target-size-file")
    parser.add_option(
            "--create-spreadsheet", dest="spreadsheet_file", default=None,
            help="Specify the path of the Excel spreadsheet the should "
            "contain the items in the FS. "
            "Note: This spreadsheet will be in XML format.")
    parser.add_option(
            "--spreadsheet-metadata", dest="spreadsheet_meta", default=None,
            help="Specify the path of the spreadsheet metadata file."
            "This metadata file controls what attributes from the "
            "CopyTarget YAML manifest appear in the spreadsheet")
    return parser.parse_args()


def print_version(option, opt, value, parser):
    print("Build-FS Version: " + VERSION)
    sys.exit(0)


if __name__ == "__main__":
    MY_DIR = os.path.dirname(os.path.realpath(__file__))
    Environment.unset(unset_vars)
    Environment.set_exist(set_vars)
    # Parse command line
    parser = OptionParser()
    (options, args) = define_options(parser)
    init_logger(options.log_level, options.output_folder)

    if not options.nv_workspace:
        parser.print_help()
        raise_error_and_exit("No value for NV_WORKSPACE provided"
                             + " with the '-w' option.")
    if not os.path.exists(options.nv_workspace):
        raise_error_and_exit(
                "NV_WORKSPACE: '" + options.nv_workspace + "' doesn't exist.")
    Environment.set('NV_WORKSPACE', options.nv_workspace)

    # Source the main config.json common section followed by OS section,
    # exit if any REQUIRED_VARIABLE has not been defined
    if Environment.get("BUILD_FS_ENV"):
        BUILD_FS_ENV = Environment.get("BUILD_FS_ENV")
    logging.info("Reading Configuration File: '{env}'".format(
            env=BUILD_FS_ENV))
    Environment.source(BUILD_FS_ENV, 'common')
    if Environment.get('REQUIRED_VARIABLES'):
        Environment.exit_if_not_defined(
                Environment.get('REQUIRED_VARIABLES').split(','))
        Environment.unset(['REQUIRED_VARIABLES'])

    if options.json_path:
        json_file = options.json_path
    elif args:
        json_file = args[0]
    else:
        parser.print_help()
        raise_error_and_exit("No CONFIG provided with the '-i' option.")
    if json_file == "STDIN":
        json_str = sys.stdin.read()
    elif os.path.exists(json_file):
        with open(json_file, 'r', encoding='utf-8') as f:
            json_str = f.read()
    else:
        raise_error_and_exit("CONFIG file: '" + json_file + "' doesn't exist.")

    config_os = FileParser(json_file=json_file, json_str=json_str).get_os()
    Environment.source(BUILD_FS_ENV, config_os.lower())
    if Environment.get('REQUIRED_VARIABLES'):
        Environment.exit_if_not_defined(
                Environment.get('REQUIRED_VARIABLES').split(','))
        Environment.unset(['REQUIRED_VARIABLES'])
    if Environment.get('BUILD_FS_DIR'):
        BUILD_FS_DIR = Environment.get('BUILD_FS_DIR')

    if options.size_limits_file is not None:
        if options.generate_target_size_file != "yes":
            raise_error_and_exit("Option --size-limits-file can only be used "
                                 "with --generate-target-size-file")

    if config_os.lower() == "linux":
        build_fs = LinuxBuildFS(options, json_file, BUILD_FS_DIR, json_str)
    elif config_os.lower() == "qnx":
        if options.filesystem_work_folder is not None \
                and options.filesystem_work_folder != "/":
            raise_error_and_exit("Option -f/--filesystem-working-directory "
                                 "must have value '/' for QNX")
        if options.create_tar == "yes":
            raise_error_and_exit("Option --create-tar "
                                 "must be 'no' for QNX")
        qnx_build_file = Environment.get('QNX_BUILD_FILE')
        qnx_passwd_file = Environment.get('QNX_PASSWD_FILE')
        qnx_group_file = Environment.get('QNX_GROUP_FILE')
        if not qnx_passwd_file:
            raise_error_and_exit("QNX_PASSWD_FILE is not defined.")
        if not os.path.exists(qnx_passwd_file):
            raise_error_and_exit("QNX_PASSWD_FILE: '" + qnx_passwd_file +
                                 "' Doesn't exist.")
        if not qnx_group_file:
            raise_error_and_exit("QNX_GROUP_FILE is not defined.")
        if not os.path.exists(qnx_group_file):
            raise_error_and_exit("QNX_GROUP_FILE: '" + qnx_group_file +
                                 "Doesn't exist.")
        build_fs = QNXBuildFS(options, json_file, qnx_build_file,
                              qnx_passwd_file, qnx_group_file, json_str)
    else:
        raise_error_and_exit(config_os + "is not supported.\n")

    Environment.set('BUILD_FS_OUTDIR', options.output_folder)

    # Perform build_fs tasks
    build_fs.pre_build()
    build_fs.build()
    build_fs.post_build()
    build_fs.process_output()

    logging.info("=======Build-FS Successfully Finished Execution=======")
