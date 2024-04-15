#!/usr/bin/python3

# SPDX-FileCopyrightText: Copyright (c) 2019-2023, NVIDIA CORPORATION &
# AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and its licensors retain all intellectual
# property and proprietary rights in and to this software, related
# documentation and any modifications thereto.  Any use, reproduction,
# disclosure or distribution of this software and related documentation without
# an express license agreement from NVIDIA CORPORATION or its affiliates is
# strictly prohibited.

import os
import re
import sys
import copy
import json
import yaml
import shutil
import hashlib
import traceback
import subprocess
from optparse import OptionParser
from collections import OrderedDict
import xml.etree.ElementTree as ET
from io import BytesIO

VERSION = "1.4.10"
UIDMAPFILE = "/etc/passwd"
GIDMAPFILE = "/etc/group"
COMPATIBLE_MANIFEST_VERSIONS = ["1.4", "1.4.1", "1.4.2", "1.4.3", "1.4.4",
                                "1.4.5", "1.4.6", "1.4.7", "1.4.8", "1.4.9",
                                VERSION]
DEFAULT = {
    "sourceType": "pdk_sdk_installed_path",
    "filesystemType": "standard",
    "doChown": True,
    "autocreateParentDir": True
}
DOMAIN = {  # Defines a list of accepted values for a variable
    "positive_values": ['true', 'True', 'yes', True],
    "negative_values": ['false', 'False', 'no', False]
}


