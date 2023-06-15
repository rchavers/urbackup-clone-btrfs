#! /usr/bin/python3

"""
urbackup-clone-btrfs
  clone a urbackup btrfs file structure
  Copyright (C) 2023 Robert Chavers
  based on btrfs-clone by Martin Wilck
  some snippets of code sourced/inspired by stackexchange contributers
  some snippets of code sourced/inspired by stackoverflow contributers

  required utilities:
    python 3.10+, btrfs-progs, rsync 3.2.3+, ssh, sshfs, fusermount
  optional utilities:
    pv


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
import tempfile
import subprocess

from dataclasses import dataclass
from urllib.parse import urlparse
from argparse import ArgumentParser
from traceback import print_exc, format_exc


"""
    user editable globals
"""

# add needed paths to required utilities here (eg. running via cron)
# (btrfs, rsync, pv, ssh, sshfs, fusermount)
# these paths are added to subprocess.run(env = ...), see NEW_ENV below
utility_paths = ['/usr/bin',]

# RSYNC_DST is the destination copy of RSYC_SRC_LIST source folders
# {src}, {dst} are evaluated to their respective paths
# rsync_src_list trailing slashes will be removed
RSYNC_DST = '{dst}/_urbcb_misc_backups'
RSYNC_SRC_LIST = ['/var/urbackup', '{src}/clients', '{src}/urbackup']

# (optional) pv utility with arguments and output format to use
# set PV = [] if send/recv stats are not wanted or pv is not installed
PV_CMD_LIST = ['pv', '-f', '-F', 'time [%t] -- rate %a -- size [%b]']

# (optional) show filesystem stats after this many send|receive tasks
# set SHOW_STATS_INTERVAL = 0 to disable
SHOW_STATS_INTERVAL = 10


"""
    computed and static globals, dataclasses, and classes
"""

# save environment path in list form
old_paths = os.environ['PATH'].split(':')
# add utility_paths to old_paths, keeps order, deduplicates, converts to str
new_paths = ':'.join(dict.fromkeys(old_paths + utility_paths))
# build NEW_ENV with modified path for use in subprocess.run
NEW_ENV = {**os.environ, 'PATH': new_paths}

# default ssh port
SSH_DEFAULT_PORT = '22'

# vt100 codes and constants
NL = '\n'           # newline
CR = '\r'           # carrage return
KEY_ESC = '\x1b'    # escape key
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

# component parts of source or destination url
@dataclass()
class URL:
    scheme: str = ''    # scheme ('', 'ssh')
    user: str = ''      # username
    host: str = ''      # hostname
    port: str = ''      # port number
    path: str = ''      # path (given)
    sshfs: str = ''     # path (local sshfs mountpoint)
    origin: str = ''    # original passed in argument

# easy way to handle plurals within strings
class plural:
    """
    given:
      msg = 'I found {:N pe/rson/ople} and {:N tree//s}'
    tests:
      msg.format(plural(2), plural(1)) == 'I found 2 people and 1 tree'
      msg.format(plural(1), plural(2)) == 'I found 1 person and 2 trees'
    The format is 'always/singular/plural'
    where singular (then plural) is optional.
    credit: https://stackoverflow.com/a/27642538
    """
    def __init__(self, value):
        self.value = value

    def __format__(self, formatter):
        formatter = formatter.replace("N", str(self.value))
        start, _, suffixes = formatter.partition("/")
        singular, _, plural = suffixes.rpartition("/")
        return "{}{}".format(start, singular if self.value == 1 else plural)


"""
    program function definitions
