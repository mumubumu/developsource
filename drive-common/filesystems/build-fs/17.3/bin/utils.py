#!/usr/bin/python3
#
# Copyright (c) 2020-2021, NVIDIA CORPORATION & AFFILIATES.  All rights reserved.
#
# NVIDIA CORPORATION, its affiliates and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION or its affiliates is strictly prohibited.

import hashlib
import os
import sys
from executor import Executor, PIPE
from collections import OrderedDict
import logging

# Constants
MAGIC_MAP = {
    b"\x42\x5a\x68": "bz2",
    b"\x50\x4b\x03\x04": "zip",
    b"\x1f\x8b\x08": "gz",
    b"\xfd\x37\x7a\x58\x5A\x00": "xz"
}
MAGIC_MAP_OD = OrderedDict(sorted(MAGIC_MAP.items(), reverse=True))
MAGIC_MAP_LEN = max(len(x) for x in MAGIC_MAP_OD)
TEXT_CHARS = bytearray(
                {7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7f})


def md5(fname):
    """
    Returns the md5sum hash of a given filename.
    If file is a symlink, function returns the md5sum hash
    of the path symlink points to.

    Parameters
    ----------
    fname       : str
                  Path to file, whose md5sum is to be calculated.

    Returns
    -------
    str
        md5sum of the
        1. contents of input file, if it is not a symlink.
        2. path pointed to, if it is a symlink.
    """
    hash_md5 = hashlib.md5()
    if not os.path.islink(fname) and os.path.lexists(fname):
        with open(fname, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    elif os.path.islink(fname):
        return hashlib.md5(os.readlink(fname).encode()).hexdigest()
    else:
        raise_error_and_exit("md5: No such file or directory: "
                             + fname + ".")


def md5s(string):
    """
    Return the md5sum hash of a given string.

    Parameters
    ----------
    string      : str
                  String to get md5 hash out of.

    Returns
    -------
    str
        md5sum of the string.
    """

    return hashlib.md5(string.encode()).hexdigest()


# Exit function
def raise_error_and_exit(error, rc=1):
    """
    Build-FS exit function

    Parameters
    ----------
    error   : str
              Error string.
    rc      : int
              Exit code. (default is 1)
    """
    logging.error("\n\nExiting due to error:\n" + error)
    sys.exit(rc)


def get_files_from_dir(directory):
    filelist = []
    root = directory
    for r, d, f in os.walk(directory):
        for fil in f:
            filelist += ["/" + os.path.normpath(os.path.join(
                        os.path.relpath(r, root), fil))]
        for fil in d:
            if os.path.islink(os.path.join(r, fil)):
                filelist += ["/" + os.path.normpath(os.path.join(
                            os.path.relpath(r, root), fil))]

    return filelist


def nv_abs_path(path):
    if path is None:
        raise RuntimeError(
                "nv_abs_path: NoneType object passed as argument.")
    return os.path.normpath(
                os.path.join(
                    os.path.realpath(
                        os.path.dirname(path)),
                    os.path.basename(path)))


def get_file_ext(path):
    with open(path, "rb") as fd:
        content = fd.read(MAGIC_MAP_LEN)
    for magic, filetype in MAGIC_MAP_OD.items():
        if content.startswith(magic):
            return filetype
    return None


def get_compression_tool(fil=None, comp=None):
    if fil is None and comp is None:
        raise RuntimeError(
                "_get_compression_tool: Missing arguments to function")
    compression_tools = None
    if fil:
        comp = str(get_file_ext(fil))
    comp = comp.strip('.')
    logging.debug("Compression of File {} is {}".format(
                    fil, comp))
    if comp == "gz":
        compression_tools = ["pigz", "gzip"]
    elif comp == "bz2":
        compression_tools = ["lbzip2", "bzip2"]
    elif comp == "xz":
        compression_tools = ["pixz", "xz"]
    if compression_tools:
        for tool in compression_tools:
            rc = Executor.execute_on_host(
                        'which', tool, exit_on_failure=False,
                        silent=True, stdout=PIPE)["rc"]
            if rc == 0:
                return tool
    raise RuntimeError(
            "_get_compression_tool: No compression tool found")


def is_text(path):
    try:
        with open(path, "rb") as fd:
            return not bool(fd.read(1024).translate(None, TEXT_CHARS))
    except (IsADirectoryError, FileNotFoundError):
        return False


def deep_dict_update(d1, d2, list_action="replace"):
    if not isinstance(d2, dict):
        raise_error_and_exit("deep_dict_update: Non dict update value.")
    if not isinstance(d1, dict):
        return d2
    for k, v in d2.items():
        if isinstance(v, dict):
            d1[k] = deep_dict_update(d1.get(k, {}), v)
        elif isinstance(v, list) and k in d1 and isinstance(d1[k], list):
            if list_action == "replace":
                d1[k] = v
            elif list_action == "append":
                d1[k] = d1[k] + v
            else:
                raise_error_and_exit(
                    "deep_dict_update: Incorrect list_action value: "
                    + list_action)
        else:
            d1[k] = v
    return d1