class CopyTarget:

    def __init__(self, targetDirectory, workspace, uidMapFile, gidMapFile,
                 sourceType=None, filesystemType=None, allOptions=None,
                 args=None):
        self.targetDirectory = targetDirectory + "/"
        self.workspace = workspace
        if not self.workspace.isspace():
            self.workspace = self.workspace + "/"
        self.sourceType = sourceType
        self.filesystemType = filesystemType
        self.uidMapFile = uidMapFile
        self.gidMapFile = gidMapFile
        self.uidMapFileExists = False
        self.gidMapFileExists = False
        self.manifest_module = None
        self.identifierDict = {}
        self.fileListDict = OrderedDict()
        self.exports = []
        self.verifyYAMLOnly = False
        self.doChown = DEFAULT["doChown"]
        self.autocreateParentDir = DEFAULT["autocreateParentDir"]
        self.spreadsheetFile = None
        self.spreadsheet = None
        self.mountPoint = None
        self.digestMetadata = CopyTargetDigestMetadata(None)
        try:
            self.recordFileSize = FileSizeRecords(allOptions.targetSizeFile)
        except AttributeError as e:
            self.recordFileSize = FileSizeRecords(None)
        if allOptions is not None:
            try:
                self.doChown = (not allOptions.noChown)
            except AttributeError as e:
                pass

            try:
                self.verifyYAMLOnly = allOptions.verifyYAMLOnly
            except AttributeError as e:
                pass

            try:
                if allOptions.mountPoint is not None:
                    self.mountPoint = CopyTarget.normpath(
                        allOptions.mountPoint)
            except AttributeError as e:
                pass

            try:
                digestMetadataJSON = allOptions.digestMetadataJSON
                if digestMetadataJSON is not None:
                    self.digestMetadata = CopyTargetDigestMetadata(
                        digestMetadataJSON, self.mountPoint)
            except AttributeError as e:
                pass

            try:
                self.spreadsheetFile = allOptions.spreadsheetFile
                if self.spreadsheetFile is not None:
                    metaSubstitutions = {
                        "filesystem": self.filesystemType,
                        "source": self.sourceType if (
                            self.sourceType
                            is not None) else DEFAULT["sourceType"]
                    }
                    self.spreadsheet = CopyTargetSpreadsheet(
                        self.spreadsheetFile, allOptions.spreadsheetMeta,
                        metaSubstitutions)
                elif allOptions.spreadsheetMeta is not None:
                    print("Error: '--spreadsheet-metadata' command line "
                          "argument can only be used with "
                          "'--create-spreadsheet'",
                          file=sys.stderr)
                    sys.exit(1)
            except AttributeError as e:
                pass

            if str(allOptions.autocreateParentDir).lower() \
                    in DOMAIN["positive_values"]:
                self.autocreateParentDir = True
            elif str(allOptions.autocreateParentDir).lower() \
                    in DOMAIN["negative_values"]:
                self.autocreateParentDir = False
            else:
                raise ValueError("'--autocreate-parent' command line argument "
                                 "received an unrecognized value '{}'. "
                                 .format(allOptions.autocreateParentDir))

        if self.doChown:
            self.createIdentifierDict()

        self.findRestrictedSourceFiles = None
        if (allOptions.blacklistYAMLManifestFile is not None) or (
                allOptions.whitelistYAMLManifestFile is not None):
            self.findRestrictedSourceFiles = FindRestrictedSourceFiles(
                allOptions, args)
        else:
            if self.verifyYAMLOnly:
                raise VerificationError("No verification operation specified")

    # Parse /etc/passwd file to determine the UID of each user
    # Parse /etc/group file to determine the GID for each group
    def createIdentifierDict(self):
        if not os.path.exists(self.uidMapFile):
            print("\033[93mNote: \"%s\" does not exist.\n"
                  "CopyTarget will not translate user name to UID \033[0m"
                  % self.uidMapFile)
            self.uidMapFileExists = False
        else:
            try:
                with open(self.uidMapFile, "r", encoding='utf-8') as f:
                    for line in f:
                        cols = line.split(":")
                        if len(cols) >= 3:
                            if cols[0] not in self.identifierDict:
                                self.identifierDict[cols[0]] = {}
                            try:
                                self.identifierDict[cols[0]]["UID"] = \
                                    int(cols[2])
                            except ValueError as e:
                                # Ignore lines from passwd file that
                                # cannot be parsed. An error will be displayed
                                # at a later stage, when consuming the values,
                                # in cases the required data is not found.
                                pass
            except:
                traceback.print_exc()
                print("Error: Unable to parse '%s'"
                      % (self.uidMapFile), file=sys.stderr)
                sys.exit(1)
            self.uidMapFileExists = True

        if not os.path.exists(self.gidMapFile):
            print("\033[93mNote: \"%s\" does not exist.\n"
                  "CopyTarget will not translate group name to GID \033[0m"
                  % self.gidMapFile)
            self.gidMapFileExists = False
        else:
            try:
                with open(self.gidMapFile, "r", encoding='utf-8') as f:
                    for line in f:
                        cols = line.split(":")
                        if len(cols) >= 3:
                            if cols[0] not in self.identifierDict:
                                self.identifierDict[cols[0]] = {}
                            try:
                                self.identifierDict[cols[0]]["GID"] = \
                                    int(cols[2])
                            except ValueError as e:
                                pass
            except:
                traceback.print_exc()
                print("Error: Unable to parse '%s'"
                      % (self.gidMapFile), file=sys.stderr)
                sys.exit(1)
            self.gidMapFileExists = True

    # Returns the UID and GID assigned to a user on the system
    def getIdentifier(self, user, type):
        # Check if the user identifier is already a numeric value
        try:
            if int(user):  # This will throw a ValueError if "user" is a string
                pass
            return int(user)
        except ValueError:
            # This means that "user" is not a numeric value and UID/GID
            # has to be derived by looking at passwd file
            pass
        try:
            return self.identifierDict[user][type]
        except KeyError as e:
            traceback.print_exc()
            if self.uidMapFileExists and (type == "UID"):
                print("Error: Username '%s' is not defined in '%s'.\n"
                      "Preserving user/group ownership without UID/GID is "
                      "not possible."
                      % (user, self.uidMapFile), file=sys.stderr)
            elif self.gidMapFileExists and (type == "GID"):
                print("Error: Group name '%s' is not defined in '%s'.\n"
                      "Preserving user/group ownership without UID/GID is "
                      "not possible."
                      % (user, self.gidMapFile), file=sys.stderr)
            elif not self.uidMapFileExists and (type == "UID"):
                print("Error: Cannot automatically find %s of user '%s' "
                      "since '%s' does not exist.\n"
                      "Preserving user/group ownership without UID/GID is "
                      "not possible."
                      % (type, user, self.uidMapFile), file=sys.stderr)
            elif not self.gidMapFileExists and (type == "GID"):
                print("Error: Cannot automatically find %s of group '%s' "
                      "since '%s' does not exist.\n"
                      "Preserving user/group ownership without UID/GID is "
                      "not possible."
                      % (type, user, self.gidMapFile), file=sys.stderr)
            sys.exit(1)

    # Iterate through each item in the CFG file and process it
    def processCFG(self, copytargetCFG, isLeafCopyTarget=True):
        print("Reading %s..." % os.path.abspath(copytargetCFG))
        cfg = OrderedDict()
        with open(copytargetCFG, "r", encoding='utf-8') as copytargetCFGHandle:
            # YAML 1.1 and 1.2  have different octal notations.
            # To prevent errors due to automatic value conversion,
            # treat all values as strings by using Loader=yaml.BaseLoader.
            cfg = CopyTarget.orderedYAMLLoad(copytargetCFGHandle,
                                             Loader=yaml.BaseLoader)
        if "version" in cfg:
            if cfg["version"] not in COMPATIBLE_MANIFEST_VERSIONS:
                print("Error: %s version %s is incompatible with %s "
                      "version %s."
                      % (copytargetCFG, cfg["version"], sys.argv[0], VERSION),
                      file=sys.stderr)
                sys.exit(1)
        else:
            print("Error: %s version is undefined."
                  % copytargetCFG, file=sys.stderr)
            sys.exit(1)
        if "exports" in cfg:
            for item in cfg["exports"]:
                if item not in self.exports:
                    self.exports.append(item)
                for key in item:
                    os.environ[key] = os.path.expandvars(item[key])
        if "imports" in cfg:
            for item in cfg["imports"]:
                itemPath = os.path.expandvars(item)
                itemAbsolutePath = CopyTarget.normpath(
                    os.path.abspath(itemPath))
                itemRelativePath = CopyTarget.normpath(itemPath)
                if itemAbsolutePath != itemRelativePath:
                    # Paths in import fields in YAML are relative to the YAML
                    # file itself
                    copytargetCFGParent = os.path.abspath(
                        os.path.join(copytargetCFG, os.pardir))
                    itemPath = os.path.abspath(
                        copytargetCFGParent + '/' + itemRelativePath)
                self.processCFG(itemPath, False)
        if "element" in cfg:
            self.manifest_module = cfg["element"]
        else:
            # Note: since a manifest can import a manifest from other modules,
            # module names are not inherited (when using imports).
            self.manifest_module = None
        i = 0
        for item in cfg["fileList"]:
            i += 1
            self.updateFileListDict(item, i, copytargetCFG)
        print("Read %d items from %s" % (i, os.path.abspath(copytargetCFG)))
        if isLeafCopyTarget:
            i = 0
            if self.findRestrictedSourceFiles is not None:
                print("Searching {} for restricted file items..."
                      .format(copytargetCFG))
                for key, item in self.fileListDict.items():
                    i += 1
                    self.findRestrictedSourceFiles.processFileItem(
                        copytargetCFG, item, i)
                self.findRestrictedSourceFiles.printResults(copytargetCFG)
            i = 0
            if not self.verifyYAMLOnly:
                for key, item in self.fileListDict.items():
                    i += 1
                    print('[%d, %s] ' % (i, key))
                    sys.stdout.flush()
                    self.processFileItem(item, i)
            if self.spreadsheet is not None:
                self.spreadsheet.createSpreadsheet()
            self.digestMetadata.writeGoldenDigestFile()
            self.fileListDict = OrderedDict()

    def trimMountPoint(self, destination):
        if self.mountPoint is not None and os.getenv(
                "NV_COPYTARGET_DESTINATION_INCLUDES_MOUNTPOINT",
                "true") != "false":
            mountPoint = self.mountPoint
            if mountPoint.endswith("/"):
                mountPoint = mountPoint[:-1]
            if destination.startswith(mountPoint):
                destination = destination[len(mountPoint):]
            else:
                print("Error: Destination entry '{}' in CopyTarget Manifest "
                      "must also contain the mount point specified by "
                      "--mount-point argument ('{}'). "
                      "If the destination entries in the CopyTarget does not "
                      "include the mount point, please export "
                      "`NV_COPYTARGET_DESTINATION_INCLUDES_MOUNTPOINT=false` "
                      "in addition to specifying the --mount-point argument."
                      .format(destination, self.mountPoint), file=sys.stderr)
                sys.exit(1)
        return destination

    def updateFileListDict(self, item, pos, copytargetCFG):
        destination = CopyTarget.normpath(
            CopyTarget.expandvars(
                self.getValueFromDict(item, "destination"), self.exports))

        # Do not add items of different filesystemType to fileListDict
        # Note: A file is copied if the manifest does not define "filesystems"
        # field
        filesystems = self.getValueFromDict(item, "filesystems", False)
        filesystemDict = None
        if filesystems:
            try:
                if isinstance(filesystems[self.filesystemType], dict):
                    filesystemDict = filesystems[self.filesystemType]
                    if not self.getValueFromDict(filesystemDict,
                                                 "required", True):
                        return
                else:
                    if not self.getValueFromDict(filesystems,
                                                 self.filesystemType, True):
                        return
            except KeyError as e:
                traceback.print_exc()
                print("Error: No directives for '%s' filesystem type is "
                      "defined for destination '%s' in '%s' manifest"
                      % (self.filesystemType, destination, copytargetCFG),
                      file=sys.stderr)
                sys.exit(1)
        if destination not in self.fileListDict:
            self.fileListDict[destination] = {}
        self.fileListDict[destination]["destination"] = self.getValueFromDict(
            item, "destination")
        if filesystemDict and "destination" in filesystemDict:
            print("Error: 'destination' key for '%s' in '%s' cannot be "
                  "overridden under 'filesystems' field"
                  % (destination, copytargetCFG), file=sys.stderr)
            sys.exit(1)
        source = self.getValueFromDict(item, "source", False, filesystemDict)
        if source is not None:
            assert isinstance(source, (dict, str)), "Invalid source field for "
            "destination '%s' in '%s'" % (destination, copytargetCFG)
            if isinstance(source, dict):
                try:
                    if self.sourceType is None:
                        # Choose default source type if the user has not
                        # explicitly specified a source type
                        self.fileListDict[destination]["source"] = source[
                            DEFAULT["sourceType"]]
                        if not self.fileListDict[destination]["source"]:
                            print("Error: '%s' source path for '%s' is not "
                                  "defined in '%s' manifest"
                                  % (DEFAULT["sourceType"],
                                     destination, copytargetCFG),
                                  file=sys.stderr)
                            sys.exit(1)
                    else:
                        self.fileListDict[destination]["source"] = source[
                            self.sourceType]
                        if not self.fileListDict[destination]["source"]:
                            print("Error: '%s' source path for '%s' is not "
                                  "defined in '%s' manifest"
                                  % (self.sourceType,
                                     destination, copytargetCFG),
                                  file=sys.stderr)
                            sys.exit(1)
                except KeyError as e:
                    if self.sourceType is None:
                        self.sourceType = DEFAULT["sourceType"]
                    print("Error: Source path for '%s' type is not defined "
                          "for '%s' in '%s' manifest"
                          % (self.sourceType, destination, copytargetCFG),
                          file=sys.stderr)
                    sys.exit(1)
            else:
                if self.sourceType is not None:
                    print("Error: '%s' manifest contains item ('%s') at "
                          "position '%d' that does not accept source types"
                          % (copytargetCFG, destination, pos), file=sys.stderr)
                    sys.exit(1)
                self.fileListDict[destination]["source"] = source
                if not self.fileListDict[destination]["source"]:
                    print("Error: Source path for '%s' is not defined "
                          "in '%s' manifest"
                          % (destination, copytargetCFG),
                          file=sys.stderr)
                    sys.exit(1)
            if self.getValueFromDict(self.fileListDict[destination],
                                     "remove", False):
                print("\033[93mWARNING: Directive for '%s' has been "
                      "redefined. Since a source is defined for this file, "
                      "it is no longer marked for removal."
                      "\033[0m" % (destination))
                self.fileListDict[destination]["remove"] = "false"
        perm = self.getValueFromDict(item, "perm", False, filesystemDict)
        if perm:
            self.fileListDict[destination]["perm"] = perm
        owner = self.getValueFromDict(item, "owner", False, filesystemDict)
        if owner:
            self.fileListDict[destination]["owner"] = owner
        group = self.getValueFromDict(item, "group", False, filesystemDict)
        if group:
            self.fileListDict[destination]["group"] = group
        raw = self.getValueFromDict(item, "raw", False, filesystemDict)
        if raw:
            self.fileListDict[destination]["raw"] = raw
        remove = self.getValueFromDict(item, "remove", False, filesystemDict)
        if remove is not None:
            self.fileListDict[destination]["remove"] = remove
            # Remove source directive (if remove is set to true)
            if self.getValueFromDict(self.fileListDict[destination],
                                     "remove"):
                if "source" in self.fileListDict[destination]:
                    print("\033[93mWARNING: Directive for '%s' has been "
                          "redefined. This file is now marked for removal."
                          "\033[0m" % (destination))
                    self.fileListDict[destination].pop("source")
        create_symlink = self.getValueFromDict(item, "create_symlink", False,
                                               filesystemDict)
        if create_symlink is not None:
            self.fileListDict[destination]["create_symlink"] = create_symlink
        module = self.getValueFromDict(item, "element", False, filesystemDict)
        if module:
            self.fileListDict[destination]["module"] = module
        elif self.manifest_module is not None:
            self.fileListDict[destination]["module"] = self.manifest_module
            item["element"] = self.manifest_module
        elif "module" in self.fileListDict[destination]:
            # Handle case where the module for an entry has already been
            # defined earlier
            pass
        else:
            self.fileListDict[destination]["module"] = "unknown"

        # Add data from the spreadsheet
        if self.spreadsheet is not None:
            self.spreadsheet.addFileEntry(destination, item)

    # Returns a value of a key from dictionary
    def getValueFromDict(self, dictionary, key, isRequired="True",
                         overrideDictionary=None):
        dict = copy.deepcopy(dictionary)
        if overrideDictionary is not None:
            try:
                dict[key] = CopyTarget.mergeDict(dict[key],
                                                 overrideDictionary[key])
            except KeyError as e:
                if key in overrideDictionary:
                    dict[key] = copy.deepcopy(overrideDictionary[key])
        try:
            if dict[key] is not None:
                if dict[key] in ['true', 'True', 'yes']:
                    return True
                elif dict[key] in ['false', 'False', 'no']:
                    return False
                return dict[key]
            else:
                raise KeyError(key)
        except KeyError as e:
            if isRequired:
                traceback.print_exc()
                print("Error: Expected key %s is not defined" % e,
                      file=sys.stderr)
                sys.exit(1)
            else:
                return None

    def executeCommand(self, command, exitOnFail=True):
        print("--> EXEC %s" % (command))
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, shell=True,
                                    check=exitOnFail, executable="bash")
            print(result.stdout.decode('utf-8'))
            return result
        except subprocess.CalledProcessError as e:
            print(e.stdout.decode('utf-8'))
            raise

    # This function merges the data from dict2 into dict1 and returns the
    # result. dict1 and dict2 remain unmodified.
    @staticmethod
    def mergeDict(dict1, dict2):
        if not isinstance(dict2, dict):
            return dict2
        result = copy.deepcopy(dict1)
        for k, v in dict2.items():
            if isinstance(v, dict):
                result[k] = CopyTarget.mergeDict(dict1.get(k, OrderedDict()),
                                                 v)
            else:
                result[k] = copy.deepcopy(dict2[k])
        return result

    @staticmethod
    def expandvars(path, exports=[]):
        for item in exports:
            for key in item:
                # replace ${export} or $export variables in the path
                # Note: this does not replace \${export} or \$export
                path = re.sub(r"(?<!\\)[$]{0}".format(key),
                              item[key], path)
                path = re.sub(r"(?<!\\)[$]{{{0}}}".format(key),
                              item[key], path)
        if os.getenv("NV_COPYTARGET_EXPANDVARS", "true") == "false":
            return path
        # Ensure there are no undefined environment variables in path
        for matches in re.findall("[$]{(.+?)}|[$]([a-zA-Z_]+[a-zA-Z0-9_]*)",
                                  path):
            match = matches[0] if matches[0] else matches[1]
            if match not in os.environ:
                print("Error: Environment variable '%s' has not been "
                      "initialized" % (match), file=sys.stderr)
                sys.exit(1)
        return os.path.expandvars(path)

    @staticmethod
    def normpath(path):
        if len(re.findall("[$]{(.+?)}|[$]([a-zA-Z_]+[a-zA-Z0-9_]*)", path
                          )) == 0 or path.find("../") == -1:
            normpath = os.path.normpath(path)
        else:
            # Do not use normpath() function if the path is relative.
            # Normalizing a path in such cases may cause issues when it also
            # contains unexpanded environment variables.
            # (e.g. "${TEST}/../../file.txt" gets incorrectly converted
            # to "../file.txt")
            normpath = path
        if path.endswith("/"):
            if normpath.replace('//', '/') == "/":
                return "/"
            return normpath.replace('//', '/') + "/"
        return normpath.replace('//', '/')

    @staticmethod
    def orderedYAMLLoad(stream, Loader=yaml.BaseLoader):

        class OrderedLoader(Loader):
            pass

        def construct_mapping(loader, node):
            return OrderedDict(loader.construct_pairs(node))

        OrderedLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            construct_mapping)
        return yaml.load(stream, OrderedLoader)

    @staticmethod
    def error(string, code=1):
        print("Error: " + string, file=sys.stderr)
        sys.exit(code)

    @staticmethod
    def warning(string):
        print("\033[93mWARNING: " + string + "\033[0m")


