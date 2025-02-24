#!/usr/bin/python3

import re
import sys
import subprocess
import os

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
MALCOLM_DIR = os.path.dirname(SCRIPT_DIR)
SENSOR_DIR = os.path.join(MALCOLM_DIR, 'sensor-iso')

# pylint: disable=invalid-name

### Sanity/usage checks

if len(sys.argv) != 3:
    print("E: need 2 arguments", file=sys.stderr)
    sys.exit(1)

version = sys.argv[1]
if version not in ["1", "2", "3", "4"]:
    print("E: unsupported version %s" % version, file=sys.stderr)
    sys.exit(1)

suite = sys.argv[2]
if suite not in ['bullseye', 'bookworm', 'trixie']:
    print("E: unsupported suite %s" % suite, file=sys.stderr)
    sys.exit(1)
target_yaml = 'raspi_%s_%s.yaml' % (version, suite)


### Setting variables based on suite and version starts here

# Arch, kernel, DTB:
if version == '1':
    arch = 'armel'
    linux = 'linux-image-rpi'
    dtb = '/usr/lib/linux-image-*-rpi/bcm*rpi-*.dtb'
elif version == '2':
    arch = 'armhf'
    linux = 'linux-image-armmp'
    dtb = '/usr/lib/linux-image-*-armmp/bcm*rpi*.dtb'
elif version in ['3', '4']:
    arch = 'arm64'
    linux = 'linux-image-arm64'
    dtb = '/usr/lib/linux-image-*-arm64/broadcom/bcm*rpi*.dtb'

# Bookworm introduced the 'non-free-firmware' component¹; before that,
# raspi-firmware was in 'non-free'
#
# ¹ https://www.debian.org/vote/2022/vote_003
if suite != 'bullseye':
    firmware_component = 'non-free-firmware'
    firmware_component_old = 'non-free'
else:
    firmware_component = 'non-free'
    firmware_component_old = ''

# wireless firmware:
if version != '2':
    wireless_firmware = 'firmware-brcm80211'
else:
    wireless_firmware = ''

# bluetooth firmware:
if version != '2':
    bluetooth_firmware = 'bluez-firmware'
else:
    bluetooth_firmware = ''

# Pi 4 on buster required some backports. Let's keep variables around, ready to
# be used whenever we need to pull specific things from backports.
backports_enable = False
backports_suite = '%s-backports' % suite

# Serial console:
if version in ['1', '2']:
    serial = 'ttyAMA0,115200'
elif version in ['3', '4']:
    serial = 'ttyS1,115200'

# CMA fixup:
extra_chroot_shell_cmds = []
if version == '4':
    extra_chroot_shell_cmds = [
        "sed -i 's/cma=64M //' /boot/firmware/cmdline.txt",
    ]

# Hostname:
hostname = 'Hedgehog-rpi-%s' % version

# Nothing yet!
extra_root_shell_cmds = [
    'cp sensor_install.sh "${ROOT?}/root/"',
    '/bin/bash -c \'mkdir -p "${ROOT?}/opt/"{buildshared,deps,hooks,patches,sensor/sensor_ctl/suricata/rules-default,arkime/etc,zeek/bin}\'',
    'cp "%s/arkime/patch/"* "${ROOT?}/opt/patches/" || true' % MALCOLM_DIR,
    'cp "%s/arkime/etc/"* "${ROOT?}/opt/arkime/etc" || true' % SENSOR_DIR,
    'cp -r "%s/suricata/rules-default/"* "${ROOT?}/opt/sensor/sensor_ctl/suricata/rules-default/" || true'
    % MALCOLM_DIR,
    'cp -r shared/* "${ROOT?}/opt/buildshared"',
    'cp -r "%s/interface/"* "${ROOT?}/opt/sensor"' % SENSOR_DIR,
    'cp -r "%s/shared/bin/"* "${ROOT?}/usr/local/bin"' % MALCOLM_DIR,
    'cp "%s/scripts/malcolm_utils.py" "${ROOT?}/usr/local/bin/"' % MALCOLM_DIR,
    'cp "%s/config/archives/beats.list.chroot" "${ROOT?}/etc/apt/sources.list.d/beats.list"' % SENSOR_DIR,
    'cp "%s/config/archives/beats.key.chroot" "${ROOT?}/etc/apt/keyrings/"' % SENSOR_DIR,
    'cp "%s/config/archives/fluentbit.list.chroot" "${ROOT?}/etc/apt/sources.list.d/fluentbit.list"' % SENSOR_DIR,
    'cp "%s/config/archives/fluentbit.key.chroot" "${ROOT?}/etc/apt/keyrings/"' % SENSOR_DIR,
    'cp -r "%s/config/includes.chroot/"* "${ROOT?}/"' % SENSOR_DIR,
    'rm -r "${ROOT?}/etc/live"',
    'cp -r "%s/config/hooks/normal/"* "${ROOT?}/opt/hooks/"' % SENSOR_DIR,
    'cp -r "%s/config/package-lists/"* "${ROOT?}/opt/deps/"' % SENSOR_DIR,
    'cp -r "%s/docs/images/hedgehog/logo/hedgehog-ascii-text.txt"* "${ROOT?}/root/"' % MALCOLM_DIR,
]