"""

def log(*messages: str, verbose: int=0) -> None:
    """
    Print output messages to stdout and prepend date and time.
    To log to a file, redirect stdout to a file.
    messages: either a single string or multiple strings
    verbose: prints only if equal or greater than args.verbose
    """
    if args.verbose >= verbose:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for msg in messages:
            print (now, msg, flush=True)


def error_handler(*messages: str) -> None:
    """
    log error messages to stderr (+ stdout if different devices)
    stop program execution if args.ignore_errors is False
    """
    # compare stdout and stderr, are they the same output device?
    out_fileno = sys.stdout.fileno()
    err_fileno = sys.stderr.fileno()
    out_eq_err = (os.fstat(out_fileno) == os.fstat(err_fileno))

    # start output with current date and time
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # print one timestamp line
    print (now, 'Error:', file=sys.stderr)
    # only print to stdout if different output than stderr
    if not out_eq_err:
        print (now, 'Error:')

    for msg in messages:
        print (msg, file=sys.stderr, flush=True)
        # only print to stdout if different output than stderr
        if not out_eq_err:
            print (msg, flush=True)

    # ignore errors, or quit?
    if args.ignore_errors:
        log('* (--ignore-errors) Continuing...')
    else:
        sys.exit(1)


def do_countdown(seconds: float) -> None:
    """
    if we are in interactive mode, then present a
    countdown prompt for the number of seconds.
    the user can press the enter key to continue,
    or the escape key to exit
    """
    if not args.interactive:
        # no need for countdown in non-interactive mode
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
                if key == KEY_ESC:
                    # ESCape was pressed
                    # (this is NOT perfect, arrow keys also trigger)
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_stdin)
                    print (f'{NL}{NL}You pressed [Escape]')
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


def parse_url(origin: str) -> URL:
    """
    returns a parsed URL dataclass from a url string
      embeded passwords will not work. use ssh keys instead
      (eg. ssh://user:secretpass@host/path) will not work
    URL is (scheme,user,host,port,path,sshfs,origin)
     if user is not specified, user=(current process username)
     if host is not specified, host='localhost'
     if port is not specified, port=SSH_DEFAULT_PORT
     sshfs is a path, but it is used later and not needed here
    local url example:
      /mnt/backup/urbackup
    ssh url example:
      ssh://[[user@]host[:port]]/remote/path
    """
    scheme, user, host, port, path, sshfs = [''] * 6
    parsed = urlparse(origin)
    if parsed.scheme == 'ssh':
        # found ssh url, setup URL vars
        scheme  = 'ssh'
        user = (parsed.username or psutil.Process().username())
        host = (parsed.hostname or 'localhost')
        port = str(parsed.port or SSH_DEFAULT_PORT)
        path = os.path.normpath(parsed.path)
    else:
        # default to a local file url
        path = os.path.normpath(origin)
    return URL(scheme, user, host, port, path, sshfs, origin)


def parse_args() -> ArgumentParser:
    """
    setup and parse program arguments
    eg. args.verbose, args.src, args.dst, etc...
    """
    # define program arguments
    desc = 'This program copies a UrBackup BTRFS'
    desc += ' backup source to another destination'
    epilog = 'url can either be local or ssh'
    epilog += ' :: Local example /path/to/mountpoint'
    epilog += ' :: SSH example ssh://[[user@]host[:port]]/remote/path'
    parser = ArgumentParser(description=desc, epilog=epilog)
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
        help='source (local btrfs mountpoint or remote ssh url)')
    parser.add_argument('dst',
        help='destination (local btrfs mountpoint or remote ssh url)')

    # parse arguments
    args = parser.parse_args()

    # remove all trailing '/' from src and dst
    # can't use normpath if using non-local schemes. eg. ssh://
    # rstrip would leave empty if src or dst is rootdir ('/')
    src_origin = (args.src.rstrip('/') or '/')
    dst_origin = (args.dst.rstrip('/') or '/')

    # parse the src and dst as URL class w/ origin
    # (only local and ssh for now)
    args.src = parse_url(src_origin)
    args.dst = parse_url(dst_origin)

    return args


def is_ssh(url: URL) -> bool:
    """
    return True if url.scheme is ssh
    """
    return (url.scheme == 'ssh')


def build_remote_cmd(cmd: list, url: URL=None) -> list:
    """
    returns a cmd_list suitable for subprocess.
    if ssh, then prepend the ssh connection,
    otherwise, just return the original cmd
    """
    if url and is_ssh(url):
        ssh = ['ssh', '-p', url.port, f'{url.user}@{url.host}']
        cmd_list = ssh + [shlex.join(cmd)]
        return cmd_list
    else:
        return cmd


def run_cmd(cmd: list, url: URL=None,
    dryrun: bool=False, newsession: bool=False) -> tuple[int,str,str]:
    """
    return result from cmd using subprocess.run
    url can be local or remote (ssh)
    """
    if dryrun:
        log(f'  (--dry-run) NOT running: {shlex.join(cmd)}')
        cmd_list = ['echo', '(--dry-run) noop']
    else:
        cmd_list = build_remote_cmd(cmd, url)

    try:
        result = subprocess.run(cmd_list,
            env=NEW_ENV, start_new_session=newsession,
            check=True, capture_output=True, text=True)
        ret = result.returncode
        out = result.stdout
        err = result.stderr
    except subprocess.CalledProcessError as error:
        ret = error.returncode
        out = error.stdout
        err = error.stderr
    except:
        msg = 'Error running command: "%s"' %shlex.join(cmd_list)
        error_handler(msg, format_exc())
    return (ret, out, err)


def subvol_is_readonly(url: URL, rel_path: str) -> bool:
    """
    return True if subvol is readonly
    """
    full_path = os.path.join(url.path, rel_path)
    cmd = ['btrfs', 'property', 'get', '-ts', full_path, 'ro']
    ret, out, err = run_cmd(cmd, url=url)
    return ('ro=true' in out.lower())


def get_filesystem_uuid(url: URL) -> str:
    """
    returns the uuid of the passed btrfs url
    returns empty string '' if missing uuid or an error
    """
    cmd = ['btrfs', 'filesystem', 'show', url.path]
    ret, out, err = run_cmd(cmd, url=url)
    pattern = r'uuid: (?P<uuid>[-a-f0-9]*)'
    try:
        uuid = re.findall(pattern, out, re.MULTILINE)[0]
        return uuid
    except:
        return ''


def normalize_uuid(uuid: str) -> str:
    """
    btrfs returns '-' for non-existent uuids
    python logic is easier with an empty string ''
    """
    return uuid if uuid != '-' else ''


def build_subvols(url: URL, readonly: bool=False) -> list[Subvol]:
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
        cmd = ['btrfs', 'subvolume', 'list', '-qRur', url.path]
    else:
        # list all subvolumes including writable
        cmd = ['btrfs', 'subvolume', 'list', '-qRu', url.path]

    ret, out, err = run_cmd(cmd, url=url)
    lines = out.splitlines()

    subvols = []
    # parse the result and extract subvol info
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
                id, uuid, rel_path,
                normalize_uuid(parent_uuid),
                normalize_uuid(received_uuid)))

    # return a sorted list of subvols (already sotred, I know)
    return sorted(subvols, key=lambda subvol: subvol.id)


def get_dst_subvol_by_src_subvol(src_subvol: Subvol,
    dst_subvols: list[Subvol]) -> Subvol:
    """
    returns a destination subvol (dst_subvol)
    if dst_received_uuid is not empty ('')
      and dst_received_uuid matches (src_uuid or src_received_uuid)
      and dst_rel_path matches src_rel_path
    """
    src_uuids = [src_subvol.uuid, src_subvol.received_uuid]
    # loop through the destination subvols
    for dst_subvol in dst_subvols:
        dst_received_uuid = dst_subvol.received_uuid
        # is dst received uuid valid (non-empty)?
        if dst_received_uuid:
            # is this dst received uuid in the source?
            if dst_received_uuid in src_uuids:
                # are the relative paths the same?
                if dst_subvol.rel_path == src_subvol.rel_path:
                    return dst_subvol


def get_subvol_orphans(subvols: list[Subvol]) -> list[str]:
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


def makedirs_if_missing(path: str) -> None:
    """
    create path if it does not exist
    """
    if not os.path.exists(path):
        if args.dry_run:
            log(f'  (--dry-run) NOT creating "{path}"', verbose=1)
        else:
            log(f'  creating "{path}"', verbose=1)
            os.makedirs(path)


def do_send_receive(src_rel_path: str, dst_rel_path: str,
    src_parent_rel_path: str=None) -> None:
    """
    my complicated send/receive ... instead of using subprocess.run()
    I wanted to capture the stderr pipe from several Popen commands
    while not directly handling the stdin and stdout pipes.
        Why, you ask?
    when I originally tried handling all the pipes between send, pv,
    and receive directly, the cpu usage was way too high.  Eventually, I
    settled on letting popen pipe stdout directly from send to pv
    to recv.  I found python 3.5+ allows setting non-blocking PIPE reads.
    This allows me to capture pv's output in realtime (+ send/receive errors)
    while using very little cpu resources, at the expense of more complicated
    code.  There may be other ways, but this is what I found that works.
    """
    # these are only used for logging
    log_src_path = os.path.join(args.src.origin, src_rel_path)
    log_dst_path = os.path.join(args.dst.origin, dst_rel_path)

    # log our intentions
    log(f'* sending "{log_src_path}" to "{log_dst_path}"')
    if src_parent_rel_path:
        log_src_parent_path = os.path.join(
            args.src.origin, src_parent_rel_path)
        log(f'  +parent "{log_src_parent_path}"')

    # if dry run, nothing more to do here
    if args.dry_run:
        log('  (--dry-run) NOT sending')
        return

    # make directory dst_dir_path if missing
    # uses .sshfs or local path as needed
    dst_dir_path = os.path.dirname(os.path.join(
        (args.dst.sshfs or args.dst.path), dst_rel_path))
    makedirs_if_missing(dst_dir_path)

    # determine send_path, recv_path, and recv_dirname
    # send/recv might use ssh, so no sshfs needed here
    send_path = os.path.join(args.src.path, src_rel_path)
    recv_path = os.path.join(args.dst.path, dst_rel_path)
    recv_dirname = os.path.dirname(recv_path)

    # setup send and receive commands
    send_cmd = ['btrfs', '-q', 'send']
    if src_parent_rel_path:
        send_parent_path = os.path.join(
            args.src.path, src_parent_rel_path)
        send_cmd += ['-p', send_parent_path]
    send_cmd += [send_path]
    recv_cmd = ['btrfs', '-q', 'receive', recv_dirname]

    # send or recv might use ssh, so build as necessary
    send_cmd_list = build_remote_cmd(send_cmd, args.src)
    recv_cmd_list = build_remote_cmd(recv_cmd, args.dst)

    # shorten verbosity by using alias for PIPE and Popen
    PIPE = subprocess.PIPE
    Popen = subprocess.Popen

    # setup the send/stat/recv processes
    send_proc = Popen(send_cmd_list, stdout=PIPE, stderr=PIPE)
    if PV_CMD_LIST:
        # insert PV between send/recv
        stat_proc = Popen(
            PV_CMD_LIST, stdin=send_proc.stdout, stdout=PIPE, stderr=PIPE)
        recv_proc = Popen(recv_cmd_list, stdin=stat_proc.stdout, stderr=PIPE)
    else:
        # no PV, connect recv directly to send
        recv_proc = Popen(recv_cmd_list, stdin=send_proc.stdout, stderr=PIPE)

    # time to start the send/pv/receive
    send_proc.stdout.close()
    if PV_CMD_LIST:
        stat_proc.stdout.close()
        # set PV stderr pipe to non-blocking mode (requires python 3.5+)
        os.set_blocking(stat_proc.stderr.fileno(), False)
        line_out = ''
        line_buf = ''
        spaces = ' '*22
        # loop while send/recv to collect pv output
        while recv_proc.poll() is None:
            time.sleep(0.5)  # be kind to the cpu
            data = stat_proc.stderr.readline().decode()
            if data:
                line_buf += data
                # keep adding data until we get a CR
                if CR in line_buf:
                    cr_count = line_buf.count(CR)
                    cr_lines = line_buf.split(CR)
                    # extract the last full line
                    line_out = cr_lines[cr_count-1]
                    # buffer the remaining data
                    line_buf = cr_lines[cr_count]
                    if args.interactive:
                        # print spaces + stats, clear rest of line
                        print (f'{spaces}{line_out}{TTY_EL0}', end=CR)

        # finish up; either print newline, or log line_out
        if line_out:
            if args.interactive:
                print ()
            else:
                log(f'  {line_out}')

    # handle errors
    (result, errors) = recv_proc.communicate()
    if errors:
        msg = f'send/recv {errors.decode()}'
        error_handler(msg)


def delete_dst_subvols(*paths: list[str], countdown: int=10) -> bool:
    """
    paths need to be relative to args.dst.path
    for subvolumes that should be deleted
    if interactive mode, present a countdown
    to allow for program termination
    """
    success = True
    num_paths = len(paths)
    msg = '* Deleting {} stray destination {:subvolume/s} from {}'
    log(msg.format(num_paths, plural(num_paths), args.dst.origin))
    if args.interactive and not args.dry_run:
        do_countdown(countdown)

    for path in paths:
        dst_full_path = os.path.join(args.dst.path, path)
        cmd = ['btrfs','subvolume','delete',dst_full_path]
        ret, out, err = run_cmd(cmd, url=args.dst, dryrun=args.dry_run)
        if not args.dry_run:
            if ret == 0:
                msg = f'  Successfully deleted "{args.dst.origin}/{path}"'
                log(msg, verbose=2)
            else:
                success = False
                msg = f'  Error deleting subvolume "{args.dst.origin}/{path}"'
                error_handler(msg, out, err)
    return success


def delete_dst_directory(*paths: list[str], countdown: int=10) -> bool:
    """
    paths need to be relative to args.dst.(sshfs or path)
    for directories that should be deleted
    if interactive mode is on, present a countdown to allow
    for program termination
    """
    success = True
    num_paths = len(paths)
    msg = '* Deleting {} stray destination {:director/y/ies} from {}'
    log(msg.format(num_paths, plural(num_paths), args.dst.origin))
    if args.interactive and not args.dry_run:
        do_countdown(countdown)

    for path in paths:
        dst_dir = (args.dst.sshfs or args.dst.path)
        dst_full_path = os.path.join(dst_dir, path)
        if args.dry_run:
            log(f'  (--dry-run) NOT running: os.rmdir({dst_full_path})')
            continue
        try:
            os.rmdir(dst_full_path)
        except:
            success = False
            msg = f'  Error deleting directory "{dst_full_path}"'
            error_handler(msg, format_exc())
        else:
            msg = f'  Successfully deleted "{dst_full_path}"'
            log(msg, verbose=2)
    return success


def delete_stray_destinations(src_subvols: list[Subvol],
    dst_subvols: list[Subvol]) -> None:
    """
    Delete destination subvolumes and directories
    that are no longer found at the source
    """
    # build a list of source and destination subvol paths
    src_subvol_paths = [s.rel_path for s in src_subvols]
    dst_subvol_paths = [s.rel_path for s in dst_subvols]
    # build a list of destination subvol paths with no matching source path
    stray_dst_subvol_paths = list(
        set(dst_subvol_paths) - set(src_subvol_paths))
    # delete the stray destination subvols
    if stray_dst_subvol_paths:
        success = delete_dst_subvols(*stray_dst_subvol_paths)

    # delete directories in destination which are not in source
    src_dir = (args.src.sshfs or args.src.path)
    dst_dir = (args.dst.sshfs or args.dst.path)
    try:
        # build a list of source and destination directories
        # these are basenames only, not full paths
        src_dirs = [f.name for f in os.scandir(src_dir) if f.is_dir()]
        dst_dirs = [f.name for f in os.scandir(dst_dir) if f.is_dir()]
    except:
        msg = 'Cannot scan directories: '
        msg += f'"{args.src.origin}", "{args.dst.origin}"'
        error_handler(msg, format_exc())
    else:
        # check for special RSYNC_DST (should not be deleted)
        rsync_dst_dir = os.path.basename(RSYNC_DST)
        if rsync_dst_dir in dst_dirs:
            dst_dirs.remove(rsync_dst_dir)
        # build a list of destination directories with no matching source
        stray_dst_dirs = list(set(dst_dirs) - set(src_dirs))
        if stray_dst_dirs:
            # delete all stray destination directories
            success = delete_dst_directory(*stray_dst_dirs)


def get_max_column_sizes(*data: any) -> tuple[int]:
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
    sizes = [max(len(str(v)) for v in d) for d in zipped_data]
    return tuple(sizes)


def show_stats(src_subvols: list[Subvol], dst_subvols: list[Subvol]) -> None:
    """
    shows some simple stats about the copy progress
    """
    src_len = len(src_subvols)
    dst_len = len(dst_subvols)
    src_parents_len = sum((s.parent_uuid != '' for s in src_subvols))
    dst_parents_len = sum((s.parent_uuid != '' for s in dst_subvols))
    src_orphans_len = len(get_subvol_orphans(src_subvols))
    dst_orphans_len = len(get_subvol_orphans(dst_subvols))

    # get source and destination filesystem utilization
    src_dir = (args.src.sshfs or args.src.path)
    dst_dir = (args.dst.sshfs or args.dst.path)
    src_percent_used = psutil.disk_usage(src_dir).percent
    dst_percent_used = psutil.disk_usage(dst_dir).percent

    # build stats data
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
    src_msg = '       Source ' + template.format(*src_stats) + args.src.origin
    dst_msg = '  Destination ' + template.format(*dst_stats) + args.dst.origin

    # log the stats
    log ('', src_msg, dst_msg, '')


def get_subvol_rel_path_by_uuid(uuid: str, subvols: list[Subvol]) -> str:
    # return subvol relative path using uuid
    for subvol in subvols:
        if subvol.uuid == uuid:
            return subvol.rel_path


def get_valid_src_parent_rel_path(parent_uuid: str,
    src_subvols: list[Subvol]) -> str:
    """
    search for parent uuid in source subvols
    subvol is vaild if it exists and is readonly
    return the rel_path of the parent subvol
    """
    if parent_uuid:
        # can we actually find the parent subvol?
        parent_rel_path = get_subvol_rel_path_by_uuid(
            parent_uuid, src_subvols)
        if parent_rel_path:
            # found parent subvol
            src_dir = (args.src.sshfs or args.src.path)
            parent_full_path = os.path.join(src_dir, parent_rel_path)
            if os.path.exists(parent_full_path):
                # parent subvol still exists on disk
                if subvol_is_readonly(args.src, parent_rel_path):
                    return parent_rel_path


def sshfs_mount(url: URL, desc: str) -> str:
    """
    mount the remote btrfs using sshfs
    url = remote filesystem, desc = description (either 'src' or 'dst')
    running filesystem commands via sshfs (ie. os.path.exists)
    is *much* faster than ssh via subprocess.run or paramiko
    example to mount sshfs:
    sshfs root@remote:/mnt/offsite/urbackup/ /tmp/_urbcb_dst_1685243777_
    NOTE: sshfs needs to run in a new session, or we hang on exit
    """
    # integer epoc seconds, for human readable use: time.ctime(iesec)
    iesec = int(time.time())
    sshfs = os.path.join(tempfile.gettempdir(), f'_urbcb_{desc}_{iesec}_')
    try:
        # make the temp directory and mount the remote url
        os.mkdir(sshfs)
    except:
        msg = f'could not create sshfs mountpoint: {sshfs}'
        error_handler(msg, format_exc())
        sys.exit(1)

    # use sshfs to mount the remote url at the temp directory
    remote = f'{url.user}@{url.host}:{url.path}'
    cmd = ['sshfs', '-p', url.port, remote, sshfs]
    ret, out, err = run_cmd(cmd, newsession=True)
    if ret != 0:
        msg = f'could not mount sshfs: {cmd}'
        error_handler(msg, out, err)
        try:
            os.rmdir(sshfs)
        except:
            pass
        sys.exit(1)

    log (f'* created sshfs mountpoint: {shlex.join(cmd)}')
    return sshfs


def rsync_copy_misc() -> bool:
    """
    use rsync to copy the urbackup database and other miscellany:
        /var/urbackup, {src}/urbackup,  {src}/clients
        (database    , backup database, client symlinks)
    """
    success = True
    log('* Using rsync to backup databases and client symlinks')

    # normalize and format the rsync destination path
    rsync_dst_path = os.path.normpath(
        RSYNC_DST.format(dst=args.dst.path))

    for rsync_src in RSYNC_SRC_LIST:
        # normalize and format each rsync source path
        rsync_src_path = os.path.normpath(
            rsync_src.format(src=args.src.path))

        # build the rsync command
        cmd = ['rsync', '-a', '--mkpath', '--delete', '--relative']
        cmd_src = rsync_src_path
        cmd_dst = rsync_dst_path
        if is_ssh(args.src):
            cmd += ['-e', f'ssh -p {args.src.port}']
            cmd_src = f'{args.src.user}@{args.src.host}:{rsync_src_path}'
        if is_ssh(args.dst):
            cmd += ['-e', f'ssh -p {args.dst.port}']
            cmd_dst = f'{args.dst.user}@{args.dst.host}:{rsync_dst_path}'
        cmd += [cmd_src, cmd_dst]

        # run rsync and log any errors
        ret, out, err = run_cmd(cmd, dryrun=args.dry_run)
        if not args.dry_run:
            if ret == 0:
                msg = f'  Successfully copied {cmd_src} to {cmd_dst}'
                log(msg, verbose=1)
            else:
                success = False
                msg = f'  Error copying {cmd_src} to {cmd_dst}'
                error_handler(msg, out, err)
    return success


def main(args: ArgumentParser) -> None:
    msg = 'This program copies a UrBackup BTRFS '
    msg += 'backup source to another destination'
    log('', msg, '')

    # get btrfs filesystem uuid of source and destination
    src_fs_uuid = get_filesystem_uuid(args.src)
    dst_fs_uuid = get_filesystem_uuid(args.dst)

    # show info about the source and destination filesystems
    log(f'     Source BTRFS uuid {src_fs_uuid} path {args.src.origin}',
        verbose=1)
    log(f'Destination BTRFS uuid {dst_fs_uuid} path {args.dst.origin}',
        verbose=1)
    log('')

    # a few safety error checks
    if is_ssh(args.src) and is_ssh(args.dst):
        msg = 'The source and destination cannot both be remote.'
        raise RuntimeError(msg)
    if not src_fs_uuid:
        msg = f'Could not find BTRFS filesystem uuid for "{args.src.origin}"'
        raise RuntimeError(msg)
    if not dst_fs_uuid:
        msg = f'Could not find BTRFS filesystem uuid for "{args.dst.origin}"'
        raise RuntimeError(msg)
    if src_fs_uuid == dst_fs_uuid:
        msg = f'"{args.src.origin}" and "{args.dst.origin}" '
        msg += 'are the same file system'
        raise RuntimeError(msg)

    # build the list of source and destination subvols (may take a minute)
    log(f'* building subvolumes for {args.src.origin}', verbose=1)
    src_subvols = build_subvols(args.src, readonly=True)
    log(f'* building subvolumes for {args.dst.origin}', verbose=1)
    dst_subvols = build_subvols(args.dst)

    # show a few nice btrfs/urbackup stats
    show_stats(src_subvols, dst_subvols)

    # if interactive, pause before getting started
    do_countdown(20)

    # data dump of source and destination subvol info
    log (src_subvols, verbose=4)
    log (dst_subvols, verbose=4)

    # if ssh, setup sshfs
    # much faster than using subprocess.run for every operation
    if is_ssh(args.src):
        args.src.sshfs = sshfs_mount(args.src, 'src')
    elif is_ssh(args.dst):
        args.dst.sshfs = sshfs_mount(args.dst, 'dst')

    # make a copy of the miscellaneous urbackup (non-subvol) files
    # eg. databases, client symlinks, etc.
    rsync_copy_misc()

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

        # determine the correct path (either sshfs or local)
        src_dir = (args.src.sshfs or args.src.path)
        dst_dir = (args.dst.sshfs or args.dst.path)

        # full path is the absolute path to the subvolume
        src_full_path = os.path.join(src_dir, src_rel_path)
        dst_full_path = os.path.join(dst_dir, dst_rel_path)

        if not os.path.exists(src_full_path):
            # skip if source subvol is missing
            # (perhaps removed by urbackup nightly cleanup)
            log (f'* skipping: "{src_rel_path}"')
            log ('  [subvol no longer available]')
            continue

        dst_subvol = get_dst_subvol_by_src_subvol(src_subvol, dst_subvols)
        if dst_subvol:
            # skip if valid source copy already exists at destination
            # (valid copy already exists)
            log (f'* valid destination "{dst_rel_path}"', verbose=2)
            msg = f'  src_id={src_subvol.id}, dst_id={dst_subvol.id}'
            msg += f' dst {dst_subvol}'
            log (msg, verbose=3)
            continue

        if os.path.exists(dst_full_path):
            # stray destination subvolume exists, needs to be deleted
            # (perhaps an interrupted copy)
            success = delete_dst_subvols(dst_rel_path)

        # find the source parent relative path, if it exists and is readonly
        src_parent_rel_path = get_valid_src_parent_rel_path(
            src_subvol.parent_uuid, src_subvols)

        # time for the big show; execute send|receive
        do_send_receive(src_rel_path, dst_rel_path, src_parent_rel_path)

        if args.verbose > 0 and SHOW_STATS_INTERVAL > 0:
            # show stats every SHOW_STATS_INTERVAL
            show_stats_counter += 1
            if show_stats_counter % SHOW_STATS_INTERVAL == 0:
                if not args.dry_run:
                    # only need to reload when dst changes
                    dst_subvols = build_subvols(args.dst)
                show_stats(src_subvols, dst_subvols)

    # finally, show stats after all source subvols have been processed
    show_stats(src_subvols, dst_subvols)


def exit_handler(args: ArgumentParser) -> None:
    log ('* exit_handler...')
    time.sleep(1)
    # find the remote sshfs (both can't be remote)
    sshfs = (args.src.sshfs or args.dst.sshfs)
    # unmount if mounted
    if os.path.ismount(sshfs):
        log (f'  umount sshfs tempdir {sshfs}')
        time.sleep(1)
        cmd = ['fusermount', '-u', sshfs]
        ret, out, err = run_cmd(cmd)
        if ret != 0:
            log (f'Error, could not unmount {sshfs}', out, err)
    # remove tempdir if it exists
    if os.path.exists(sshfs):
        log (f'  remove sshfs tempdir {sshfs}')
        time.sleep(1)
        try:
            os.rmdir(sshfs)
        except:
            print_exc()


if __name__ == "__main__":
    # variables assigned here can be referenced in all functions
    try:
        # parse program arguments
        args = parse_args()
        log (str(args), verbose=2)
    except SystemExit:
        # argparse error, or help (-h) was requested
        sys.exit()
    except:
        print_exc()

    try:
        main(args)
    except SystemExit:
        # catch sys.exit()
        pass
    except KeyboardInterrupt:
        # erase current line and show message
        print (TTY_EL2)
        log ('* User pressed [Ctrl-c]')
    except:
        # print the exception
        print_exc()
    finally:
        # clean up prior to exit
        exit_handler(args)
        log ('* Finished... Goodbye.')