# CopyTarget implementation specific for Linux Target
class LinuxCopyTarget(CopyTarget):

    # Recursively create missing directories and set ownership and permission
    # This function can also be used to change permission of an already
    # existing file/directory
    def makedirs(self, destination, uid, gid, perm, isLeafNode="True"):
        if os.path.lexists(destination):
            if isLeafNode:
                if self.doChown:
                    os.chown(destination, uid, gid, follow_symlinks=False)
                if not os.path.islink(destination):
                    os.chmod(destination, perm)
            return
        else:
            parent = os.path.abspath(os.path.join(destination, os.pardir))
            self.makedirs(parent, uid, gid, perm, False)
        if not isLeafNode:
            print("--> MKDIR PARENT '%s'" % destination)
        os.mkdir(destination)
        if self.doChown:
            os.chown(destination, uid, gid)
        os.chmod(destination, perm)

    # Copy each item in the CFG file from source to target
    def processFileItem(self, item, pos):
        destination = CopyTarget.normpath(
            self.targetDirectory + self.trimMountPoint(
                CopyTarget.expandvars(
                    self.getValueFromDict(item, "destination"), self.exports)))
        remove = self.getValueFromDict(item, "remove", False)
        if not remove:
            if self.doChown:
                uid = self.getIdentifier(self.getValueFromDict(item, "owner"),
                                         "UID")
                gid = self.getIdentifier(self.getValueFromDict(item, "group"),
                                         "GID")
            else:
                uid = os.getuid()
                gid = os.getgid()
        source = self.getValueFromDict(item, "source", False)
        if source:
            module = self.getValueFromDict(item, "module")
            create_symlink = self.getValueFromDict(item, "create_symlink",
                                                   False)
            if create_symlink:
                source = CopyTarget.normpath(
                    CopyTarget.expandvars(source, self.exports))
                print("[CREATE SYMLINK] '%s' -> '%s'" % (destination, source))
            else:
                source = CopyTarget.normpath(
                    self.workspace + CopyTarget.expandvars(source,
                                                           self.exports))
                print("[CP] '%s' to '%s'" % (source, destination))
            # If parent directory does not exist, print a error and exit if
            # user has selected the appropriate option.
            # Otherwise, print a warning and automatically create all missing
            # directories.
            parent_dir = os.path.dirname(destination)
            if not os.path.exists(parent_dir):
                if not self.autocreateParentDir:
                    print("Error: Parent directory '%s' does not "
                          "exist." % (parent_dir), file=sys.stderr)
                    sys.exit(1)
                print("\033[93mWARNING: Parent directory '%s' does not exist."
                      "\nAutomatically creating the missing directories with "
                      "ownership obtained from the file being processed. "
                      "Permission has been set to %d. "
                      "(uid='%d', gid='%d', perm='%d')."
                      "\033[0m" % (parent_dir, 755, uid, gid, 755))
                self.makedirs(parent_dir, uid, gid, int("755", 8), False)
            if os.path.lexists(destination) and \
                    (not os.path.isdir(destination) or
                     os.path.islink(destination)):
                os.remove(destination)
            if create_symlink:
                os.symlink(source, destination)
                self.recordFileSize.addFile(None, destination, module,
                                            os.lstat(destination).st_size)
                self.digestMetadata.addDigestEntry(self, source, destination,
                                                   uid, gid, symlink=True)
                if self.doChown:
                    os.chown(destination, uid, gid, follow_symlinks=False)
            else:
                try:
                    shutil.copyfile(source, destination, follow_symlinks=False)
                    self.recordFileSize.addFile(source, destination, module)
                    perm = int(str(self.getValueFromDict(item, "perm")), 8)
                    self.digestMetadata.addDigestEntry(self, source,
                                                       destination, uid,
                                                       gid, perm)
                except IsADirectoryError as e:
                    traceback.print_exc()
                    if source in str(e):
                        print("Error: CopyTarget does not allow directory to "
                              "directory copy. Please itemize the files to "
                              "copy.", file=sys.stderr)
                    elif destination in str(e):
                        print("Error: CopyTarget does not allow destination "
                              "to be a directory. Please specify complete "
                              "file name.", file=sys.stderr)
                    sys.exit(1)
                if self.doChown:
                    os.chown(destination, uid, gid, follow_symlinks=False)
                if not os.path.islink(destination):
                    perm = int(str(self.getValueFromDict(item, "perm")), 8)
                    os.chmod(destination, perm)
        elif remove:
            # User wants to remove files if "remove" key is set to true in CFG
            # (and "source" is not defined)
            print("[REMOVE] '%s'" % (destination))
            # Remove files specified in CFG from ${targetdir}/
            self.executeCommand("rm -rfv " + destination)
            module = self.getValueFromDict(item, "module")
            self.recordFileSize.removeFile(destination, module)
        else:
            # Assume that user wants to create a directory or change the
            # permission of an already existing file/directory if "source"
            # is not defined.
            if not os.path.lexists(destination):
                print("[MKDIR] '%s'" % (destination))
            else:
                print("[CHANGE METADATA] '%s'" % (destination))
            # Error out if the target directory does not have a trailing slash
            if ((not os.path.lexists(destination)
                 or os.path.isdir(destination)) and destination[-1:] != "/"):
                print("Error: Directory entries must end with a trailing "
                      "slash.", file=sys.stderr)
                sys.exit(1)
            # If parent directory does not exist, print a error and exit if
            # user has selected the appropriate option.
            # Otherwise, print a warning and automatically create all missing
            # directories.
            parent_dir = os.path.abspath(os.path.join(destination, os.pardir))
            if not os.path.islink(destination):
                perm = int(str(self.getValueFromDict(item, "perm")), 8)
            else:
                perm = None
            if not os.path.exists(parent_dir):
                if not self.autocreateParentDir:
                    print("Error: Parent directory '%s' does not "
                          "exist." % (parent_dir), file=sys.stderr)
                    sys.exit(1)
                print("\033[93mWARNING: Parent directory '%s' does not exist."
                      "\nAutomatically creating the missing directories with "
                      "ownership and permission obtained from the directory "
                      "being processed (uid='%d', gid='%d', perm='%s').\033[0m"
                      % (parent_dir, uid, gid,
                         self.getValueFromDict(item, "perm")))
            self.makedirs(destination, uid, gid, perm)