# Extend list just in case version is 4
extra_chroot_shell_cmds.extend(
    [
        'chmod 755 /root/sensor_install.sh',
        '/root/sensor_install.sh 2>&1 | tee -a /root/sensor_install_debug',
    ]
)

### The following prepares substitutions based on variables set earlier

# Enable backports with a reason, or add commented-out entry:
if backports_enable:
    backports_stanza = """
%s
deb http://deb.debian.org/debian/ %s main %s
""" % (
        backports_enable,
        backports_suite,
        firmware_component,
    )
else:
    # ugh
    backports_stanza = """
# Backports are _not_ enabled by default.
# Enable them by uncommenting the following line:
# deb http://deb.debian.org/debian %s main %s
""" % (
        backports_suite,
        firmware_component,
    )

# gitcommit = subprocess.getoutput("git show -s --pretty='format:%C(auto)%h (%s, %ad)' --date=short ")
buildtime = subprocess.getoutput("date --utc +'%Y-%m-%d %H:%M'")

### Write results:


def align_replace(text, pattern, replacement):
    """
    This helper lets us keep the indentation of the matched pattern
    with the upcoming replacement, across multiple lines. Naive
    implementation, please make it more pythonic!
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r'^(\s+)%s' % pattern, line)
        if m:
            indent = m.group(1)
            del lines[i]
            for r in replacement:
                lines.insert(i, '%s%s' % (indent, r))
                i = i + 1
            break
    return '\n'.join(lines) + '\n'


with open('raspi_master.yaml', 'r') as in_file:
    with open(target_yaml, 'w') as out_file:
        in_text = in_file.read()
        out_text = (
            in_text.replace('__RELEASE__', suite)
            .replace('__ARCH__', arch)
            .replace('__FIRMWARE_COMPONENT__', firmware_component)
            .replace('__FIRMWARE_COMPONENT_OLD__', firmware_component_old)
            .replace('__LINUX_IMAGE__', linux)
            .replace('__DTB__', dtb)
            .replace('__WIRELESS_FIRMWARE__', wireless_firmware)
            .replace('__BLUETOOTH_FIRMWARE__', bluetooth_firmware)
            .replace('__SERIAL_CONSOLE__', serial)
            .replace('__HOST__', hostname)
            .replace('__BUILDTIME__', buildtime)
        )
        #            .replace('__GITCOMMIT__', gitcommit) \
        #            .replace('__BUILDTIME__', buildtime)

        out_text = align_replace(out_text, '__EXTRA_ROOT_SHELL_CMDS__', extra_root_shell_cmds)
        out_text = align_replace(out_text, '__EXTRA_CHROOT_SHELL_CMDS__', extra_chroot_shell_cmds)
        out_text = align_replace(out_text, '__BACKPORTS__', backports_stanza.splitlines())

        # Try not to keep lines where the placeholder was replaced
        # with nothing at all (including on a "list item" line):
        filtered = [x for x in out_text.splitlines() if not re.match(r'^\s+$', x) and not re.match(r'^\s+-\s*$', x)]
        out_file.write('\n'.join(filtered) + "\n")
