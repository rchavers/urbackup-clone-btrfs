#! /usr/bin/python3

"""
This is a helper program used to mount luks encrypted btrfs drives
the drives are unencrypted via calling:
    urbackup-clone-btrfs.mount.py mount
the drives are locked and unmounted via calling:
    urbackup-clone-btrfs.mount.py unmount

# create a luks formatted disk (adjust as needed):
# repeat parted for each drive (sdX,sdY,sdZ) needed: (a1,a2,a3)
parted /dev/sdX
  unit MiB
  mklabel gpt
  mkpart offsite-urbackup-a1 4MiB 100%

cryptsetup luksFormat /dev/sdX1
  (type in passphrase)

cryptsetup open /dev/sdX1 offsite-urbackup-a1
  (type in passphrase)

mkfs.btrfs -L offsite-urbackup-a -m single -d single \
  /dev/mapper/offsite-urbackup-a1
  /dev/mapper/offsite-urbackup-a2
  /dev/mapper/offsite-urbackup-a3

# To use this program, remember to put passphrase in the keyfile (single line only)
echo -n "mysecretpassphrase" > /usr/local/bin/urbackup-clone-btrfs/urbackup-clone-btrfs.keyfile
"""

"""
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 3
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
"""

import os
import re
import sys
import shlex
import subprocess
from traceback import print_exc
from argparse import ArgumentParser


"""
btrfs drives map format is:
{'decrypted btrfs uuid':(('encrypted luks uuid', 'device mapper name'), ...), ...}
'encrypted luks uuid' is found by "lsblk -f"
  look at the crypto_LUKS partition UUID column
'device mapper name' is "offsite-urbackup-XY"
  where X are letters (a,b,c...) and Y are sequential integers
"""
BTRFS_DRIVES_MAP ={
    '4a75033e-7553-4a47-84d3-b9ab75281f20':(
        ('fd2082b1-4ccc-44fb-98b9-cc7aec0d68f7', 'offsite-urbackup-a1'),
        ('693e94e9-e417-4ac5-8a2d-9bfb01cabd5d', 'offsite-urbackup-a2'),
        ('2f4cba72-5dda-458c-b636-37a301ca5595', 'offsite-urbackup-a3'),
        ),
    'd632677b-bd06-4513-a090-ee5861e69a4d':(
        ('8a83b660-07cb-462a-9e29-38beea2a3f34', 'offsite-urbackup-b1'),
        ('e3268ba9-c24c-488b-95fb-8e5454e0da60', 'offsite-urbackup-b2'),
        ('db349a72-1aba-44be-8beb-c69aeec29e35', 'offsite-urbackup-b3'),
        ),
    }

# cryptsetup unlock keyfile
KEYFILE = '/usr/local/bin/urbackup-clone-btrfs/urbackup-clone-btrfs.keyfile'

# where to mount the btrfs decrypted filesystem
MOUNT_POINT = '/mnt/offsite-urbackup'

# mount options for the btrfs filesystem
MOUNT_OPTIONS = '-o compress-force=zstd:3'

# program locations
BTRFS = '/usr/bin/btrfs'
MOUNT = '/usr/bin/mount'
UMOUNT = '/usr/bin/umount'
CRYPTSETUP = '/usr/sbin/cryptsetup'


##
## function definitions
##

def parse_args():
    # define program arguments
    parser = ArgumentParser()
    help = 'either mount encrypted drives or unmount and lock them'
    choices = ['mount','unmount']
    parser.add_argument('action', choices=choices, help=help, type=str.lower)
    # return program options
    return parser.parse_args()


def get_filesystem_uuid(mount_point):
    """
    returns the uuid of the passed btrfs mount point
    returns None if there was an error
    """
    cmd = [BTRFS, 'filesystem', 'show', mount_point]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        re_pattern = r'uuid: (?P<uuid>[-a-f0-9]*)'
        uuid = re.findall(re_pattern, result.stdout, re.MULTILINE)
        return uuid[0]
    except:
        return None


