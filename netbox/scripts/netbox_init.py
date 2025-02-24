#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2024 Battelle Energy Alliance, LLC.  All rights reserved.

import argparse
import glob
import gzip
import ipaddress
import itertools
import json
import logging
import magic
import os
import psycopg
import pynetbox
import randomcolor
import re
import shutil
import sys
import tarfile
import tempfile
import time
import malcolm_utils

from collections.abc import Iterable
from distutils.dir_util import copy_tree
from datetime import datetime
from slugify import slugify

###################################################################################################
args = None
script_name = os.path.basename(__file__)
script_path = os.path.dirname(os.path.realpath(__file__))
orig_path = os.getcwd()


###################################################################################################
def get_iterable(x):
    if isinstance(x, Iterable) and not isinstance(x, str):
        return x
    else:
        return (x,)


def is_ip_address(x):
    try:
        ipaddress.ip_address(x)
        return True
    except Exception:
        return False


def is_ip_v4_address(x):
    try:
        ipaddress.IPv4Address(x)
        return True
    except Exception:
        return False


def is_ip_v6_address(x):
    try:
        ipaddress.IPv6Address(x)
        return True
    except Exception:
        return False


def is_ip_network(x):
    try:
        ipaddress.ip_network(x)
        return True
    except Exception:
        return False


def min_hash_value_by_value(x):
    return next(
        iter(list({k: v for k, v in sorted(x.items(), key=lambda item: item[1])}.values())),
        None,
    )


def min_hash_value_by_key(x):
    return next(
        iter(list({k: v for k, v in sorted(x.items(), key=lambda item: item[0])}.values())),
        None,
    )


def max_hash_value_by_value(x):
    try:
        *_, last = iter(list({k: v for k, v in sorted(x.items(), key=lambda item: item[1])}.values()))
    except Exception:
        last = None
    return last


def max_hash_value_by_key(x):
    try:
        *_, last = iter(list({k: v for k, v in sorted(x.items(), key=lambda item: item[0])}.values()))
    except Exception:
        last = None
    return last