# CopyTarget implementation specific to QNX
class QNXCopyTarget(CopyTarget):

    def __init__(self, targetDirectory, workspace, uidMapFile, gidMapFile,
                 sourceType=None, filesystemType=None, allOptions=None,
                 args=None):
        super().__init__(targetDirectory, workspace, uidMapFile, gidMapFile,
                         sourceType, filesystemType, allOptions, args)
        if not self.doChown:
            print("Error: \"--no-chown\" command-line option cannot be used "
                  "when creating QNX BuildFile", file=sys.stderr)
            sys.exit(1)
        if CopyTarget.normpath(targetDirectory) != "/":
            print("Error: The target directory has to be set to \"/\" while "
                  "creating QNX build files", file=sys.stderr)
            sys.exit(1)
        self.directoriesCreated = []
        self.outputBuildFile = allOptions.QNXBuildFile
        self.outputBuffer = ""
        self.outputHeaderFiles = allOptions.QNXBFHeaderFiles

    def expandSourcePath(self, path):
        return CopyTarget.normpath(
                    self.workspace + CopyTarget.expandvars(
                        path, self.exports)).strip()

    def copyfile(self, source, destination, uid, gid, perm, raw):
        # Check if parent directory has been created
        if not self.autocreateParentDir:
            parent_dir = CopyTarget.normpath(
                os.path.dirname(destination) + "/")
            if parent_dir not in self.directoriesCreated:
                print("Error: Parent directory '%s' does not "
                      "exist." % (parent_dir), file=sys.stderr)
                sys.exit(1)
        if not raw:
            self.outputBuffer += (
                "[uid={} gid={} perms={}]\t\t\t{} = {}\n").format(
                    uid, gid, perm, CopyTarget.normpath(destination),
                    CopyTarget.normpath(source))
        else:
            self.outputBuffer += (
                "[+raw uid={} gid={} perms={}]\t\t\t{} = {}\n").format(
                    uid, gid, perm, CopyTarget.normpath(destination),
                    CopyTarget.normpath(source))

    def symlink(self, source, destination, uid, gid, perm):
        # Check if parent directory has been created
        if not self.autocreateParentDir:
            parent_dir = CopyTarget.normpath(
                os.path.dirname(destination) + "/")
            if parent_dir not in self.directoriesCreated:
                print("Error: Parent directory '%s' does not "
                      "exist." % (parent_dir), file=sys.stderr)
                sys.exit(1)
        if perm is None:
            self.outputBuffer += (
                "[type=link uid={} gid={}]\t\t\t{} = {}\n").format(
                    uid, gid, CopyTarget.normpath(destination),
                    CopyTarget.normpath(source))
        else:
            self.outputBuffer += (
                "[type=link uid={} gid={} perms={}]\t{} = {}\n").format(
                    uid, gid, perm, CopyTarget.normpath(destination),
                    CopyTarget.normpath(source))

    def makedir(self, destination, uid, gid, perm):
        # Check if parent directory has been created
        self.directoriesCreated.append(CopyTarget.normpath(destination))
        if not self.autocreateParentDir:
            parent_dir = CopyTarget.normpath(
                os.path.abspath(os.path.join(destination, os.pardir)) + "/")
            if parent_dir not in self.directoriesCreated:
                print("Error: Parent directory '%s' does not "
                      "exist." % (parent_dir), file=sys.stderr)
                sys.exit(1)

        self.outputBuffer += (
            "[type=dir uid={} gid={} dperms={}]\t{}\n").format(
                uid, gid, perm, CopyTarget.normpath(destination))

    # Copy each item in the CFG file from source to target
    def processFileItem(self, item, pos):
        destination = CopyTarget.normpath(
            self.trimMountPoint(CopyTarget.normpath(
                self.targetDirectory + CopyTarget.expandvars(
                    self.getValueFromDict(
                        item, "destination"), self.exports))))
        remove = self.getValueFromDict(item, "remove", False)
        if not remove:
            uid = self.getIdentifier(self.getValueFromDict(item, "owner"),
                                     "UID")
            gid = self.getIdentifier(self.getValueFromDict(item, "group"),
                                     "GID")
        module = self.getValueFromDict(item, "module")
        source = self.getValueFromDict(item, "source", False)
        if source:
            create_symlink = self.getValueFromDict(item, "create_symlink",
                                                   False)
            if create_symlink:
                source = CopyTarget.normpath(
                    CopyTarget.expandvars(source, self.exports))
                print("[CREATE SYMLINK] '%s' -> '%s'" % (destination, source))
                perm = self.getValueFromDict(item, "perm", False)
                self.symlink(source, destination, uid, gid, perm)
                # Note: It is not possible to determine the size of symlink
                # created here at this stage. Assume that the size is 32 bytes.
                self.recordFileSize.addFile(None, destination, module, 32)
                self.digestMetadata.addDigestEntry(self, source, destination,
                                                   uid, gid, symlink=True)
            else:
                source = self.expandSourcePath(source)
                print("[CP] '%s' to '%s'" % (source, destination))
                perm = self.getValueFromDict(item, "perm")
                raw = self.getValueFromDict(item, "raw", False)
                self.copyfile(source, destination, uid, gid, perm, raw)
                self.recordFileSize.addFile(source, destination, module)
                self.digestMetadata.addDigestEntry(self, source, destination,
                                                   uid, gid, perm)
        elif remove:
            # User wants to remove files if "remove" key is set to true in CFG
            # (and "source" is not defined).
            # However, this is not allowed while creating a QNX buildFile
            print("[REMOVE] '%s'" % (destination))
            print("Error: \"remove\" directive is not allowed in CopyTarget "
                  "YAML manifest when creating QNX BuildFile", file=sys.stderr)
            sys.exit(1)
        else:
            # Assume that user wants to create a directory or change the
            # permission of an already existing file/directory if "source"
            # is not defined.
            print("[MKDIR] '%s'" % (destination))
            # Error out if the target directory does not have a trailing slash
            if destination[-1:] != "/":
                print("Error: Directory entries must end with a trailing "
                      "slash.", file=sys.stderr)
                sys.exit(1)
            perm = self.getValueFromDict(item, "perm")
            self.makedir(destination, uid, gid, perm)

    def writeBuildFile(self):
        fileAccessMode = "w"
        if len(self.outputHeaderFiles) == 0:
            fileAccessMode = "a"
        with open(self.outputBuildFile, fileAccessMode, encoding='utf-8') as f:
            for hf in map(os.path.expandvars, self.outputHeaderFiles):
                with open(hf, 'r', encoding='utf-8') as h:
                    f.write(h.read())
                f.write('\n')
            f.write(self.outputBuffer)


class VerificationError(Exception):
    pass


class FindRestrictedSourceFiles():

    def __init__(self, options, args):
        self.workspace = args[1]
        self.filesystemType = options.filesystemType
        self.violationFileList = {}
        for copytargetCFG in args[2:]:
            self.violationFileList[copytargetCFG] = {}
        self.blacklistYAMLManifestFile = None
        if options.blacklistYAMLManifestFile is not None:
            self.blacklistYAMLManifestFile = options.blacklistYAMLManifestFile
            self.blacklistManifestFileDict = OrderedDict()
            for copytargetCFG in args[2:]:
                self.violationFileList[copytargetCFG]["blacklist"] = []
        self.whitelistYAMLManifestFile = None
        if options.whitelistYAMLManifestFile is not None:
            self.whitelistYAMLManifestFile = options.whitelistYAMLManifestFile
            self.whitelistManifestFileList = []
            for copytargetCFG in args[2:]:
                self.violationFileList[copytargetCFG]["whitelist"] = []

        # Load YAML Manifest containing blacklisted files
        if options.blacklistYAMLManifestFile is not None:
            blacklistManifestDict = OrderedDict()
            with open(options.blacklistYAMLManifestFile,
                      "r", encoding='utf-8') as blacklistYAMLManifestHandle:
                blacklistManifestDict = yaml.load(
                    blacklistYAMLManifestHandle,
                    Loader=yaml.BaseLoader)
            for fileItem in blacklistManifestDict["fileList"]:
                file = CopyTarget.normpath(
                    self.workspace + CopyTarget.expandvars(fileItem["source"]))
                self.blacklistManifestFileDict[file] = OrderedDict()
                if "tag" in fileItem:
                    self.blacklistManifestFileDict[file]["tag"] = fileItem[
                        "tag"]
                if "reason" in fileItem:
                    self.blacklistManifestFileDict[file]["reason"] = fileItem[
                        "reason"]

        # Load YAML Manifest containing whitelisted files
        if options.whitelistYAMLManifestFile is not None:
            whitelistManifestDict = OrderedDict()
            with open(options.whitelistYAMLManifestFile,
                      "r", encoding='utf-8') as whitelistYAMLManifestHandle:
                whitelistManifestDict = yaml.load(
                    whitelistYAMLManifestHandle,
                    Loader=yaml.BaseLoader)
            for fileItem in whitelistManifestDict["fileList"]:
                file = CopyTarget.normpath(
                    self.workspace + CopyTarget.expandvars(fileItem["source"]))
                self.whitelistManifestFileList.append(file)

    def processFileItem(self, copytargetCFG, item, pos):
        source = CopyTarget.getValueFromDict(None, item, "source", False)
        if source:
            create_symlink = CopyTarget.getValueFromDict(None, item,
                                                         "create_symlink",
                                                         False)
            if create_symlink:
                return
            source = CopyTarget.normpath(
                self.workspace + CopyTarget.expandvars(source))
            if self.blacklistYAMLManifestFile is not None:
                if source in self.blacklistManifestFileDict:
                    self.violationFileList[copytargetCFG][
                        "blacklist"].append(item["source"])
            if self.whitelistYAMLManifestFile is not None:
                if source not in self.whitelistManifestFileList:
                    self.violationFileList[copytargetCFG][
                        "whitelist"].append(item["source"])

    def printResults(self, copytargetCFG):
        print("RESULT FOR VERIFICATION OF RESTRICTED FILE ITEMS:")
        verificationFailed = False
        if "blacklist" in self.violationFileList[copytargetCFG]:
            if len(self.violationFileList[copytargetCFG][
                    "blacklist"]) == 0:
                print("No blacklisted files found in '{}' "
                      "('{}' filesystem type)."
                      .format(copytargetCFG, self.filesystemType))
            else:
                verificationFailed = True
                print("Following blacklisted files found in '{}' "
                      "('{}' filesystem type)"
                      .format(copytargetCFG, self.filesystemType))
                for fileItem in self.violationFileList[
                        copytargetCFG]["blacklist"]:
                    file = CopyTarget.normpath(
                        self.workspace + os.path.expandvars(fileItem))
                    print(fileItem)
                    if "tag" in self.blacklistManifestFileDict[file]:
                        print("\tTag: {}".format(
                            self.blacklistManifestFileDict[file]["tag"]))
                    if "reason" in self.blacklistManifestFileDict[file]:
                        print("\tReason: {}".format(
                            self.
                            blacklistManifestFileDict[file]["reason"]))
            print("----")

        if "whitelist" in self.violationFileList[copytargetCFG]:
            if len(self.violationFileList[copytargetCFG][
                    "whitelist"]) == 0:
                print("No non-whitelisted files found in '{}' "
                      "('{}' filesystem type)."
                      .format(copytargetCFG, self.filesystemType))
            else:
                verificationFailed = True
                print("Following files in '{}', "
                      "('{}' filesystem type) and NOT whitelisted"
                      .format(copytargetCFG, self.filesystemType))
                print('\n'.join(map(str,
                                    self.violationFileList[copytargetCFG][
                                        "whitelist"])))
            print("----")

        if verificationFailed:
            raise VerificationError("Verification failed. "
                                    "Restricted files found in CopyTarget "
                                    "manifests")


