# urbackup-clone-btrfs.py
clone UrBackup BTRFS snapshots to another BTRFS filesystem -- Backup the backup :-)

This script will use btrfs send and receive to make a copy (not technically a clone since the uuids are different) of a working UrBackup folder structure.  This program can currently only copy to another local filesystem.  I did not need to have remote (for instance, via ssh) capabilities, however, it can be used with multiple destination btrfs filesystems.  For example, I have a helper cron script that will mount multiple LUKS encrypted btrfs disks that are rotated offsite as needed.

Since the program can compare each subvolume on the the source and destination, it can safely be stopped any time.  The next time it runs, if it finds an interrupted send/receive, it will delete the unfinished subvolume copy and start it over.  However, the program does NOT resume in the middle of a send/receive.  This is not a problem for my needs, as even my biggest image copies are only a few hundred gigabytes, so the entire copy takes less than an hour anyway.

### Requirements:
* UrBackup installed using the btrfs filesystem https://www.urbackup.org/
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

### Notes:
My understanding of the UrBackup file structure is as follows (please correct me if my knowledge is faulty):
* UrBackup stores the active databases and various support files in /var/urbackup
* UrBackup stores backup copies of the databases, etc. see: "Backup storage path" in settings. (eg. /mnt/backups/urbackup)
* UrBackup stores a client's most recent file backup (not image backup) as symlinks in /mnt/backups/urbackup/clients
* The "Backup storage path" is a btrfs filesystem:
  * However, the client folders directly under that path are not subvolumes, but are instead regular directories
  * The actual backup folders are subvolumes

### FAQ:
#### Why create this, why not simply use program x, y or z?
I tried several other btrfs clone/backup/copy programs, but none of them worked exactly how I wanted.  Most requrired a snapshot at the main btrfs filesystem level.  That is to say, I would have needed to keep a snapshot of /mnt/backups/urbackup/.  Then, you need to clone that snapshot.  This approach is *Much* easier than writing this script.  However, if that snapshot was removed, I would no longer be able to keep my offiste disks in sync without start over from scratch.  With 40TB of data, that takes a really long time!  As my backups grow, that snapshot needs to stay around and the storage deltas from that snapshot can't easily be released without starting a new copy.
#### How does this program solve the snapshot issue above?
urbackup-clone-btrfs.py does not need a urbackup main subvolume snapshot to work.  It simply re-creates the file structure and keeps the source and destination in sync using the original parent/child relationship information as defined by the btrfs subvolume list command.  It will iterate through each subvolume and check if a valid copy already exists, if not, call btrfs send and recieve.