###################################################################################################
# main
def main():
    global args

    parser = argparse.ArgumentParser(
        description='\n'.join([]),
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False,
        usage='{} <arguments>'.format(script_name),
    )
    parser.add_argument(
        '--verbose',
        '-v',
        action='count',
        default=1,
        help='Increase verbosity (e.g., -v, -vv, etc.)',
    )
    parser.add_argument(
        '--wait',
        dest='wait',
        action='store_true',
        help='Wait for connection first',
    )
    parser.add_argument(
        '--no-wait',
        dest='wait',
        action='store_false',
        help='Do not wait for connection (error if connection fails)',
    )
    parser.set_defaults(wait=True)
    parser.add_argument(
        '-u',
        '--url',
        dest='netboxUrl',
        type=str,
        default='http://localhost:8080/netbox',
        required=False,
        help="NetBox Base URL",
    )
    parser.add_argument(
        '-t',
        '--token',
        dest='netboxToken',
        type=str,
        default=None,
        required=False,
        help="NetBox API Token",
    )
    parser.add_argument(
        '-s',
        '--site',
        dest='netboxSites',
        nargs='*',
        type=str,
        default=[os.getenv('NETBOX_DEFAULT_SITE', 'default')],
        required=False,
        help="Site(s) to create",
    )
    parser.add_argument(
        '--net-map',
        dest='netMapFileName',
        type=str,
        default=None,
        required=False,
        help="Filename of JSON file containing network subnet/host name mapping",
    )
    parser.add_argument(
        '--default-group',
        dest='defaultGroupName',
        type=str,
        default=os.getenv('REMOTE_AUTH_DEFAULT_GROUPS', 'standard'),
        required=False,
        help="Name of default group for automatic NetBox user creation",
    )
    parser.add_argument(
        '--staff-group',
        dest='staffGroupName',
        type=str,
        default=os.getenv('REMOTE_AUTH_STAFF_GROUPS', 'administrator'),
        required=False,
        help="Name of staff group for automatic NetBox user creation",
    )
    parser.add_argument(
        '-m',
        '--manufacturer',
        dest='manufacturers',
        nargs='*',
        type=str,
        default=[os.getenv('NETBOX_DEFAULT_MANUFACTURER', 'Unspecified')],
        required=False,
        help="Manufacturers to create",
    )
    parser.add_argument(
        '-r',
        '--role',
        dest='roles',
        nargs='*',
        type=str,
        default=[os.getenv('NETBOX_DEFAULT_ROLE', 'Unspecified')],
        required=False,
        help="Role(s) to create",
    )
    parser.add_argument(
        '-y',
        '--device-type',
        dest='deviceTypes',
        nargs='*',
        type=str,
        default=[os.getenv('NETBOX_DEFAULT_DEVICE_TYPE', 'Unspecified')],
        required=False,
        help="Device types(s) to create",
    )
    parser.add_argument(
        '-n',
        '--netbox',
        dest='netboxDir',
        type=str,
        default=os.getenv('NETBOX_PATH', '/opt/netbox'),
        required=False,
        help="NetBox installation directory",
    )
    parser.add_argument(
        '-l',
        '--library',
        dest='libraryDir',
        type=str,
        default=os.getenv('NETBOX_DEVICETYPE_LIBRARY_IMPORT_PATH', '/opt/netbox-devicetype-library-import'),
        required=False,
        help="Directory containing NetBox Device-Type-Library-Import project and library repo",
    )
    parser.add_argument(
        '-p',
        '--preload',
        dest='preloadDir',
        type=str,
        default=os.getenv('NETBOX_PRELOAD_PATH', '/opt/netbox-preload'),
        required=False,
        help="Directory containing netbox-initializers files to preload",
    )
    parser.add_argument(
        '--preload-prefixes',
        dest='preloadPrefixes',
        type=malcolm_utils.str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=malcolm_utils.str2bool(os.getenv('NETBOX_PRELOAD_PREFIXES', default='False')),
        help="Preload IPAM IP Prefixes for private IP space",
    )
    parser.add_argument(
        '--preload-backup',
        dest='preloadBackupFile',
        type=str,
        default=os.getenv('NETBOX_PRELOAD_GZ', default=''),
        required=False,
        help="Database dump .gz file to preload into postgreSQL",
    )
    parser.add_argument(
        '--postgres-host',
        dest='postgresHost',
        type=str,
        default=os.getenv('DB_HOST', 'netbox-postgres'),
        required=False,
        help="postgreSQL host for preloading an entire database dump .gz (specified with --preload-backup or loaded from the --preload directory)",
    )
    parser.add_argument(
        '--postgres-db',
        dest='postgresDB',
        type=str,
        default=os.getenv('DB_NAME', 'netbox'),
        required=False,
        help="postgreSQL database name",
    )
    parser.add_argument(
        '--postgres-user',
        dest='postgresUser',
        type=str,
        default=os.getenv('DB_USER', 'netbox'),
        required=False,
        help="postgreSQL user name",
    )
    parser.add_argument(
        '--postgres-password',
        dest='postgresPassword',
        type=str,
        default=os.getenv('DB_PASSWORD', ''),
        required=False,
        help="postgreSQL password",
    )
    try:
        parser.error = parser.exit
        args = parser.parse_args()
    except SystemExit:
        parser.print_help()
        exit(2)

    args.verbose = logging.ERROR - (10 * args.verbose) if args.verbose > 0 else 0
    logging.basicConfig(
        level=args.verbose, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.debug(os.path.join(script_path, script_name))
    logging.debug("Arguments: {}".format(sys.argv[1:]))
    logging.debug("Arguments: {}".format(args))
    if args.verbose > logging.DEBUG:
        sys.tracebacklimit = 0

    netboxVenvPy = os.path.join(os.path.join(os.path.join(args.netboxDir, 'venv'), 'bin'), 'python')
    manageScript = os.path.join(os.path.join(args.netboxDir, 'netbox'), 'manage.py')

    # if there is a database backup .gz in the preload directory, load it up (preferring the newest)
    # if there are multiple) instead of populating via API
    preloadDatabaseFile = args.preloadBackupFile
    preloadDatabaseSuccess = False
    if (not os.path.isfile(preloadDatabaseFile)) and os.path.isdir(args.preloadDir):
        preloadFiles = [
            x
            for x in list(filter(os.path.isfile, glob.glob(os.path.join(args.preloadDir, '*.gz'))))
            if not x.endswith('.media.tar.gz')
        ]
        preloadFiles.sort(key=lambda x: os.path.getmtime(x))
        preloadDatabaseFile = next(iter(preloadFiles), '')

    if os.path.isfile(preloadDatabaseFile):
        # we're loading an existing database directly with postgreSQL
        # this should pretty much match what is in control.py:netboxRestore
        try:
            osEnv = os.environ.copy()
            osEnv['PGPASSWORD'] = args.postgresPassword

            # stop the netbox processes (besides this one)
            cmd = [
                'bash',
                '-c',
                "supervisorctl status netbox:* | grep -v :initialization | awk '{ print $1 }' | xargs -r -L 1 -P 4 supervisorctl stop",
            ]
            err, results = malcolm_utils.run_process(cmd, logger=logging)
            if err != 0:
                logging.error(f'{err} stopping netbox:*: {results}')

            # drop the existing netbox database
            cmd = [
                'dropdb',
                '-h',
                args.postgresHost,
                '-U',
                args.postgresUser,
                '-f',
                args.postgresDB,
            ]
            err, results = malcolm_utils.run_process(cmd, env=osEnv, logger=logging)
            if err != 0:
                logging.warning(f'{err} dropping NetBox database: {results}')

            # create a new netbox database
            cmd = [
                'createdb',
                '-h',
                args.postgresHost,
                '-U',
                args.postgresUser,
                args.postgresDB,
            ]
            err, results = malcolm_utils.run_process(cmd, env=osEnv, logger=logging)
            if err != 0:
                raise Exception(f'Error {err} creating new NetBox database: {results}')

            # make sure permissions are set up right
            cmd = [
                'psql',
                '-h',
                args.postgresHost,
                '-U',
                args.postgresUser,
                '-c',
                f'GRANT ALL PRIVILEGES ON DATABASE {args.postgresDB} TO {args.postgresUser}',
            ]
            err, results = malcolm_utils.run_process(cmd, env=osEnv, logger=logging)
            if err != 0:
                logging.error(f'{err} setting NetBox database permissions: {results}')

            # load the backed-up psql dump
            cmd = [
                'psql',
                '-h',
                args.postgresHost,
                '-U',
                args.postgresUser,
            ]
            with (
                gzip.open(preloadDatabaseFile, 'rt')
                if 'application/gzip' in magic.from_file(preloadDatabaseFile, mime=True)
                else open(preloadDatabaseFile, 'r')
            ) as f:
                err, results = malcolm_utils.run_process(cmd, env=osEnv, logger=logging, stdin=f.read())
            if (err == 0) and results:
                preloadDatabaseSuccess = True
            else:
                raise Exception(f'Error {err} loading NetBox database: {results}')

            # with idaholab/Malcolm#280 we switched to use prefix.description instead of VRF for identifying subnets in NetBox,
            # this will migrate ipam_vrf.name to ipam_prefix.description if we're coming from an older backup
            cmd = [
                'psql',
                '-h',
                args.postgresHost,
                '-U',
                {args.postgresUser},
                '-c',
                "UPDATE ipam_prefix SET description = (SELECT name from ipam_vrf WHERE id = ipam_prefix.vrf_id) WHERE ((description = '') IS NOT FALSE) AND (vrf_id > 0)",
            ]
            err, results = malcolm_utils.run_process(cmd, env=osEnv, logger=logging)
            if err != 0:
                logging.error(f'{err} migrating ipam_vrf.name to ipam_prefix.description: {results}')

            # don't restore auth_user, tokens, etc: they're created by Malcolm and may not be the same on this instance
            cmd = [
                'psql',
                '-h',
                args.postgresHost,
                '-U',
                {args.postgresUser},
                '-c',
                'TRUNCATE auth_user CASCADE',
            ]
            err, results = malcolm_utils.run_process(cmd, env=osEnv, logger=logging)
            if err != 0:
                logging.error(f'{err} truncating table auth_user table: {results}')

            # start back up the netbox processes (except initialization)
            cmd = [
                'bash',
                '-c',
                "supervisorctl status netbox:* | grep -v :initialization | awk '{ print $1 }' | xargs -r -L 1 -P 4 supervisorctl start",
            ]
            err, results = malcolm_utils.run_process(cmd, logger=logging)
            if err != 0:
                logging.error(f'{err} starting netbox:*: {results}')

            with malcolm_utils.pushd(os.path.dirname(manageScript)):
                # migrations if needed
                cmd = [
                    netboxVenvPy,
                    os.path.basename(manageScript),
                    "migrate",
                ]
                err, results = malcolm_utils.run_process(cmd, logger=logging)
                if (err != 0) or (not results):
                    logging.error(f'{err} performing NetBox migration: {results}')

                # create auth_user for superuser
                cmd = [
                    netboxVenvPy,
                    os.path.basename(manageScript),
                    "shell",
                    "--interface",
                    "python",
                ]
                with open('/usr/local/bin/netbox_superuser_create.py', 'r') as f:
                    err, results = malcolm_utils.run_process(cmd, logger=logging, stdin=f.read())
                if (err != 0) or (not results):
                    logging.error(f'{err} setting up superuser: {results}')

            # restore media directory
            preloadDatabaseFileParts = os.path.splitext(preloadDatabaseFile)
            mediaFileName = preloadDatabaseFileParts[0] + ".media.tar.gz"
            mediaPath = os.path.join(args.netboxDir, os.path.join('netbox', 'media'))
            if os.path.isfile(mediaFileName) and os.path.isdir(mediaPath):
                try:
                    malcolm_utils.RemoveEmptyFolders(mediaPath, removeRoot=False)
                    with tarfile.open(mediaFileName) as t:
                        t.extractall(mediaPath)
                except Exception as e:
                    logging.error(f"{type(e).__name__} processing restoring {os.path.basename(mediaFileName)}: {e}")

        except Exception as e:
            logging.error(f"{type(e).__name__} restoring {os.path.basename(preloadDatabaseFile)}: {e}")

    # only proceed to do the regular population/preload if if we didn't preload a database backup, or
    #   if we attempted (and failed) but they didn't explicitly specify a backup file
    if not preloadDatabaseSuccess and (not args.preloadBackupFile):
        # create connection to netbox API
        nb = pynetbox.api(
            args.netboxUrl,
            token=args.netboxToken,
            threading=True,
        )
        sites = {}
        groups = {}
        permissions = {}
        prefixes = {}
        devices = {}
        interfaces = {}
        ipAddresses = {}
        deviceTypes = {}
        roles = {}
        manufacturers = {}
        randColor = randomcolor.RandomColor(seed=datetime.now().timestamp())

        # wait for a good connection
        while args.wait:
            try:
                [x.name for x in nb.dcim.sites.all()]
                break
            except Exception as e:
                logging.info(f"{type(e).__name__}: {e}")
                logging.debug("retrying in a few seconds...")
                time.sleep(5)

        # GROUPS #####################################################################################################
        DEFAULT_GROUP_NAMES = (
            args.staffGroupName,
            args.defaultGroupName,
        )

        try:
            groupsPreExisting = {x.name: x for x in nb.users.groups.all()}
            logging.debug(f"groups (before): { {k:v.id for k, v in groupsPreExisting.items()} }")

            # create groups that don't already exist
            for groupName in [x for x in DEFAULT_GROUP_NAMES if x not in groupsPreExisting]:
                try:
                    nb.users.groups.create({'name': groupName})
                except pynetbox.RequestError as nbe:
                    logging.warning(f"{type(nbe).__name__} processing group \"{groupName}\": {nbe}")

            groups = {x.name: x for x in nb.users.groups.all()}
            logging.debug(f"groups (after): { {k:v.id for k, v in groups.items()} }")
        except Exception as e:
            logging.error(f"{type(e).__name__} processing groups: {e}")

        # PERMISSIONS ##################################################################################################
        DEFAULT_PERMISSIONS = {
            f'{args.staffGroupName}_permission': {
                'name': f'{args.staffGroupName}_permission',
                'enabled': True,
                'groups': [args.staffGroupName],
                'actions': [
                    'view',
                    'add',
                    'change',
                    'delete',
                ],
                'exclude_objects': [],
            },
            f'{args.defaultGroupName}_permission': {
                'name': f'{args.defaultGroupName}_permission',
                'enabled': True,
                'groups': [args.defaultGroupName],
                'actions': [
                    'view',
                    'add',
                    'change',
                    'delete',
                ],
                'exclude_objects': [
                    'admin.logentry',
                    'auth.group',
                    'auth.permission',
                    'auth.user',
                    'users.admingroup',
                    'users.adminuser',
                    'users.objectpermission',
                    'users.token',
                    'users.userconfig',
                ],
            },
        }

        try:
            # get all content types (for creating new permissions)
            allContentTypeNames = [f'{x.app_label}.{x.model}' for x in nb.extras.content_types.all()]

            permsPreExisting = {x.name: x for x in nb.users.permissions.all()}
            logging.debug(f"permissions (before): { {k:v.id for k, v in permsPreExisting.items()} }")

            # create permissions that don't already exist
            for permName, permConfig in {
                k: v
                for (k, v) in DEFAULT_PERMISSIONS.items()
                if v.get('name', None) and v['name'] not in permsPreExisting
            }.items():
                permConfig['groups'] = [groups[x].id for x in permConfig['groups']]
                permConfig['object_types'] = [
                    ct for ct in allContentTypeNames if ct not in permConfig['exclude_objects']
                ]
                permConfig.pop('exclude_objects', None)
                try:
                    nb.users.permissions.create(permConfig)
                except pynetbox.RequestError as nbe:
                    logging.warning(f"{type(nbe).__name__} processing permission \"{permConfig['name']}\": {nbe}")

            permissions = {x.name: x for x in nb.users.permissions.all()}
            logging.debug(f"permissions (after): { {k:v.id for k, v in permissions.items()} }")
        except Exception as e:
            logging.error(f"{type(e).__name__} processing permissions: {e}")

        # ###### MANUFACTURERS #########################################################################################
        try:
            manufacturersPreExisting = {x.name: x for x in nb.dcim.manufacturers.all()}
            logging.debug(f"Manufacturers (before): { {k:v.id for k, v in manufacturersPreExisting.items()} }")

            # create manufacturers that don't already exist
            for manufacturerName in [x for x in args.manufacturers if x not in manufacturersPreExisting]:
                try:
                    nb.dcim.manufacturers.create(
                        {
                            "name": manufacturerName,
                            "slug": slugify(manufacturerName),
                        },
                    )
                except pynetbox.RequestError as nbe:
                    logging.warning(f"{type(nbe).__name__} processing manufacturer \"{manufacturerName}\": {nbe}")

            manufacturers = {x.name: x for x in nb.dcim.manufacturers.all()}
            logging.debug(f"Manufacturers (after): { {k:v.id for k, v in manufacturers.items()} }")
        except Exception as e:
            logging.error(f"{type(e).__name__} processing manufacturers: {e}")

        # ###### ROLES #################################################################################################
        try:
            rolesPreExisting = {x.name: x for x in nb.dcim.device_roles.all()}
            logging.debug(f"Roles (before): { {k:v.id for k, v in rolesPreExisting.items()} }")

            # create roles that don't already exist
            for roleName in [x for x in args.roles if x not in rolesPreExisting]:
                try:
                    nb.dcim.device_roles.create(
                        {
                            "name": roleName,
                            "slug": slugify(roleName),
                            "vm_role": True,
                            "color": randColor.generate()[0][1:],
                        },
                    )
                except pynetbox.RequestError as nbe:
                    logging.warning(f"{type(nbe).__name__} processing role \"{roleName}\": {nbe}")

            roles = {x.name: x for x in nb.dcim.device_roles.all()}
            logging.debug(f"Roles (after): { {k:v.id for k, v in roles.items()} }")
        except Exception as e:
            logging.error(f"{type(e).__name__} processing roles: {e}")

        # ###### DEVICE TYPES ##########################################################################################
        try:
            deviceTypesPreExisting = {x.model: x for x in nb.dcim.device_types.all()}
            logging.debug(f"Device types (before): { {k:v.id for k, v in deviceTypesPreExisting.items()} }")

            # create device types that don't already exist
            for deviceTypeModel in [x for x in args.deviceTypes if x not in deviceTypesPreExisting]:
                try:
                    manuf = min_hash_value_by_value(manufacturers)
                    nb.dcim.device_types.create(
                        {
                            "model": deviceTypeModel,
                            "slug": slugify(deviceTypeModel),
                            "manufacturer": manuf.id if manuf else None,
                        },
                    )
                except pynetbox.RequestError as nbe:
                    logging.warning(f"{type(nbe).__name__} processing device type \"{deviceTypeModel}\": {nbe}")

            deviceTypes = {x.model: x for x in nb.dcim.device_types.all()}
            logging.debug(f"Device types (after): { {k:v.id for k, v in deviceTypes.items()} }")
        except Exception as e:
            logging.error(f"{type(e).__name__} processing device types: {e}")

        # ###### SITES #################################################################################################
        try:
            sitesPreExisting = {x.name: x for x in nb.dcim.sites.all()}
            logging.debug(f"sites (before): { {k:v.id for k, v in sitesPreExisting.items()} }")

            # create sites that don't already exist
            for siteName in [x for x in args.netboxSites if x not in sitesPreExisting]:
                try:
                    nb.dcim.sites.create(
                        {
                            "name": siteName,
                            "slug": slugify(siteName),
                        },
                    )
                except pynetbox.RequestError as nbe:
                    logging.warning(f"{type(nbe).__name__} processing site \"{siteName}\": {nbe}")

            sites = {x.name: x for x in nb.dcim.sites.all()}
            logging.debug(f"sites (after): { {k:v.id for k, v in sites.items()} }")
        except Exception as e:
            logging.error(f"{type(e).__name__} processing sites: {e}")

        # ###### Net Map ###############################################################################################
        try:
            # load net-map.json from file
            netMapJson = None
            if args.netMapFileName is not None and os.path.isfile(args.netMapFileName):
                with open(args.netMapFileName) as f:
                    netMapJson = json.load(f)
            if netMapJson is not None:
                # create IP prefixes

                prefixesPreExisting = {x.prefix: x for x in nb.ipam.prefixes.all()}
                logging.debug(f"prefixes (before): { {k:v.id for k, v in prefixesPreExisting.items()} }")

                for segment in [
                    x
                    for x in get_iterable(netMapJson)
                    if isinstance(x, dict)
                    and (x.get('type', '') == "segment")
                    and x.get('name', None)
                    and is_ip_network(x.get('address', None))
                ]:
                    try:
                        site = min_hash_value_by_value(sites)
                        nb.ipam.prefixes.create(
                            {
                                "prefix": segment['address'],
                                "site": site.id if site else None,
                                "description": segment['name'],
                            },
                        )
                    except pynetbox.RequestError as nbe:
                        logging.warning(
                            f"{type(nbe).__name__} processing prefix \"{segment['address']}\" (\"{segment['name']}\"): {nbe}"
                        )

                prefixes = {x.prefix: x for x in nb.ipam.prefixes.all()}
                logging.debug(f"prefixes (after): { {k:v.id for k, v in prefixes.items()} }")

                # create hosts as devices
                devicesPreExisting = {x.name: x for x in nb.dcim.devices.all()}
                logging.debug(f"devices (before): { {k:v.id for k, v in devicesPreExisting.items()} }")

                for host in [
                    x
                    for x in get_iterable(netMapJson)
                    if isinstance(x, dict)
                    and (x.get('type', '') == "host")
                    and x.get('name', None)
                    and x.get('address', None)
                    and x['name'] not in devicesPreExisting
                ]:
                    try:
                        site = min_hash_value_by_value(sites)
                        dType = min_hash_value_by_value(deviceTypes)
                        role = min_hash_value_by_value(roles)
                        deviceCreated = nb.dcim.devices.create(
                            {
                                "name": host['name'],
                                "site": site.id if site else None,
                                "device_type": dType.id if dType else None,
                                "role": role.id if role else None,
                            },
                        )
                        if deviceCreated is not None:
                            # create interface for the device
                            if is_ip_address(host['address']):
                                nb.dcim.interfaces.create(
                                    {
                                        "device": deviceCreated.id,
                                        "name": "default",
                                        "type": "other",
                                    },
                                )
                            elif re.match(r'^([0-9a-f]{2}[:-]){5}([0-9a-f]{2})$', host['address'].lower()):
                                nb.dcim.interfaces.create(
                                    {
                                        "device": deviceCreated.id,
                                        "name": "default",
                                        "type": "other",
                                        "mac_address": host['address'].lower(),
                                    },
                                )

                    except pynetbox.RequestError as nbe:
                        logging.warning(f"{type(nbe).__name__} processing device \"{host['name']}\": {nbe}")

                devices = {x.name: x for x in nb.dcim.devices.all()}
                logging.debug(f"devices (after): { {k:v.id for k, v in devices.items()} }")
                interfaces = {x.device.id: x for x in nb.dcim.interfaces.all()}
                logging.debug(f"interfaces (after): { {k:v.id for k, v in interfaces.items()} }")

                # and associate IP addresses with them
                ipAddressesPreExisting = {x.address: x for x in nb.ipam.ip_addresses.all()}
                logging.debug(f"IP addresses (before): { {k:v.id for k, v in ipAddressesPreExisting.items()} }")

                for host in [
                    x
                    for x in get_iterable(netMapJson)
                    if isinstance(x, dict)
                    and (x.get('type', '') == "host")
                    and x.get('name', None)
                    and is_ip_address(x.get('address', None))
                    and x['name'] in devices
                ]:
                    try:
                        hostKey = f"{host['address']}/{'32' if is_ip_v4_address(host['address']) else '128'}"
                        if hostKey not in ipAddressesPreExisting:
                            ipCreated = nb.ipam.ip_addresses.create(
                                {
                                    "address": host['address'],
                                    "assigned_object_type": "dcim.interface",
                                    "assigned_object_id": interfaces[devices[host['name']].id].id,
                                },
                            )
                            if ipCreated is not None:
                                # update device to set this as its primary IPv4 address
                                deviceForIp = nb.dcim.devices.get(id=devices[host['name']].id)
                                if deviceForIp is not None:
                                    if is_ip_v4_address(host['address']):
                                        deviceForIp.primary_ip4 = ipCreated
                                    elif is_ip_v6_address(host['address']):
                                        deviceForIp.primary_ip = ipCreated
                                    deviceForIp.save()

                    except pynetbox.RequestError as nbe:
                        logging.warning(f"{type(nbe).__name__} processing address \"{host['address']}\": {nbe}")

                ipAddresses = {x.address: x for x in nb.ipam.ip_addresses.all()}
                logging.debug(f"IP addresses (after): { {k:v.id for k, v in ipAddresses.items()} }")

        except Exception as e:
            logging.error(f"{type(e).__name__} processing net map JSON \"{args.netMapFileName}\": {e}")

        # ###### Missing prefix descriptions from VRF names (see idaholab/Malcolm#280) ##################################
        try:
            for prefix in [x for x in nb.ipam.prefixes.filter(description__empty=True) if x.vrf]:
                logging.debug(f"Updating prefix {str(prefix)}'s description to {str(prefix.vrf)}")
                prefix.update(
                    {
                        "description": str(prefix.vrf),
                    }
                )

        except Exception as e:
            logging.error(f"{type(e).__name__} migrating prefix VRF to prefix description: {e}")

        # ###### Netbox-Initializers ###################################################################################
        if os.path.isfile(netboxVenvPy) and os.path.isfile(manageScript) and os.path.isdir(args.preloadDir):
            try:
                with malcolm_utils.pushd(os.path.dirname(manageScript)):
                    # make a local copy of the YMLs to preload
                    with tempfile.TemporaryDirectory() as tmpPreloadDir:
                        copy_tree(args.preloadDir, tmpPreloadDir)

                        # only preload catch-all IP Prefixes if explicitly specified and they don't already exist
                        if args.preloadPrefixes:
                            defaultSiteName = next(iter([x for x in args.netboxSites]), None)
                            for loadType in ('vrfs', 'prefixes'):
                                defaultFileName = os.path.join(tmpPreloadDir, f'{loadType}_defaults.yml')
                                loadFileName = os.path.join(tmpPreloadDir, f'{loadType}.yml')
                                if os.path.isfile(defaultFileName) and (not os.path.isfile(loadFileName)):
                                    try:
                                        with open(defaultFileName, 'r') as infile:
                                            with open(loadFileName, 'w') as outfile:
                                                for line in infile:
                                                    outfile.write(line.replace("NETBOX_DEFAULT_SITE", defaultSiteName))
                                    except Exception:
                                        pass

                        retcode, output = malcolm_utils.run_process(
                            [
                                netboxVenvPy,
                                os.path.basename(manageScript),
                                "load_initializer_data",
                                "--path",
                                tmpPreloadDir,
                            ],
                            logger=logging,
                        )
                        if retcode == 0:
                            logging.debug(f"netbox-initializers: {retcode} {output}")
                        else:
                            logging.error(f"Error processing netbox-initializers: {retcode} {output}")

            except Exception as e:
                logging.error(f"{type(e).__name__} processing netbox-initializers: {e}")

        # ######  Device-Type-Library-Import ###########################################################################
        if os.path.isdir(args.libraryDir):
            try:
                with malcolm_utils.pushd(args.libraryDir):
                    osEnv = os.environ.copy()
                    osEnv['NETBOX_URL'] = args.netboxUrl
                    osEnv['NETBOX_TOKEN'] = args.netboxToken
                    osEnv['REPO_URL'] = 'local'
                    cmd = [netboxVenvPy, 'nb-dt-import.py']
                    err, results = malcolm_utils.run_process(
                        cmd,
                        logger=logging,
                        env=osEnv,
                    )
                    if (err != 0) or (not results):
                        logging.error(f"{err} running nb-dt-import.py: {results}")

            except Exception as e:
                logging.error(f"{type(e).__name__} processing library: {e}")


###################################################################################################
if __name__ == '__main__':
    main()