class FileSizeRecords:

    def __init__(self, targetSizeFile, allOptions=None):
        self.targetSizeFile = targetSizeFile
        if self.targetSizeFile is None:
            return

        self.fileSizeDict = None
        self.fileRecordsDict = {}
        self.readTargetSizeManifest()

    def readTargetSizeManifest(self):
        try:
            with open(self.targetSizeFile, "r",
                      encoding='utf-8') as targetSizeFileHandle:
                self.fileSizeDict = self.orderedYAMLLoad(
                    targetSizeFileHandle, Loader=yaml.SafeLoader)
        except FileNotFoundError as e:
            pass

        if self.fileSizeDict is None:
            self.fileSizeDict = OrderedDict()
        if "totalFSSize" not in self.fileSizeDict:
            self.fileSizeDict["totalFSSize"] = 0
        if "totalFSSizeLimit" not in self.fileSizeDict:
            self.fileSizeDict["totalFSSizeLimit"] = "unknown"
        if "totalNumberOfFiles" not in self.fileSizeDict:
            self.fileSizeDict["totalNumberOfFiles"] = 0
        if "modules" not in self.fileSizeDict:
            self.fileSizeDict["modules"] = OrderedDict()

        for module in self.fileSizeDict["modules"]:
            if "files" in self.fileSizeDict["modules"][module]:
                for file in self.fileSizeDict["modules"][module]["files"]:
                    size = self.fileSizeDict[
                        "modules"][module]["files"][file]
                    self.fileRecordsDict[file] = {"module": module,
                                                  "size": size}

    def addFile(self, source, destination, module, size=None):
        if self.targetSizeFile is None:
            return
        module = module.lower()
        destination = CopyTarget.normpath(destination)
        if module not in self.fileSizeDict["modules"]:
            self.fileSizeDict["modules"][module] = OrderedDict()
            self.fileSizeDict["modules"][module]["moduleSize"] = 0
            self.fileSizeDict["modules"][module]["moduleSizeLimit"] = "unknown"
            self.fileSizeDict["modules"][module]["numberOfFiles"] = 0
            self.fileSizeDict["modules"][module]["files"] = OrderedDict()

        if size is None:
            if os.path.isdir(source) and (not os.path.islink(source)):
                print("Error: {} is a directory. addFile() function "
                      "in FileSizeRecords can only accept files."
                      .format(source), file=sys.stderr)
                sys.exit(1)
            size = os.lstat(source).st_size

        if destination in self.fileRecordsDict:
            # Handle the case where the same file is added again to the FS
            oldSize = self.fileRecordsDict[destination]["size"]
            oldModule = self.fileRecordsDict[destination]["module"]
            self.removeFile(destination, oldModule)

        self.fileSizeDict["totalFSSize"] += size
        self.fileSizeDict["modules"][module]["moduleSize"] += size

        self.fileSizeDict["totalNumberOfFiles"] += 1
        self.fileSizeDict["modules"][module]["numberOfFiles"] += 1

        self.fileSizeDict["modules"][module]["files"][destination] = size
        self.fileRecordsDict[destination] = {"module": module,
                                             "size": size}

    def removeFile(self, destination, module):
        if self.targetSizeFile is None:
            return
        module = module.lower()
        destination = CopyTarget.normpath(destination)
        try:
            size = self.fileSizeDict[
                "modules"][module]["files"].pop(destination)
            if size != self.fileRecordsDict.pop(destination)["size"]:
                print("\033[93mWARNING: Consistency issue detected while "
                      "verifying the size of '{}' file ('{}' module) "
                      "in '{}'\033[0m"
                      .format(destination, module, self.targetSizeFile))
            self.fileSizeDict["totalFSSize"] -= size
            self.fileSizeDict["modules"][module]["moduleSize"] -= size
            self.fileSizeDict["totalNumberOfFiles"] -= 1
            self.fileSizeDict["modules"][module]["numberOfFiles"] -= 1
        except KeyError as e:
            # Case where a user is trying to remove a file not  added using
            # CopyTarget
            pass

    def writeTargetSizeManifest(self):
        if self.targetSizeFile is None:
            return
        with open(self.targetSizeFile, "w",
                  encoding='utf-8') as targetSizeFileHandle:
            self.orderedYAMLDump(self.fileSizeDict,
                                 targetSizeFileHandle,
                                 default_flow_style=False, indent=4)

    def orderedYAMLDump(self, data, stream=None, Dumper=yaml.SafeDumper,
                        **kwds):

        class OrderedDumper(Dumper):
            pass

        OrderedDumper.add_representer(
            OrderedDict,
            lambda dumper, data:
                dumper.represent_mapping(
                    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                    data.items()))
        return yaml.dump(data, stream, OrderedDumper, **kwds)

    def orderedYAMLLoad(self, stream, Loader=yaml.SafeLoader):

        class OrderedLoader(Loader):
            pass

        def construct_mapping(loader, node):
            loader.flatten_mapping(node)
            return OrderedDict(loader.construct_pairs(node))

        OrderedLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            construct_mapping)
        return yaml.load(stream, OrderedLoader)


class CopyTargetDigestMetadata:
    SIZE = {
        "NAME_LENGTH": 1535,  # bytes (PATH_MAX + Max Filename length + 1)
        "MOUNTPATH_LENGTH": 1025,  # bytes (PATH_MAX + 1)
        "CHAR": 1,  # byte
        "NULL": 1,  # byte
        "VALUE": 4,  # bytes
        "PERM": 2,  # bytes
        "ARRAY_LENGTH": 4,  # bytes
        "DIGEST": 64  # bytes
    }

    def __init__(self, digestMetadataConfigJSON, mountPoint=None):
        self.enabled = False
        if digestMetadataConfigJSON is None:
            return
        self.digestMetadataConfig = None
        with open(digestMetadataConfigJSON, "r", encoding="utf-8") as f:
            self.digestMetadataConfig = json.load(f)
        self.enabled = self.digestMetadataConfig["enabled"]
        if self.enabled in DOMAIN["negative_values"]:
            return
        self.goldenDigestFile = CopyTarget.expandvars(
            self.digestMetadataConfig["goldenDigestFile"])
        self.metadataFileDirectory = CopyTarget.expandvars(
            self.digestMetadataConfig["metadataFileDirectory"])
        if not self.metadataFileDirectory.endswith("/"):
            print("Error: metadataFileDirectory: '%s' must be a directory "
                  "entry ending with a '/'"
                  % (self.metadataFileDirectory),
                  file=sys.stderr)
            sys.exit(1)
        self.authBlockSize = self.digestMetadataConfig["authBlockSize"]
        if mountPoint is None:
            print("Error: To compute digest and create metadata files, mount "
                  "point must be specified using the --mount-point argument.",
                  file=sys.stderr)
            sys.exit(1)
        self.mountPoint = mountPoint
        self.metadataFileListDict = OrderedDict()
        self._initialize()

    def dictToGoldenDigestFile(self, goldenDigestFile, dict):
        # ---------------------------------------------------------------------
        # Structure of golden digests binary:
        # =====================================================================
        #    Field               Size
        # =====================================================================
        #    magic               10 bytes
        #                        Magic has to be "COPYTARGET". CopyTarget is
        #                        set to discard metadata files without this
        #                        magic keyword.
        #    mount_path          1025 bytes
        #                        Stores the path where the filesystem is
        #                        mounted
        #    auth_block_size     4 bytes
        #                        Defines the size of file chunks used to
        #                        calculate the digest of files in QNX6 FS
        #    digest_array_length 4 bytes
        #                        Stores the number of items in the "digests"
        #                        array
        #    digests             (1535+64)*digest_array_length
        #                        An array that stores the name and digest
        #                        of all metadata file in QNX6 FS.
        # ---------------------------------------------------------------------

        # ---------------------------------------------------------------------
        # Structure of `digests` field in golden digests binary:
        # =====================================================================
        #    Field               Size
        # =====================================================================
        #    name                1535 bytes (PATH_MAX + Max Filename len + 1)
        #                        Name stores the absolute path of metadata
        #                        file in QNX6 FS
        #    digest              64 bytes
        #                        Stores the SHA512 SUM of metadata file
        # ---------------------------------------------------------------------

        # mount_path_length
        buffer = "COPYTARGET".encode("utf-8")
        # mount_path
        buffer += (self.mountPoint.encode("utf-8") + b'\x00').ljust(
            CopyTargetDigestMetadata.SIZE["MOUNTPATH_LENGTH"], b'\x00')
        # auth_block_size
        buffer += self.authBlockSize.to_bytes(
            CopyTargetDigestMetadata.SIZE["VALUE"], byteorder='big')
        # digest_array_length
        buffer += len(
            self.metadataFileListDict).to_bytes(
                CopyTargetDigestMetadata.SIZE["ARRAY_LENGTH"], byteorder='big')
        # digests
        for name, digest in self.metadataFileListDict.items():
            # name
            buffer += (name.encode("utf-8") + b'\x00').ljust(
                CopyTargetDigestMetadata.SIZE["NAME_LENGTH"], b'\x00')
            # digest
            buffer += int(digest, 16).to_bytes(
                CopyTargetDigestMetadata.SIZE["DIGEST"], byteorder='big')
        os.makedirs(os.path.dirname(goldenDigestFile), exist_ok=True)
        with open(goldenDigestFile, "wb") as f:
            f.write(buffer)

    def goldenDigestFileToDict(self, goldenDigestFile):
        dict = OrderedDict()
        if not os.path.exists(goldenDigestFile):
            return dict
        with open(goldenDigestFile, "rb") as f:
            magic = f.read(len("COPYTARGET")).decode("utf-8")
            if magic != "COPYTARGET":
                return dict
            mountPath = f.read(
                CopyTargetDigestMetadata.SIZE["MOUNTPATH_LENGTH"]).decode(
                    "utf-8").split("\x00")[0]
            if mountPath != self.mountPoint:
                CopyTarget.error("Mount point specified by --mount-point "
                                 "argument ('{}') and the mount path in "
                                 "the existing '{}' golden digest "
                                 "file ('{}') do not match"
                                 .format(self.mountPoint,
                                         self.goldenDigestFile, mountPath))
            authBlockSize = int.from_bytes(
                f.read(CopyTargetDigestMetadata.SIZE["VALUE"]),
                byteorder='big')
            if authBlockSize != self.authBlockSize:
                CopyTarget.error("Auth block size specified in the metadata "
                                 "JSON config ('{}') and the block size in "
                                 "the existing '{}' golden digest "
                                 "file ('{}') do not match"
                                 .format(self.authBlockSize,
                                         self.goldenDigestFile,
                                         authBlockSize))
            digest_array_length = int.from_bytes(
                f.read(CopyTargetDigestMetadata.SIZE["ARRAY_LENGTH"]),
                byteorder='big')
            for digest in range(digest_array_length):
                metadataFile = f.read(
                    CopyTargetDigestMetadata.SIZE["NAME_LENGTH"]).decode(
                        "utf-8").split("\x00")[0]
                metadataFileDigest = '{:x}'.format(
                    int.from_bytes(
                        f.read(CopyTargetDigestMetadata.SIZE["DIGEST"]),
                        byteorder="big"))
                dict[metadataFile] = metadataFileDigest
        return dict

    def _initialize(self):
        self.metadataFileListDict = self.goldenDigestFileToDict(
            self.goldenDigestFile)

    def addDigestEntry(self, copytargetContext, source, destination,
                       uid, gid, perm="777", symlink=False):
        if self.enabled in DOMAIN["negative_values"]:
            return
        metadataFileName = os.path.basename(destination) + ".metadata"
        metadataFileName = "." + metadataFileName
        metadataFilePathHost = CopyTarget.normpath(
            self.metadataFileDirectory + os.path.dirname(
                destination) + "/" + metadataFileName)
        metadataFilePathTarget = os.path.join(os.path.dirname(destination),
                                              metadataFileName)
        os.makedirs(self.metadataFileDirectory, exist_ok=True)

        # Handle cases where a symlink is being copied
        if not symlink:
            if os.path.islink(source):
                symlink = True
                perm = "777"
                source = os.readlink(source)

        # ---------------------------------------------------------------------
        # Structure of metadata binary:
        # =====================================================================
        #    Field               Size
        # =====================================================================
        #    uid                 4 bytes
        #                        Stores the UID of a file
        #    gid                 4 bytes
        #                        Stores the GID of the file
        #    perm                2 bytes
        #                        Stores the permission of the file in octal
        #    file_type           1 byte
        #                        Stores the type of file, it can take any of
        #                        the following values:
        #                         * Regular file: 0
        #                         * Symlink:      1
        #                        Other values may be used in the future.
        #    #if SYMLINK:
        #    target_name         1535 bytes (PATH_MAX + Filename length + 1)
        #                        Stores the path of symlink's target file
        #    #else:
        #    digest_array_length 4 bytes
        #                        Stores the number of items in "digest_array".
        #                        This is equal to the number of blocks in a
        #                        file
        #    digest_array        64 bytes * digest_array_length
        #                        Stores the SHA512 SUM of all blocks in a file
        #    #endif
        # ---------------------------------------------------------------------

        # uid
        buffer = uid.to_bytes(
            CopyTargetDigestMetadata.SIZE["VALUE"], byteorder='big')
        # gid
        buffer += gid.to_bytes(
            CopyTargetDigestMetadata.SIZE["VALUE"], byteorder='big')
        # perm
        buffer += int(perm, 8).to_bytes(
            CopyTargetDigestMetadata.SIZE["PERM"], byteorder='big')
        if symlink:
            # file_type (set 1 for symlink)
            buffer += (1).to_bytes(1, byteorder='big')
            # symlink target_name (source)
            buffer += (source.encode("utf-8") + b'\x00').ljust(
                CopyTargetDigestMetadata.SIZE["NAME_LENGTH"], b'\x00')
        else:
            fileBlockDigests = []
            self._getSHA_512(source, blockSize=self.authBlockSize,
                             digestArray=fileBlockDigests)
            # file_type (for now, set 0 for all other types of file)
            buffer += (0).to_bytes(1, byteorder='big')
            # digest_array_length
            buffer += len(fileBlockDigests).to_bytes(
                CopyTargetDigestMetadata.SIZE["ARRAY_LENGTH"], byteorder='big')
            # digest_array
            for digest in fileBlockDigests:
                buffer += int(digest, 16).to_bytes(
                    CopyTargetDigestMetadata.SIZE["DIGEST"], byteorder='big')

        os.makedirs(os.path.dirname(metadataFilePathHost), exist_ok=True)
        with open(metadataFilePathHost, 'wb') as f:
            f.write(buffer)

        copytargetContext.copyfile(os.path.abspath(metadataFilePathHost),
                                   metadataFilePathTarget, 0, 0, 444, False)
        copytargetContext.recordFileSize.addFile(metadataFilePathHost,
                                                 metadataFilePathTarget,
                                                 "digestMetadata")
        self.metadataFileListDict[
            CopyTarget.normpath(metadataFilePathTarget)] = self._getSHA_512(
                metadataFilePathHost)

    def _getSHA_512(self, path, blockSize=8192, digestArray=None):
        hash = hashlib.sha512()
        if digestArray is not None:
            digestArray.clear()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(blockSize), b''):
                hash.update(chunk)
                if digestArray is not None:
                    digestArray.append(hashlib.sha512(chunk).hexdigest())
        return hash.hexdigest()

    def writeGoldenDigestFile(self):
        if self.enabled in DOMAIN["negative_values"]:
            return
        self.dictToGoldenDigestFile(self.goldenDigestFile,
                                    self.metadataFileListDict)


