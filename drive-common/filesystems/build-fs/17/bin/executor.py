#!/usr/bin/python3
#
# Copyright (c) 2020-2021, NVIDIA CORPORATION & AFFILIATES.  All rights reserved.
#
# NVIDIA CORPORATION, its affiliates and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION or its affiliates is strictly prohibited.

import os
import shlex
import shutil
import sys
import logging
from subprocess import Popen, PIPE

# String Constants
NOT_EXISTS = "/not/exists/"

# ============================
# Linux Specific
# ============================
QEMU_PATH = "/usr/bin/"
QEMU_BIN = "/usr/bin/qemu-aarch64-static"


class Executor:
    """
    Executor class is used to execute commands on the host/target.
    It executes commands on the host/target using the subprocess module.
    """
    def __init__(self, work_dir=NOT_EXISTS,
                 filesystem_work_dir=NOT_EXISTS):
        """
        Executor class constructor.

        Parameters
        ----------
        work_dir            : str
                              Path to Build-FS work directory.
                              (default is /not/exists/)
        filesystem_work_dir : str
                              Path to target filesystem directory.
                              (default is /not/exists/)

        Returns
        -------
        Executor
            Executor object is returned.
        """
        self.work_dir = work_dir
        self.filesystem_work_dir = filesystem_work_dir
        self.target_mount_list = []

    @staticmethod
    def execute_on_host(program, arguments, exit_on_failure=True, stdin=None,
                        stdout=sys.stdout, stderr=sys.stderr, silent=False):
        """
        Executes command on the host machine.

        Parameters
        ----------
        program         : str
                          Binary to be executed.
        arguments       : str
                          Args to be passed to the binary being executed.
        exit_on_failure : bool
                          Controls if Build-FS should exit on Non-zero
                          return code returned by binary. (default is True)
        stdin           : str
                          String of data to be sent to the binary as input.
                          (default is None)
        stdout          : file
                          File object to which binary's standard output shall
                          be written to. (default is sys.stdout)
        stderr          : file
                          File object to which binary's standard error shall
                          be written to. (default is sys.stderr)

        Returns
        -------
        dict
            {"stdout": stdout, "stderr": stderr, "rc": rc}
            dict object with keys stdout, stderr and rc, with values
            of standard output, standard error and return code of
            the binary execution.
        """
        cmd_string = program + ' ' + arguments
        cmd = shlex.split(cmd_string)
        if not silent:
            logging.info("Executing " + cmd_string)
        process = Popen(cmd, stdin=PIPE, stdout=stdout, stderr=stderr)
        stdout, stderr = process.communicate(stdin)
        rc = process.returncode
        if rc != 0 and exit_on_failure is True:
            raise_error_and_exit('Command returned non-zero error code:\n'
                                 + cmd_string, rc)
        return {"stdout": stdout, "stderr": stderr, "rc": rc}

    def execute_for_arm64(self, program, arguments,
                          exit_on_failure=True, stdin=None, stdout=sys.stdout,
                          stderr=sys.stderr, silent=False):
        """
        Executes command in the target filesystem chroot.
        This function shall set up arm64 chroot,
        execute the command in the chroot environment,
        and undo the arm64 chroot setup.

        Parameters
        ----------
        program         : str
                          Binary to be executed.
        arguments       : str
                          Args to be passed to the binary being executed.
        exit_on_failure : bool
                          Controls if Build-FS should exit on Non-zero
                          return code returned by binary. (default is True)
        stdin           : str
                          String of data to be sent to the binary as input.
                          (default is None)
        stdout          : file
                          File object to which binary's standard output shall
                          be written to. (default is sys.stdout)
        stderr          : file
                          File object to which binary's standard error shall
                          be written to. (default is sys.stderr)
        Returns
        -------
        dict
            {"stdout": stdout, "stderr": stderr, "rc": rc}
            dict object with keys stdout, stderr and rc, with values
            of standard output, standard error and return code of
            the binary execution.
        """
        self.setup_arm64_chroot()
        output = self.execute_on_host(
                    'chroot', self.filesystem_work_dir + ' ' + program + ' ' +
                    arguments, exit_on_failure=exit_on_failure, stdin=stdin,
                    stdout=stdout, stderr=stderr, silent=silent)
        self.cleanup_arm64_chroot()
        return output

    def setup_arm64_chroot(self):
        """
        Sets up the the arm64 target filesystem directory for chroot.
        """
        # Setup arm64
        global QEMU_PATH
        if os.getenv('QEMU_PATH'):
            QEMU_PATH = os.getenv('QEMU_PATH')

        shutil.copy2(QEMU_PATH+'/qemu-aarch64-static',
                     self.filesystem_work_dir + '/usr/bin/')
        perm = os.stat(self.filesystem_work_dir + QEMU_BIN).st_mode & 0o777
        perm_withx = perm | 0o111
        os.chmod(self.filesystem_work_dir + QEMU_BIN, perm_withx)
        self.execute_on_host('mount', '--bind -r /dev '
                             + self.filesystem_work_dir
                             + '/dev', silent=True)
        self.execute_on_host('mount', '--bind -r /sys '
                             + self.filesystem_work_dir
                             + '/sys', silent=True)
        self.execute_on_host('mount', '--bind -r /proc '
                             + self.filesystem_work_dir
                             + '/proc', silent=True)
        # For bind mounts to be truly read-only, you need
        # to remount it as read-only to enable the RO flag.
        self.execute_on_host('mount', '-o bind,remount,ro '
                             + '/dev/ '
                             + self.filesystem_work_dir
                             + '/dev/', silent=True)

        for n in self.target_mount_list:
            self.execute_on_host('mount', '--bind -r ' + n[0] + ' '
                                 + self.filesystem_work_dir
                                 + n[1], silent=True)

    def cleanup_arm64_chroot(self):
        """
        Removes the setup required for chroot from the arm64 target filesystem
        directory.
        """
        # cleanup
        for n in self.target_mount_list:
            self.execute_on_host('umount', self.filesystem_work_dir + n[1],
                                 exit_on_failure=False, silent=True)

        self.execute_on_host('umount', self.filesystem_work_dir + '/dev',
                             exit_on_failure=False, silent=True, stderr=PIPE)
        self.execute_on_host('umount', self.filesystem_work_dir + '/sys',
                             exit_on_failure=False, silent=True, stderr=PIPE)
        self.execute_on_host('umount', self.filesystem_work_dir + '/proc',
                             exit_on_failure=False, silent=True, stderr=PIPE)
        if os.path.exists(self.filesystem_work_dir + QEMU_BIN):
            os.remove(self.filesystem_work_dir + QEMU_BIN)

    @classmethod
    def setup_multi_binary_exec(cls):
        """
        Sets up the Host for executing aarch64 binaries via binfmts.
        """
        mnt_out = cls.execute_on_host('mount', '', stdout=PIPE)["stdout"]
        rc = cls.execute_on_host(
                'grep', '"binfmt_misc on /proc/sys/fs/binfmt_misc"',
                stdin=mnt_out, stdout=PIPE, exit_on_failure=False)["rc"]
        if rc != 0:
            cls.execute_on_host(
                    'mount',
                    'binfmt_misc -t binfmt_misc /proc/sys/fs/binfmt_misc')
        cls.execute_on_host(
                'update-binfmts', ' --enable qemu-aarch64 ')


# Exit function
def raise_error_and_exit(error, rc=1):
    """
    Exit function

    Parameters
    ----------
    error   : str
              Error string.
    rc      : int
              Exit code. (default is 1)
    """
    logging.error("\n\nExiting due to error:\n" + error)
    sys.exit(rc)
