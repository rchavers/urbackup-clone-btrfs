# urbackup-clone-btrfs.py
clone UrBackup BTRFS snapshots to another BTRFS filesystem -- Backup the backup :-)

This script will use btrfs send and receive to make a copy (not technically a clone since the uuids are different) of a working UrBackup folder structure.  This program can currently only copy to another local filesystem.  I did not need to have remote (for instance, via ssh) capabilities, however, it can be used with multiple destination btrfs filesystems.  For example, I have a helper cron script that will mount multiple LUKS encrypted btrfs disks that are rotated offsite as needed.

### Requirements:
* Have UrBackup installed using the btrfs filesystem https://www.urbackup.org/
* Python v3.5+ installed https://www.python.org/
* Storage system large enough to hold a copy of your current backups

### Usage:
<pre>
urbackup-clone-btrfs.py [-h] [-v] [--delete-strays] [--dry-run] [--interactive] [--ignore-errors] src dst
</pre>

positional arguments:
  * src              source btrfs path
  * dst              destination btrfs path

options:
  * -h, --help       show this help message and exit
  * -v, --verbose    use multiple times for increased verbosity
  * --delete-strays  delete stray destination subvolumes with no matching source
  * --dry-run        simulation mode, no changes made to destination
  * --interactive    run interactively (e.g. from a tty or tmux)
  * --ignore-errors  continue after send/recv errors

### Edit global variables (if needed) before running:
<pre>
# (optional) show filesystem stats after this many send|receive tasks
# set SHOW_STATS_INTERVAL = 0 to disable
SHOW_STATS_INTERVAL = 10

# (optional) path to pv utility and the output format to use
# set PV = '' if send/recv stats are not wanted or pv is not installed
PV = '/usr/bin/pv -f -F "time [%t] -- rate %a -- size [%b]"'

# (required) path to btrfs utility
BTRFS = '/usr/bin/btrfs'

# (required) path to rsync utility
RSYNC = '/usr/bin/rsync'

# RSYNC_DST will contain RSYC_SRC_LIST folders (databases, symlinks, etc)
# {src}, {dst} are evaluated to their respective args
# rsync_src_list trailing slashes will be removed
RSYNC_DST = '{dst}/zz.misc.backups'
RSYNC_SRC_LIST = ['/var/urbackup', '{src}/clients', '{src}/urbackup']
</pre>