def mount():
    # does the mountpoint already exist?
    if os.path.exists(MOUNT_POINT):
        # check to see if something is already mounted at mountpoint
        if os.path.ismount(MOUNT_POINT):
            mounted_uuid = get_filesystem_uuid(MOUNT_POINT)
            if mounted_uuid in BTRFS_DRIVES_MAP:
                print (f'btrfs uuid {mounted_uuid} is already mounted at {MOUNT_POINT}')
                sys.exit(0)
            else:
                print (f'ERROR: Unknown filesystem is mounted at {MOUNT_POINT}', file=sys.stderr)
                sys.exit(1)
        else:
            # mountpoint exists, but nothing is mounted
            pass
    else:
        # if mountpoint is missing, create it
        print (f'Creating mountpoint {MOUNT_POINT}')
        os.mkdir(MOUNT_POINT)

    # open (unlock) the drives using cryptsetup and the keyfile
    mounted_drives = []
    for btrfs_uuid in BTRFS_DRIVES_MAP:
        print (f'Trying to find drives for btrfs uuid {btrfs_uuid}')
        found = False
        for luks_uuid, dev_mapper in BTRFS_DRIVES_MAP[btrfs_uuid]:
            path_luks_uuid = f'/dev/disk/by-uuid/{luks_uuid}'
            path_dev_mapper = f'/dev/mapper/{dev_mapper}'
            if os.path.exists(path_luks_uuid):
                print (f'  Found luks encrypted drive {path_luks_uuid}')
                found = True
                if os.path.exists(path_dev_mapper):
                    # drive is already unlocked
                    print (f'    drive is already unlocked at {path_dev_mapper}')
                    if btrfs_uuid not in mounted_drives:
                        mounted_drives.append(btrfs_uuid)
                else:
                    cmd = f'{CRYPTSETUP} open {path_luks_uuid} {dev_mapper} --key-file {KEYFILE}'
                    cmd_list = shlex.split(cmd)
                    result = subprocess.run(cmd_list, check=True, capture_output=True, text=True)
                    if result.returncode == 0:
                        print (f'    Successfully unlocked {path_luks_uuid}')
                        if btrfs_uuid not in mounted_drives:
                            mounted_drives.append(btrfs_uuid)
                    else:
                        print (result, file=sys.stderr)
        if not found:
            print (f'  No encrypted drives found for {btrfs_uuid}')

    # if there are no drives found, exit
    if not mounted_drives:
        print ('No encrypted drives found, exiting', file=sys.stderr)
        sys.exit(1)

    # mount the unlocked drives
    for btrfs_uuid in mounted_drives:
        cmd = f'{MOUNT} {MOUNT_OPTIONS} --uuid {btrfs_uuid} {MOUNT_POINT}'
        cmd_list = shlex.split(cmd)
        result = subprocess.run(cmd_list, check=True, capture_output=True, text=True)
        if result.returncode == 0:
            print (f'Successfully mounted {btrfs_uuid} to {MOUNT_POINT}')
        else:
            print (result, file=sys.stderr)


def unmount():
    if os.path.ismount(MOUNT_POINT):
        mounted_uuid = get_filesystem_uuid(MOUNT_POINT)
        if mounted_uuid in BTRFS_DRIVES_MAP:
            # try to unmount
            cmd = f'{UMOUNT} {MOUNT_POINT}'
            cmd_list = shlex.split(cmd)
            result = subprocess.run(cmd_list, check=True, capture_output=True, text=True)
            if result.returncode == 0:
                print (f'Successfully unmounted {MOUNT_POINT}')
            else:
                print (f'Error unmounting {MOUNT_POINT}')
                print (result, file=sys.stderr)
                sys.exit(1)
        else:
            print (f'ERROR: Unknown filesystem is mounted at {MOUNT_POINT}', file=sys.stderr)
            sys.exit(1)

    error = False
    for luks_uuid, dev_mapper in BTRFS_DRIVES_MAP[mounted_uuid]:
        path_luks_uuid = f'/dev/disk/by-uuid/{luks_uuid}'
        path_dev_mapper = f'/dev/mapper/{dev_mapper}'
        if os.path.exists(path_dev_mapper):
            # need to lock (close) this drive
            print (f'Found unlocked drive at {path_dev_mapper}')
            cmd = f'{CRYPTSETUP} close {dev_mapper}'
            cmd_list = shlex.split(cmd)
            result = subprocess.run(cmd_list, check=True, capture_output=True, text=True)
            if result.returncode == 0:
                print (f'  Successfully locked {path_luks_uuid}')
            else:
                error = True
                print (result, file=sys.stderr)

    # try to rmdir the mountpoint
    try:
        os.rmdir(MOUNT_POINT)
    except:
        print (f'Error: could not remove {MOUNT_POINT}')
        error = True

    # any errors?
    if error:
        print ('Error: could not lock all drives', file=sys.stderr)
        sys.exit(1)



##
## Main program
##

if __name__ == "__main__":
    # variables assigned here can be used in all functions
    try:
        # parse program arguments
        args = parse_args()
        if args.action == 'mount':
            mount()
        elif args.action == 'unmount':
            unmount()
    except SystemExit:
        # argparse error, or help (-h) was requested
        pass
    except subprocess.CalledProcessError as e:
        print ('Error while running: "%s"' %shlex.join(e.cmd))
        if e.stdout: print (e.stdout)
        if e.stderr: print (e.stderr)
    except:
        print_exc()