class CopyTargetSpreadsheet:

    def __init__(self, spreadsheetFile, spreadsheetMeta=None,
                 metaSubstitutions=None):
        self.spreadsheetFile = spreadsheetFile.split(":")[0]
        self.spreadsheetMeta = spreadsheetMeta
        self.worksheetName = "Default"
        if len(spreadsheetFile.split(":")) > 1:
            self.worksheetName = spreadsheetFile.split(":")[1]
            self.worksheetName = ".." + self.worksheetName[-29:] \
                if len(self.worksheetName) > 31 else self.worksheetName
        self.workbook = None
        self.worksheet = None
        self.table = None
        self.ns = {}
        self.fileListDict = OrderedDict()
        self.spreadsheetMetaDict = None
        self.metaSubstitutions = metaSubstitutions
        self.initialize()

    class Cell:

        def __init__(self, data, mergeAcross=0):
            self.data = data
            if self.data is None:
                self.data = ""
            self.mergeAcross = mergeAcross
            if self.mergeAcross is None:
                self.mergeAcross = 0

    class Row:

        def __init__(self, *cells, isHeader=False):
            self.cells = []
            for cell in cells:
                self.cells.append(cell)
            self.isHeader = isHeader

        def appendCell(self, cell):
            self.cells.append(cell)

    class Utils:

        @staticmethod
        def addFlattenedEntryToDict(key, value, dictionary, separator=":"):
            if len(key.split(separator)) == 1 or key.split(separator)[1] == "":
                dictionary[key.split(separator)[0]] = value
                return
            if key.split(separator)[0] not in dictionary:
                dictionary[key.split(separator)[0]] = OrderedDict()
            CopyTargetSpreadsheet.Utils.addFlattenedEntryToDict(
                separator.join(key.split(separator)[1:]),
                value, dictionary[key.split(separator)[0]], separator)

        @staticmethod
        def flattenDict(dictionary, parentKey="", seperator=":"):
            items = []
            for key, value in dictionary.items():
                if parentKey:
                    newKey = parentKey + seperator + key
                else:
                    newKey = key
                if isinstance(value, dict):
                    items.extend(
                        CopyTargetSpreadsheet.Utils.flattenDict(
                            value, newKey, seperator).items())
                else:
                    items.append((newKey, value))
            return OrderedDict(items)

        @staticmethod
        def insertItemToList(index, item, list, default=""):
            for i in range(index + 1):
                if i >= len(list):
                    list.append(default)
                if i == index:
                    list[index] = item

    def initialize(self):
        # NoOp if a path to a spreadsheet is not provided by user
        if self.spreadsheetFile is None:
            return None

        if self.spreadsheetMeta is not None:
            with open(self.spreadsheetMeta, "r",
                      encoding='utf-8') as spreadsheetMetaHandle:
                self.spreadsheetMetaDict = CopyTarget.orderedYAMLLoad(
                    spreadsheetMetaHandle, Loader=yaml.BaseLoader)
            if "version" in self.spreadsheetMetaDict:
                if self.spreadsheetMetaDict[
                        "version"] not in COMPATIBLE_MANIFEST_VERSIONS:
                    print("Error: %s version %s is incompatible with %s "
                          "version %s."
                          % (self.spreadsheetMeta,
                             self.spreadsheetMetaDict["version"],
                             sys.argv[0], VERSION),
                          file=sys.stderr)
                    sys.exit(1)
            if "metadata" not in self.spreadsheetMetaDict:
                print("Error: Metadata is not defined in '{}'"
                      .format(self.spreadsheetMeta), file=sys.stderr)
                sys.exit(1)

        self.fileListDict = OrderedDict()

        # Read data already present in the worksheet (if available)
        self.createWorkbook()
        worksheet = self.readWorksheet()
        if worksheet is None:
            self.addWorksheet()
            return

        # Flatten Headers in the worksheet
        headers = []
        rowNo = 0
        for row in worksheet:
            if not row.isHeader:
                continue
            colNo = 0
            header = []
            for cell in row.cells:
                data = cell.data
                cellSpan = int(cell.mergeAcross) + 1
                while cellSpan > 0:
                    header.insert(colNo, data)
                    cellSpan -= 1
                    colNo += 1
            headers.insert(rowNo, header)
            rowNo += 1
        flattenedHeader = []
        for row in headers:
            colNo = 0
            for data in row:
                if data != "":
                    if colNo < len(flattenedHeader):
                        flattenedHeader[colNo] = flattenedHeader[
                            colNo] + data + ":"
                    else:
                        flattenedHeader.insert(colNo, data + ":")
                colNo += 1

        # Obtain original value of header from the medatada file
        if self.spreadsheetMeta is not None:
            if "rename" in self.spreadsheetMetaDict["metadata"]:
                for key, renamedValue in self.spreadsheetMetaDict[
                        "metadata"]["rename"].items():
                    renamedValue += ":"
                    key += ":"
                    if renamedValue in flattenedHeader:
                        flattenedHeader[
                            flattenedHeader.index(renamedValue)] = key

        # Add data from the worksheet to fileListDict
        for row in worksheet:
            if row.isHeader:
                continue
            if len(row.cells) == 0:
                continue
            colNo = 0
            destination = row.cells[0].data
            if destination not in self.fileListDict:
                self.fileListDict[destination] = OrderedDict()
            for cell in row.cells:
                CopyTargetSpreadsheet.Utils.addFlattenedEntryToDict(
                    flattenedHeader[colNo], cell.data,
                    self.fileListDict[destination])
                colNo += 1

    def addFileEntry(self, key, fileEntry):
        if self.spreadsheetFile is None:
            return None
        if key not in self.fileListDict:
            self.fileListDict[key] = OrderedDict()
        self.fileListDict[key] = CopyTarget.mergeDict(self.fileListDict[key],
                                                      fileEntry)

    def createSpreadsheet(self):
        if self.spreadsheetFile is None:
            return None
        self.populateWorksheet()
        self.writeSpreadsheet()

    def populateWorksheet(self):
        # Worksheet has to be emptied before new data is added to it
        self.emptyWorksheet()

        # Flatten fileListDict and add keys to flattenedHeaders[]
        flattenedHeaders = []
        for destination in self.fileListDict:
            for key, value in CopyTargetSpreadsheet.Utils.flattenDict(
                    self.fileListDict[destination]).items():
                if key not in flattenedHeaders:
                    flattenedHeaders.append(key)

        # Determine the ordering of columns based on meta YAML
        spreadsheetMetaDict = self.spreadsheetMetaDict
        if self.spreadsheetMeta is not None:
            # Print error if any blacklisted entry is present in the Meta File
            if "blacklist" in spreadsheetMetaDict["metadata"]:
                for reEntry in spreadsheetMetaDict["metadata"]["blacklist"]:
                    for key in self.metaSubstitutions:
                        reEntry = re.sub(r"(?<!\\){{{0}}}".format(key),
                                         self.metaSubstitutions[key], reEntry)
                    matches = list(filter(
                        re.compile("^" + reEntry + "$").match,
                        flattenedHeaders))
                    for item in matches:
                        if item in flattenedHeaders:
                            flattenedHeaders.remove(item)

            filteredFlattenedHeaders = []
            if "whitelist" in spreadsheetMetaDict["metadata"]:
                if len(spreadsheetMetaDict["metadata"]["whitelist"]) > 1 and \
                        spreadsheetMetaDict[
                            "metadata"]["whitelist"][0] != "destination":
                    print("Error: The first entry of whitelist metadata "
                          "in '{}' has to be 'destination'"
                          .format(self.spreadsheetMeta), file=sys.stderr)
                    sys.exit(1)
                for reEntry in spreadsheetMetaDict["metadata"]["whitelist"]:
                    for key in self.metaSubstitutions:
                        reEntry = re.sub(r"(?<!\\){{{0}}}".format(key),
                                         self.metaSubstitutions[key], reEntry)
                    matches = list(filter(
                        re.compile("^" + reEntry + "$").match,
                        flattenedHeaders))
                    for item in matches:
                        if item not in filteredFlattenedHeaders:
                            filteredFlattenedHeaders.append(item)
            flattenedHeaders = filteredFlattenedHeaders

            # Read all overrides and expand any variables used in that
            if "overrides" in spreadsheetMetaDict["metadata"]:
                for keyEntry in spreadsheetMetaDict["metadata"]["overrides"]:
                    for key in self.metaSubstitutions:
                        spreadsheetMetaDict[
                            "metadata"]["overrides"][keyEntry] = re.sub(
                                r"(?<!\\){{{0}}}".format(key),
                                self.metaSubstitutions[key],
                                spreadsheetMetaDict[
                                    "metadata"]["overrides"][keyEntry])

            if "defaults" in spreadsheetMetaDict["metadata"]:
                for keyEntry, item in spreadsheetMetaDict["metadata"][
                        "defaults"].copy().items():
                    for key in self.metaSubstitutions:
                        keyEntry = re.sub(r"(?<!\\){{{0}}}".format(key),
                                          self.metaSubstitutions[key],
                                          keyEntry)
                        item = re.sub(r"(?<!\\){{{0}}}".format(key),
                                      self.metaSubstitutions[key],
                                      item)
                    spreadsheetMetaDict[
                        "metadata"]["defaults"][keyEntry] = item

        # Flatten fileListDict and add values to rows[]
        rows = []
        maxRowLength = 0
        for destination in self.fileListDict:
            dataList = []
            flattenedDict = CopyTargetSpreadsheet.Utils.flattenDict(
                self.fileListDict[destination])
            for key, value in flattenedDict.items():
                if spreadsheetMetaDict is not None and \
                        "overrides" in spreadsheetMetaDict["metadata"]:
                    if key in spreadsheetMetaDict["metadata"]["overrides"]:
                        if spreadsheetMetaDict[
                                "metadata"]["overrides"][key] in flattenedDict:
                            value = flattenedDict[
                                spreadsheetMetaDict[
                                    "metadata"]["overrides"][key]]
                if key in flattenedHeaders:
                    CopyTargetSpreadsheet.Utils.insertItemToList(
                        flattenedHeaders.index(key),
                        self.Cell(value), dataList, default=self.Cell(""))
            if spreadsheetMetaDict is not None and \
                    "defaults" in spreadsheetMetaDict["metadata"]:
                for key, default in spreadsheetMetaDict[
                        "metadata"]["defaults"].items():
                    try:
                        if key in flattenedHeaders and \
                                not dataList[flattenedHeaders.index(key)].data:
                            CopyTargetSpreadsheet.Utils.insertItemToList(
                                flattenedHeaders.index(key),
                                self.Cell(default), dataList,
                                default=self.Cell(""))
                    except IndexError as e:
                        if key in flattenedHeaders:
                            CopyTargetSpreadsheet.Utils.insertItemToList(
                                flattenedHeaders.index(key),
                                self.Cell(default), dataList,
                                default=self.Cell(""))
            rows.append(dataList)
            if len(dataList) > maxRowLength:
                maxRowLength = len(dataList)

        # Rename the headers
        if self.spreadsheetMeta is not None:
            if "rename" in spreadsheetMetaDict["metadata"]:
                i = 0
                for i in range(len(flattenedHeaders)):
                    if flattenedHeaders[i] in spreadsheetMetaDict[
                            "metadata"]["rename"]:
                        flattenedHeaders[i] = spreadsheetMetaDict[
                            "metadata"]["rename"][flattenedHeaders[i]]

        # Extract headers from flattenedHeaders
        headers = []
        while True:
            colNo = 0
            header = []
            allEmpty = True
            for data in flattenedHeaders:
                if data.split(":")[0] != "":
                    allEmpty = False
                    CopyTargetSpreadsheet.Utils.insertItemToList(
                        colNo, data.split(":")[0], header)
                    flattenedHeaders[colNo] = ":".join(data.split(":")[1:])
                colNo += 1
            if allEmpty:
                break
            headers.append(header)

        # Align the width of headers
        for rowNo, header in enumerate(headers):
            previousRowNo = rowNo - 1 if rowNo > 0 else 0
            lenDiff = len(headers[previousRowNo]) - len(headers[rowNo])
            while lenDiff > 0:
                header.append("")
                lenDiff -= 1

        # Populate the headers in the worksheet
        for headerRow, header in enumerate(headers):
            length = len(header)
            outputHeader = []
            for index, cell in enumerate(header):
                nextIndex = index + 1
                mergeAcross = 0
                while nextIndex < length:
                    if header[index] == "":
                        pass
                    elif header[index] == header[nextIndex]:
                        mergeAcross += 1
                        header[nextIndex] = None
                    else:
                        break
                    nextIndex += 1
                if header[index] is not None:
                    outputHeader.append(self.Cell(cell, mergeAcross))
            self.addRow(outputHeader, isHeader=True)

        # Freeze the header rows
        self.worksheet.find("WorksheetOptions").find(
            "SplitHorizontal").text = str(len(headers))
        self.worksheet.find("WorksheetOptions").find(
            "TopRowBottomPane").text = str(len(headers))

        # Align the width of data rows
        for row in rows:
            lenDiff = maxRowLength - len(row)
            while lenDiff > 0:
                row.append(self.Cell(""))
                lenDiff -= 1

        # Populate the data to the worksheet
        for row in rows:
            self.addRow(row)

    def createWorkbook(self):
        initialXML = '''<?xml version="1.0"?>
        <?mso-application progid="Excel.Sheet"?>
        <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
         xmlns:o="urn:schemas-microsoft-com:office:office"
         xmlns:x="urn:schemas-microsoft-com:office:excel"
         xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"
         xmlns:html="http://www.w3.org/TR/REC-html40">
         <DocumentProperties xmlns="urn:schemas-microsoft-com:office:office">
          <Version>16.00</Version>
          <AppName>CopyTarget</AppName>
         </DocumentProperties>
         <OfficeDocumentSettings
          xmlns="urn:schemas-microsoft-com:office:office">
          <AllowPNG/>
         </OfficeDocumentSettings>
         <Styles>
          <Style ss:ID="Default" ss:Name="Normal">
           <Alignment ss:Vertical="Bottom"/>
           <Borders/>
           <Font ss:FontName="Calibri"
            x:Family="Swiss" ss:Size="11" ss:Color="#000000"/>
           <Interior/>
           <NumberFormat/>
           <Protection/>
          </Style>
          <Style ss:ID="text">
           <Alignment ss:WrapText="1"/>
           <Borders>
            <Border
             ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>
            <Border
             ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>
            <Border
             ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>
            <Border
             ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/>
           </Borders>
          </Style>
          <Style ss:ID="header">
           <Alignment ss:Horizontal="Center" ss:Vertical="Top"/>
           <Borders>
            <Border
             ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>
            <Border
             ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>
            <Border
             ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>
            <Border
             ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/>
           </Borders>
           <Font ss:FontName="Calibri"
            x:Family="Swiss" ss:Size="11" ss:Color="#000000" ss:Bold="1"/>
           <Interior ss:Color="#A9D08E" ss:Pattern="Solid"/>
          </Style>
         </Styles>
        </Workbook>
        '''
        namespaces = dict([node for _, node in ET.iterparse(
            BytesIO(initialXML.encode("UTF-8")), events=['start-ns'])])
        for ns in namespaces:
            ET.register_namespace(ns, namespaces[ns])
            self.ns[ns] = "{" + namespaces[ns] + "}"
        if os.path.exists(self.spreadsheetFile):
            self.workbook = ET.parse(self.spreadsheetFile).getroot()
            appName = None
            property = self.workbook.find(self.ns["o"] + "DocumentProperties")
            if property:
                appName = property.find(self.ns["o"] + "AppName")
            else:
                print("Error: Unable to find DocumentProperties",
                      file=sys.stderr)
            if appName is None or appName.text != "CopyTarget":
                print("Error: '{}' is incompatible with CopyTarget. "
                      "You can only use a Spreadsheet created by CopyTarget "
                      "OR "
                      "you may provide a path to a Spreadsheet file that "
                      "does not already exist."
                      .format(self.spreadsheetFile), file=sys.stderr)
                sys.exit(1)
        else:
            self.workbook = ET.fromstring(initialXML)

    def addWorksheet(self):
        worksheetName = self.worksheetName
        self.worksheet = ET.SubElement(self.workbook,
                                       self.ns["ss"] + "Worksheet")
        self.worksheet.set(self.ns["ss"] + "Name", worksheetName)

        # Add WorksheetOptions to freeze header
        worksheetOptions = ET.SubElement(self.worksheet, "WorksheetOptions")
        worksheetOptions.set("xmlns", "urn:schemas-microsoft-com:office:excel")
        ET.SubElement(worksheetOptions, "Selected")
        ET.SubElement(worksheetOptions, "FreezePanes")
        ET.SubElement(worksheetOptions, "FrozenNoSplit")
        ET.SubElement(worksheetOptions, "SplitHorizontal").text = "0"
        ET.SubElement(worksheetOptions, "TopRowBottomPane").text = "0"
        ET.SubElement(worksheetOptions, "ActivePane").text = "2"

        self.table = ET.SubElement(self.worksheet,
                                   self.ns["ss"] + "Table")

    def adjustColumnWidth(self, colNo, width, force=False, maxWidth=400):
        i = 0
        for column in self.table.findall(self.ns["ss"] + "Column"):
            if colNo == i:
                currentWidth = column.get(self.ns["ss"] + "Width")
                if maxWidth is not None and width > maxWidth:
                    width = maxWidth
                if float(currentWidth) < width or force is True:
                    column.set(self.ns["ss"] + "Width", str(width))
            i += 1

    def addRow(self, cells, isHeader=False):
        styleID = "text"
        if isHeader:
            styleID = "header"
        if not self.table.findall(self.ns["ss"] + "Column"):
            for cell in cells:
                mergeAcross = cell.mergeAcross
                while mergeAcross >= 0:
                    column = ET.SubElement(self.table,
                                           self.ns["ss"] + "Column")
                    column.set(self.ns["ss"] + "Width", "40")
                    mergeAcross = mergeAcross - 1
        row = ET.SubElement(self.table, self.ns["ss"] + "Row")
        colNo = 0
        for cell in cells:
            mergeAcross = cell.mergeAcross
            cellElement = ET.SubElement(row, self.ns["ss"] + "Cell")
            cellElement.set(self.ns["ss"] + "MergeAcross", str(mergeAcross))
            cellElement.set(self.ns["ss"] + "StyleID", styleID)
            data = ET.SubElement(cellElement, self.ns["ss"] + "Data")
            data.set(self.ns["ss"] + "Type", "String")
            data.text = cell.data
            self.adjustColumnWidth(colNo, len(cell.data) * 6)
            colNo = colNo + 1 + int(mergeAcross)

    def deleteWorksheet(self):
        worksheetName = self.worksheetName
        for worksheet in self.workbook.findall(self.ns["ss"] + 'Worksheet'):
            if worksheet.get(self.ns["ss"] + "Name") == worksheetName:
                break
        if worksheet.get(self.ns["ss"] + "Name") != worksheetName:
            return
        self.workbook.remove(worksheet)
        self.worksheet = None
        self.table = None

    def emptyWorksheet(self):
        self.deleteWorksheet()
        self.addWorksheet()

    def readWorksheet(self):
        worksheetName = self.worksheetName
        worksheetData = []
        if len(self.workbook.findall(self.ns["ss"] + 'Worksheet')) == 0:
            return None
        for worksheet in self.workbook.findall(self.ns["ss"] + 'Worksheet'):
            if worksheet.get(self.ns["ss"] + "Name") == worksheetName:
                break
        if worksheet.get(self.ns["ss"] + "Name") != worksheetName:
            return None
        table = worksheet.find(self.ns["ss"] + "Table")
        rowNumber = 0
        for row in table.findall(self.ns["ss"] + "Row"):
            worksheetData.insert(rowNumber, self.Row())
            for cell in row.findall(self.ns["ss"] + "Cell"):
                if cell.get(self.ns["ss"] + "StyleID") == "header":
                    worksheetData[rowNumber].isHeader = True
                mergeAcross = cell.get(self.ns["ss"] + "MergeAcross")
                data = cell.find(self.ns["ss"] + "Data").text
                worksheetData[rowNumber].appendCell(self.Cell(data,
                                                              mergeAcross))
            rowNumber = rowNumber + 1
        return worksheetData

    def writeSpreadsheet(self):
        output = self.spreadsheetFile
        xmlHeader = '''<?xml version="1.0"?>
        <?mso-application progid="Excel.Sheet"?>
        '''
        with open(output, "wb") as f:
            f.write(bytes(xmlHeader, 'utf-8') + ET.tostring(self.workbook))


