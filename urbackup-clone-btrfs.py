#! /usr/bin/python3

"""
urbackup-clone-btrfs
  clone a urbackup btrfs file structure
  Copyright (C) 2023 Robert Chavers
  based on btrfs-clone by Martin Wilck
  some snippets of code sourced/inspired by stackexchange contributers
  some snippets of code sourced/inspired by stackoverflow contributers

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
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
import tty
import time
import shlex
import psutil
import select
import termios
import datetime
import subprocess
from dataclasses import dataclass
from argparse import ArgumentParser
from traceback import print_exc, format_exc


##
## globals and subvolume dataclass
##

# (optional) show stats after this many send|receive tasks
# set SHOW_STATS_INTERVAL = 0 to disable
SHOW_STATS_INTERVAL = 10

# (optional) path to pv utility and the output format to use
# set PV = '' if stats are not wanted or pv is not installed
PV = '/usr/bin/pv -f -F "time [%t] -- rate %a -- size [%b]"'

# (required) path to btrfs utility
BTRFS = '/usr/bin/btrfs'

# (required) path to rsync utility
RSYNC = '/usr/bin/rsync'

# {src}, {dst} are evaluated to their respective args
# rsync_src_list trailing slashes will be removed
RSYNC_DST = '{dst}/zz.misc.backups'
RSYNC_SRC_LIST = ['/var/urbackup', '{src}/clients', '{src}/urbackup']

# vt100 codes and constants
NL = '\n'           # newline
CR = '\r'           # carrage return
ESC = '\x1b'        # escape key
TTY_EL0 = '\033[K'  # erase to end of line
TTY_EL1 = '\033[1K' # erase to start of line
TTY_EL2 = '\033[2K' # erase entire line
TTY_UP1 = '\033[1A' # move up one line

# btrfs subvolume dataclass definition
@dataclass(order=True)
class Subvol:
    id: int             # ID
    uuid: str           # uuid
    rel_path: str       # relative path
    parent_uuid: str    # parent uuid, or ''
    received_uuid: str  # received uuid, or ''


##
## program function definitions
##

def log(*lines, verbose=0, stderr=False, end=NL):
    """
    print output to the terminal, prepends date and time.
    to log to a file, pipe stdout and/or stderr to a file
    lines: either a single string or a list of strings
    verbose: prints only if args.verbose is equal or greater
    stderr: prints to stderr if True (regardless of verbose)
    end: each line's ending character (eg. NL or CR)
    """
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if stderr:
        for line in lines:
            print (now, line, end=end, file=sys.stderr)
            sys.stderr.flush()
    elif args.verbose >= verbose:
        for line in lines:
            print (now, line, end=end)
            sys.stdout.flush()


def error_handler(*messages):
    """
    log error messages to stderr
    continue if args.ignore_errors is True; otherwise, quit
    """
    for msg in messages:
        log(msg, stderr=True)
    if args.ignore_errors:
        log('* (--ignore-errors) Continuing...')
    else:
        sys.exit(1)


def rsync_urbackup_misc():
    """
    use rsync to copy the urbackup database and other miscellany:
        /var/urbackup, {src}/urbackup,  {src}/clients
        (database    , backup database, client symlinks)
    """
    success = True
    log('* Using rsync to backup databases and client symlinks')

    # normalize and format the rsync destination path
    rsync_dst_path = os.path.normpath(RSYNC_DST.format(dst=args.dst))

    # if the rsync dst path does not exist, create it (unless dry-run)
    if not os.path.exists(rsync_dst_path):
        if args.dry_run:
            log(f'  (--dry-run) not creating "{rsync_dst_path}"', verbose=1)
        else:
            log(f'  creating "{rsync_dst_path}"', verbose=1)
            os.makedirs(rsync_dst_path)

    for rsync_src in RSYNC_SRC_LIST:
        # normalize and format the rsync source path
        rsync_src_path = os.path.normpath(rsync_src.format(src=args.src))

        # build and execute the rsync command
        cmd = f'{RSYNC} -a --delete --relative "{rsync_src_path}" "{rsync_dst_path}"'
        if args.dry_run:
            log(f'  (--dry-run) not executing "{cmd}"', verbose=1)
        else:
            log(f'  executing: {cmd}', verbose=1)
            cmd_list = shlex.split(cmd)
            result = subprocess.run(
                cmd_list, check=True, capture_output=True, text=True)
            if result.returncode == 0:
                msg = f'  Successfully copied "{rsync_src_path}" to "{rsync_dst_path}"'
                log(msg, verbose=1)
            else:
                success = False
                msg = f'  Error copying "{rsync_src_path}" to "{rsync_dst_path}"'
                error_handler(msg, result.stdout, result.stderr)
    return success


def do_countdown(seconds):
    """
    presents a countdown prompt for the number of seconds
    the user can press the enter key to continue
    or the escape key to exit
    """
    if not args.interactive:
        # no need for countdown in non-interactive (cron) mode
        return

    print ()
    # save old sys.stdin settings
    old_stdin = termios.tcgetattr(sys.stdin)
    try:
        # reset stdin with no echo
        tty.setcbreak(sys.stdin.fileno())
        spaces = ' '*22
        msg = '-- Press [Enter] to continue, or [Escape] to exit program --'
        time_end = time.time() + seconds
        time_remain = int(time_end - time.time())
        # loop until enter, escape, or timeout
        while time_remain > 0:
            time_remain = int(time_end - time.time())
            mins, secs = divmod(time_remain, 60)
            cd_msg = f'{mins:02d}:{secs:02d}'   #countdown message
            print (f'{spaces}{cd_msg} {msg} {cd_msg}{TTY_EL0}', end=CR)
            key_ready = select.select([sys.stdin], [], [], 0)[0]
            if key_ready:
                # key was pushed, read and check
                key = sys.stdin.read(1)
                if args.verbose >= 2:
                    kp_msg = f'Keypress: {key.__repr__()}, Ordinal: {ord(key)}'
                    print (NL, kp_msg, TTY_UP1, end=CR)
                if key == ESC:
                    # ESCape was pressed
                    # (this is NOT perfect, arrow keys also trigger)
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_stdin)
                    print (f'{NL}{NL}You pressed [Escape], Goodbye...')
                    sys.exit(0)
                if key == NL:
                    # Enter was pressed
                    break
            time.sleep(0.2)
    finally:
        # change back to original stdin
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_stdin)

    # cleanup the last mess(age) :-)
    print (f'{TTY_EL2}{TTY_UP1}', end=CR)


def parse_args():
    """
    setup and parse program arguments
    eg. args.verbose, args.src, args.dst, etc...
    """
    # define program arguments
    parser = ArgumentParser()
    parser.add_argument('-v', '--verbose', action='count', default=0,
        help='use multiple times for increased verbosity')
    parser.add_argument('--delete-strays', action='store_true',
        help='delete stray destination subvolumes with no matching source')
    parser.add_argument('--dry-run', action='store_true',
        help='simulation mode, no changes made to destination')
    parser.add_argument('--interactive', action='store_true',
        help='run interactively (e.g. from a tty or tmux)')
    parser.add_argument('--ignore-errors', action='store_true',
        help='continue after send/recv errors')
    parser.add_argument('src',
        help='source btrfs path')
    parser.add_argument('dst',
        help='destination btrfs path')

    # normalize src and dst paths (remove trailing slash)
    args = parser.parse_args()
    args.src = os.path.normpath(args.src)
    args.dst = os.path.normpath(args.dst)
    return args


def subvol_is_readonly(subvol_full_path):
    """
    return True if subvol is readonly
    """
    cmd = f'{BTRFS} property get -ts {subvol_full_path} ro'
    cmd_list = shlex.split(cmd)
    result = subprocess.run(
        cmd_list, check=True, capture_output=True, text=True)
    if 'ro=true' in result.stdout.rstrip().lower():
        return True


def get_filesystem_uuid(btrfs_path):
    """
    returns the uuid of the passed btrfs path
    returns None if there was an error
    """
    cmd = f'{BTRFS} filesystem show {btrfs_path}'
    cmd_list = shlex.split(cmd)
    try:
        result = subprocess.run(
            cmd_list, check=True, capture_output=True, text=True)
        re_pattern = r'uuid: (?P<uuid>[-a-f0-9]*)'
        uuid = re.findall(re_pattern, result.stdout, re.MULTILINE)
        return uuid[0]
    except:
        return None


def normalize_uuid(uuid):
    """
    btrfs returns '-' for non-existent uuids
    python logic is easier with an empty string ''
    """
    if uuid == '-':
        return ''
    return uuid


def build_subvols(btrfs_path, readonly=False):
    """
    returns a sorted list of Subvol dataclass (by subvol.id)
    [Subvol_1, Subvol_2, Subvol_3, ...]

    this data is built using the btrfs subvolume list command:
        btrfs subvolume list -qRu /some/btrfs/path
    list command example output:
        ID 11352 gen 2308972 top level 5 parent_uuid <uuid> \
        received_uuid <uuid> uuid <uuid> \
        path computer.name/DDMMYY-HHMM
    """
    if readonly:
        # only list subvolumes that are readonly
        cmd = f'{BTRFS} subvolume list -qRur {btrfs_path}'
    else:
        # list all subvolumes including writable
        cmd = f'{BTRFS} subvolume list -qRu {btrfs_path}'

    cmd_list = shlex.split(cmd)
    result = subprocess.run(
        cmd_list, check=True, capture_output=True, text=True)
    lines = result.stdout.splitlines()

    subvols = []
    for line in lines:
        try:
            id = int(re.findall('^ID (\d+) ', line)[0])
            uuid = re.findall('.*? uuid (.*?) ', line)[0]
            parent_uuid = re.findall('.*? parent_uuid (.*?) ', line)[0]
            received_uuid = re.findall('.*? received_uuid (.*?) ', line)[0]
            rel_path = re.findall('.*? path (.*)$', line)[0]
        except:
            msg = f'Error with subvolume list data: {line}'
            error_handler(msg, format_exc())
        else:
            subvols.append(Subvol(
                id,
                uuid,
                rel_path,
                normalize_uuid(parent_uuid),
                normalize_uuid(received_uuid)))

    return sorted(subvols, key=lambda subvol: subvol.id)


def get_dst_subvol_by_src_subvol(src_subvol, dst_subvols):
    """
    returns a destination subvol (dst_subvol)

    if dst_received_uuid matches (src_uuid or src_received_uuid)
      and (dst_path matches src_path)
      and (dst_received_uuid is not '')
    """
    src_uuids = [src_subvol.uuid, src_subvol.received_uuid]
    # loop through the destination subvols
    # to find a match for the source subvol
    for dst_subvol in dst_subvols:
        dst_received_uuid = dst_subvol.received_uuid
        # is dst received uuid valid (non-empty)?
        if dst_received_uuid:
            # is this dst received uuid in the source?
            if dst_received_uuid in src_uuids:
                # are the relative paths the same?
                if dst_subvol.rel_path == src_subvol.rel_path:
                    return dst_subvol


def get_subvol_rel_path_by_uuid(uuid, subvols):
    # return subvol relative path using uuid
    for subvol in subvols:
        if subvol.uuid == uuid:
            return subvol.rel_path


def get_subvol_orphans(subvols):
    """
    return a list of all subvols with a parent_uuid
    where we can find no matching subvol uuid
    """
    # build a list of all subvol uuids
    uuids = [subvol.uuid for subvol in subvols]

    # check for missing subvol parents to find orphans
    orphans = []
    for subvol in subvols:
        if subvol.parent_uuid:
            if subvol.parent_uuid not in uuids:
                orphans.append(subvol.uuid)

    return orphans


def do_send_receive(src_full_path, dst_full_path, src_parent_full_path=None):
    """
    my complicated send/receive ... instead of using subprocess.run()
    I wanted to capture the stderr pipe from several Popen commands
    while not directly handling the stdin and stdout pipes.
        Why, you ask?
    when I originally tried handling all the pipes between send, pv,
    and receive directly, the cpu usage was way too high.  Eventyally, I
    settled on letting popen pipe stdout directly from send to pv
    to recv.  I found python 3.5+ allows setting non-blocking PIPE reads.
    This allows me to capture pv's output in realtime (+ send/receive errors)
    while using very little cpu resources, at the expense of more complicated
    code.  There may be other ways, but this is what I found that works.
    """
    dst_dir_path = os.path.dirname(dst_full_path)
    # create destination path if needed
    if not os.path.exists(dst_dir_path):
        # destination path does not exist
        log('* destination path does not exist', verbose=1)
        if args.dry_run:
            log(f'  (--dry-run) not creating "{dst_dir_path}"', verbose=1)
        else:
            log(f'  creating "{dst_dir_path}"', verbose=1)
            # create it
            os.makedirs(dst_dir_path)

    # if dry run, nothing more to do here
    if args.dry_run:
        log(f'* (--dry-run) not sending '
            '"{src_full_path}" to "{dst_full_path}"')
        return

    # log our intentions
    log(f'* sending "{src_full_path}" to "{dst_full_path}"')

    # setup send | pv | receive commands
    send_cmd = f'{BTRFS} -q send '
    if src_parent_full_path:
        log(f'  parent  "{src_parent_full_path}"')
        send_cmd += f'-p "{src_parent_full_path}" '
    send_cmd += f'"{src_full_path}"'
    stat_cmd = PV
    recv_cmd = f'{BTRFS} -q receive "{dst_dir_path}"'
    send_cmd_list = shlex.split(send_cmd)
    stat_cmd_list = shlex.split(stat_cmd)
    recv_cmd_list = shlex.split(recv_cmd)

    # shorten verbosity by using (short) alias for PIPE and Popen
    PIPE = subprocess.PIPE
    Popen = subprocess.Popen

    # setup the subprocesses
    send_proc = Popen(send_cmd_list, stdout=PIPE, stderr=PIPE)
    if PV:
        stat_proc = Popen(
            stat_cmd_list, stdin=send_proc.stdout, stdout=PIPE, stderr=PIPE)
        recv_proc = Popen(recv_cmd_list, stdin=stat_proc.stdout, stderr=PIPE)
    else:
        recv_proc = Popen(recv_cmd_list, stdin=send_proc.stdout, stderr=PIPE)

    # time to start the send/pv/receive
    send_proc.stdout.close()

    if PV:
        stat_proc.stdout.close()
        # set PV stderr pipe to non-blocking mode
        os.set_blocking(stat_proc.stderr.fileno(), False)
        line_out = ''
        line_buf = ''
        spaces = ' '*22
        # loop while send/recv to collect pv output
        while recv_proc.poll() is None:
            # try to ensure we get a full line
            time.sleep(0.2)
            data = stat_proc.stderr.readline().decode()
            if data:
                line_buf += data
                # keep adding data until we get one or more CR
                if CR in line_buf:
                    cr_lines = line_buf.split(CR)
                    cr_count = line_buf.count(CR)
                    # extract the last full line
                    line_out = cr_lines[cr_count-1]
                    # keep the rest for the next iteration
                    line_buf = ''.join(cr_lines[cr_count:])
                    if args.interactive:
                        # print spaces + data, clear rest of line
                        print (f'{spaces}{line_out}{TTY_EL0}', end=CR)

        # complete the above print or log pv output
        if line_out:
            if args.interactive:
                # finish the earlier print with a newline
                print ()
            else:
                log(f'  {line_out}')

    (result, errors) = recv_proc.communicate()
    if errors:
        msg = f'send/recv {errors.decode()}'
        error_handler(msg)


def delete_subvol(*paths, countdown=10):
    """
    paths need to be absolute (not relative)
    for subvolumes that should be deleted
    if interactive mode is on, present a countdown to allow
    for program termination
    """
    success = True
    if args.dry_run:
        log('* (--dry-run) NOT deleting the following subvolumes:')
        log(f'  {paths}')
        return success

    log('* Deleting the following stray destination subvolumes:')
    log(f'  {paths}')
    do_countdown(countdown)

    for path in paths:
        cmd = f'{BTRFS} subvolume delete {path}'
        cmd_list = shlex.split(cmd)
        result = subprocess.run(
            cmd_list, check=True, capture_output=True, text=True)
        if result.returncode == 0:
            msg = f'  Successfully deleted "{path}"'
            log(msg, verbose=2)
        else:
            success = False
            msg = f'  Error deleting subvolume "{path}"'
            error_handler(msg, result.stdout, result.stderr)
    return success


def delete_directory(*paths, countdown=10):
    """
    paths need to be absolute (not relative)
    for directories that should be deleted
    if interactive mode is on, present a countdown to allow
    for program termination
    """
    success = True
    if args.dry_run:
        log('* (--dry-run) NOT deleting the following directories:')
        log(f'  {paths}')
        return success

    log('* Deleting the following stray destination directories:')
    log(f'  {paths}')
    do_countdown(countdown)

    for path in paths:
        try:
            os.rmdir(path)
            msg = f'  Successfully deleted "{path}"'
            log(msg, verbose=2)
        except:
            success = False
            msg = f'  Error deleting directory "{path}"'
            error_handler(msg, format_exc())
    return success


def delete_stray_destinations(src_subvols, dst_subvols):
    """
    Delete destination subvolumes and directories
    that are no longer found at the source
    """
    # build a list of source and destination subvol paths
    src_subvol_paths = [subvol.rel_path for subvol in src_subvols]
    dst_subvol_paths = [subvol.rel_path for subvol in dst_subvols]
    # build a list of destination subvol paths with no matching source path
    stray_dst_subvol_paths = list(
        set(dst_subvol_paths) - set(src_subvol_paths))
    # delete the stray destination subvols
    if stray_dst_subvol_paths:
        # convert the list from relative to full paths
        stray_dst_subvols = [
            os.path.join(args.dst, path) for path in stray_dst_subvol_paths]
        success = delete_subvol(*stray_dst_subvols)

    # delete directories in destination which are not in source
    try:
        # build a list of source and destination directories
        # these are basenames only, not full paths (yet)
        src_dirs = [f.name for f in os.scandir(args.src) if f.is_dir()]
        dst_dirs = [f.name for f in os.scandir(args.dst) if f.is_dir()]
    except:
        msg = f'Cannot scan directories: "{args.src}", "{args.dst}"'
        error_handler(msg, format_exc())
    else:
        # check for special RSYNC_DST (should not be deleted)
        rsync_dst_dir = os.path.basename(RSYNC_DST)
        if rsync_dst_dir in dst_dirs:
            dst_dirs.remove(rsync_dst_dir)
        # build a list of destination directories with no matching source
        stray_dst_dirs = list(set(dst_dirs) - set(src_dirs))
        if stray_dst_dirs:
            # convert the list from relative to full paths
            stray_dst_full_dirs = [
                os.path.join(args.dst, dirname) for dirname in stray_dst_dirs]
            # delete all stray destination directories
            success = delete_directory(*stray_dst_full_dirs)


def get_max_column_sizes(*data):
    """
    input data is a tuple or list (or multiples thereof)
    this function returns a tuple of integers.
    the value returned in each position of the return tuple
    will be the longest string length of all the values of the
    input data in that particular position (or column)
    eg. passing: (123,'abc',0,123456), ('a',1,'zz','123')
    will return (3,3,2,6)
    """
    zipped_data = zip(*data)
    sizes = [max(len(str(val)) for val in zd) for zd in zipped_data]
    return tuple(sizes)


def show_stats(src_subvols, dst_subvols):
    """
    shows some simple stats about the copy progress
    """
    src_len = len(src_subvols)
    dst_len = len(dst_subvols)
    src_orphans_len = len(get_subvol_orphans(src_subvols))
    dst_orphans_len = len(get_subvol_orphans(dst_subvols))
    src_parents_len = sum(
        (subvol.parent_uuid != '' for subvol in src_subvols))
    dst_parents_len = sum(
        (subvol.parent_uuid != '' for subvol in dst_subvols))

    # get source and destination filesystem utilization
    src_percent_used = psutil.disk_usage(args.src).percent
    dst_percent_used = psutil.disk_usage(args.dst).percent

    # build our stats data used
    src_stats = (src_len, src_parents_len, src_orphans_len, src_percent_used)
    dst_stats = (dst_len, dst_parents_len, dst_orphans_len, dst_percent_used)

    # find the maximum column sizes
    column_sizes = get_max_column_sizes(src_stats, dst_stats)

    # build our message templates
    template = (
            '({:>%s} subvols,'
            ' {:>%s} parents,'
            ' {:>%s} orphans,'
            ' {:>%s}%% full): ') %column_sizes
    src_msg = '       Source ' + template.format(*src_stats) + args.src
    dst_msg = '  Destination ' + template.format(*dst_stats) + args.dst

    # log the stats
    log ('', src_msg, dst_msg, '')


def get_valid_src_parent_full_path(parent_uuid, src_subvols):
    """
    search for parent uuid in source subvols -- if it exists and is read only
    return the full path of the parent subvol (args.src + parent_rel_path)
    """
    if parent_uuid:
        # can we actually find the parent subvol?
        parent_rel_path = get_subvol_rel_path_by_uuid(
            parent_uuid, src_subvols)
        if parent_rel_path:
            # found parent subvol
            parent_full_path = os.path.join(args.src, parent_rel_path)
            if os.path.exists(parent_full_path):
                # parent subvol still exists on disk
                if subvol_is_readonly(parent_full_path):
                    # parent subvol is ro
                    return parent_full_path


def main():
    log('')
    log('This program copies a UrBackup BTRFS source to a new destination')
    log('')

    # get btrfs filesystem uuid of source and destination
    src_fs_uuid = get_filesystem_uuid(args.src)
    dst_fs_uuid = get_filesystem_uuid(args.dst)

    # show info about the source and destination filesystems
    log(f'     Source BTRFS uuid {src_fs_uuid} path {args.src}', verbose=1)
    log(f'Destination BTRFS uuid {dst_fs_uuid} path {args.dst}', verbose=1)
    log('')

    # a few safety error checks
    if not src_fs_uuid:
        msg = f'could not find BTRFS filesystem uuid for "{args.src}"'
        raise RuntimeError(msg)
    if not dst_fs_uuid:
        msg = f'could not find BTRFS filesystem uuid for "{args.dst}"'
        raise RuntimeError(msg)
    if src_fs_uuid == dst_fs_uuid:
        msg = f'"{args.src}" and "{args.dst}" are the same file system'
        raise RuntimeError(msg)

    # build the list of source and destination subvols (may take a minute)
    log(f'* building subvolumes for {args.src}', verbose=1)
    src_subvols = build_subvols(args.src)
    log(f'* building subvolumes for {args.dst}', verbose=1)
    dst_subvols = build_subvols(args.dst)

    # show a few nice btrfs/urbackup stats
    show_stats(src_subvols, dst_subvols)

    # if interactive, pause before getting started
    do_countdown(20)

    # data dump of source and destination subvol info
    log (src_subvols, verbose=4)
    log (dst_subvols, verbose=4)

    # make a copy of the miscellaneous urbackup (non-subvol) files
    # eg. databases, client symlinks, etc.
    rsync_urbackup_misc()

    # remove stray (old, unused, unwanted, stranded) subvols from dst
    if args.delete_strays:
        delete_stray_destinations(src_subvols, dst_subvols)

    # iterate through the source subvols; copying one at a time
    show_stats_counter = 0
    for src_subvol in src_subvols:
        log (f'  src {src_subvol}', verbose=3)

        # rel_path is the relative path for the subvolume
        src_rel_path = src_subvol.rel_path
        dst_rel_path = src_rel_path

        # full path is the absolute path to the subvolume
        src_full_path = os.path.join(args.src, src_rel_path)
        dst_full_path = os.path.join(args.dst, dst_rel_path)

        if not os.path.exists(src_full_path):
            # skip if source subvol is missing
            # (perhaps removed by urbackup nightly cleanup)
            log (f'* skipping: "{src_full_path}"')
            log ('  [subvol no longer available]')
            continue

        if not subvol_is_readonly(src_full_path):
            # skip if source is not readonly
            # (backup is probably still running)
            log (f'* skipping: "{src_full_path}"')
            log ('  [subvol is not readonly]')
            continue

        dst_subvol = get_dst_subvol_by_src_subvol(src_subvol, dst_subvols)
        if dst_subvol:
            # skip if valid source copy already exists at destination
            # (valid copy already exists)
            log (f'* valid destination "{dst_full_path}"', verbose=2)
            log (f'  src_id={src_subvol.id}, dst_id={dst_subvol.id}', verbose=3)
            log (f'  dst {dst_subvol}', verbose=3)
            continue

        if os.path.exists(dst_full_path):
            # stray destination subvolume exists, needs to be deleted
            # (perhaps an interrupted copy)
            log (f'* stray destination "{dst_full_path}"')
            log ('  [destination received_uuid does not match any source]')
            success = delete_subvol(dst_full_path)

        # find the source parent full path, if it exists and is readonly
        src_parent_full_path = get_valid_src_parent_full_path(
            src_subvol.parent_uuid, src_subvols)

        # time the big show; do the send|receive
        do_send_receive(src_full_path, dst_full_path, src_parent_full_path)

        if args.verbose > 0 and SHOW_STATS_INTERVAL > 0:
            # show stats every SHOW_STATS_INTERVAL
            show_stats_counter += 1
            if show_stats_counter % SHOW_STATS_INTERVAL == 0:
                dst_subvols = build_subvols(args.dst)
                show_stats(src_subvols, dst_subvols)

    # finally, show stats after all source subvols have been processed
    show_stats(src_subvols, dst_subvols)



if __name__ == "__main__":
    try:
        args = parse_args()
        log (str(args), verbose=2)
        main()
    except SystemExit:
        # argparse error
        pass
    except:
        print_exc()