def main():
    parser = OptionParser(
        usage="usage: ./%prog TARGET_DIRECTORY WORKSPACE MANIFEST.yaml "
        "[MANIFEST1.yaml...] [OPTIONS]",
        version="CopyTarget %s" % VERSION)
    parser.add_option("-u", "--user-identifier-dictionary", dest="uidMapFile",
                      default=None,
                      help="Explicitly specify the path to the passswd file. "
                      "This is useful if the target root path does not "
                      "have ${TARGET_DIRECTORY}/etc/passwd file")
    parser.add_option("-g", "--group-identifier-dictionary", dest="gidMapFile",
                      default=None,
                      help="Explicitly specify the path to the group file. "
                      "This is useful if the target root path does not "
                      "have ${TARGET_DIRECTORY}/etc/group file")
    parser.add_option("-s", "--source-type", dest="sourceType",
                      default=None,
                      help="Specify source type. "
                      "Default value: \"{}\"".format(DEFAULT["sourceType"]))
    parser.add_option("-f", "--filesystem-type", dest="filesystemType",
                      default=DEFAULT["filesystemType"],
                      help="Specify filesystem type. Only process items in "
                      "fileList that have required set to yes under "
                      "filesystems entry in manifest. "
                      "Default value: \"{}\"".format(DEFAULT["filesystemType"]
                                                     ))
    parser.add_option("--autocreate-parent-directories",
                      dest="autocreateParentDir",
                      default=(DEFAULT["autocreateParentDir"]),
                      help="Automatically create parent directories even if "
                      "they have not been explicitly specified in the "
                      "manifest. Accepted values: 'True/yes', 'False/no'. "
                      "Default: '{}'".format(DEFAULT["autocreateParentDir"]))
    parser.add_option("--no-chown", dest="noChown",
                      default=(not DEFAULT["doChown"]), action="store_true",
                      help="Do not change ownership of the files")
    parser.add_option("--create-buildfile", dest="QNXBuildFile", default=None,
                      help="Convert YAML copytarget manifests to QNX "
                      "BuildFile")
    parser.add_option("--buildfile-header-file", dest="QNXBFHeaderFiles",
                      action="append", default=[],
                      help="Source file to include when generating build"
                      "files. Can be specified multiple times\n")
    parser.add_option("--blacklist", dest="blacklistYAMLManifestFile",
                      default=None,
                      help="Specify the path to a YAML manifest with a list "
                      "of source path of files that should not be copied to "
                      "TARGET_DIRECTORY. This is an operation to verify the "
                      "CopyTarget YAML Manifest")
    parser.add_option("--whitelist", dest="whitelistYAMLManifestFile",
                      default=None,
                      help="Specify the path to a YAML manifest with a list "
                      "of source path of files that can be copied to "
                      "TARGET_DIRECTORY. This is an operation to verify the "
                      "CopyTarget YAML Manifest")
    parser.add_option("--verify-only", dest="verifyYAMLOnly",
                      default=False, action="store_true",
                      help="Only verify the CopyTarget YAML manifest. By "
                      "specifying this option CopyTarget will only verify "
                      "the CopyTarget manifest. "
                      "At lease one of the following arguments are necessary "
                      "to specify what needs to be verified: \n"
                      "1) Check for blacklisted files with --blacklist.\n"
                      "2) Check for whitelisted files with --whitelist.\n")
    parser.add_option("--target-size-file", dest="targetSizeFile",
                      default=None,
                      help="Specify the path to a manifest that CopyTarget "
                      "populates with the size of files copied to the target"
                      "If this file is initially empty/non-existant, "
                      "CopyTarget will create and populate this file."
                      "If it already has data, it will add the size "
                      "information to this file.")
    parser.add_option("--create-spreadsheet", dest="spreadsheetFile",
                      default=None,
                      help="Specify the path of the spreadsheet."
                      "This option can be used to create an Excel Spreadsheet"
                      "containing the items listed in the CopyTarget YAML."
                      "Note: This spreadsheet will be in XML format."
                      "You may specify the name of worksheet here by "
                      "seperating it by a colon"
                      "(<path_to_spreadsheet>:<worksheet_name>). "
                      "By default, the worksheet name is 'Default'.")
    parser.add_option("--spreadsheet-metadata", dest="spreadsheetMeta",
                      default=None,
                      help="Specify the path of the spreadsheet metadata file."
                      "This metadata file controls what attributes from the "
                      "CopyTarget YAML manifest appear in the spreadsheet")
    parser.add_option("--mount-point", dest="mountPoint",
                      default=None,
                      help="Specify mount point of the resultant image."
                      "This option may be used to remove the mount point "
                      "from the destination field in the CopyTarget Manifest."
                      "This ensures that the resutant path of files on target"
                      "is same as what is specified on the manifest")
    parser.add_option("--digest-metadata-config", dest="digestMetadataJSON",
                      default=None,
                      help="Specify path to a file (in JSON format) with "
                      "parameters related to creating metadata files with "
                      "digests for files in the GS. "
                      "This may be used for verifying the integrity of files "
                      "in QNX FS. "
                      "Valid JSON arguments include: "
                      "'enabled:' True/False, "
                      "'goldenDigestFile': <path to output golden hash files>,"
                      "'authBlockSize': <block size in  bytes>,"
                      "'metadataFileDirectory': <Directory path of "
                      "autogenerated metadata files>")

    (options, args) = parser.parse_args()
    if len(args) < 3:
        parser.error("incorrect number of arguments")
    if not options.uidMapFile:
        options.uidMapFile = args[0] + UIDMAPFILE
    if not options.gidMapFile:
        options.gidMapFile = args[0] + GIDMAPFILE
    os.environ["TARGET_DIRECTORY"] = args[0]
    os.environ["WORKSPACE"] = args[1]
    if options.QNXBuildFile is None:
        if len(options.QNXBFHeaderFiles) != 0:
            parser.error("\"--buildfile-header-file\" can only be used with "
                         "\"--create-buildfile\"")
        linuxTargetFS = LinuxCopyTarget(args[0], args[1],
                                        options.uidMapFile, options.gidMapFile,
                                        options.sourceType,
                                        options.filesystemType,
                                        options, args)
        for copytargetCFG in args[2:]:
            linuxTargetFS.processCFG(copytargetCFG)
        linuxTargetFS.recordFileSize.writeTargetSizeManifest()
    else:
        QNXBuildFile = QNXCopyTarget(args[0], args[1],
                                     options.uidMapFile, options.gidMapFile,
                                     options.sourceType,
                                     options.filesystemType,
                                     options, args)
        for copytargetCFG in args[2:]:
            QNXBuildFile.processCFG(copytargetCFG)
        QNXBuildFile.recordFileSize.writeTargetSizeManifest()
        QNXBuildFile.writeBuildFile()


if __name__ == '__main__':
    main()
