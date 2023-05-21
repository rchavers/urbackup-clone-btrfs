# urbackup-clone-btrfs.py
clone UrBackup BTRFS subvolumes to another BTRFS filesystem -- Backup the backup :-)

This script uses btrfs send and receive to make a copy (not technically a clone since the uuids are different) of a working UrBackup folder structure.  This program can currently only copy to another local filesystem (that can be later taken offsite).  I did not need it to have remote capabilities, however, it can be used with multiple destination btrfs filesystems.  For example, I have a helper cron script that will mount multiple LUKS encrypted btrfs raid volumes that are rotated offsite as needed.

Since the program will compare each subvolume on the the source and destination, it can safely be stopped any time.  The next time it runs, if it finds an interrupted send/receive, it will delete the unfinished subvolume copy and start it over.  However, the program does NOT resume the send/receive where it left off.  This is not a problem for my needs, as even my biggest backup images are only a few hundred gigabytes, so the entire copy takes less than an hour.  If your images are several terabytes, you may not want to interrupt the backup.  Feel free to help update the code, if resuming send/receive is needed.

In my experience, btrfs send/receive can be fairly slow; especially when UrBackup has several incremental parent subvolumes.  However, this program was not noticeably slower than using any of the other methods I tried.  For my initial "clones", I used tmux and used the --interactive argument to see realtime send/receive stats and to keep an eye on disk usage.  

In my case, I needed to use compression on the destination btrfs filesytem as originally the data did not completely fit without it.  Thus far, I have had no issues with compression enabled.  I have successfully used this script with over 3500 UrBackup subvolumes, close to 30TB of source data, spanning several offsite copies; YMMV.

&nbsp;

### Requirements:
* Linux or a Linux-like OS with btrfs-progs installed (tested with Ubuntu 22.04)
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

### Output example:
_Client names, uuids, and subvolumes have been changed to protect the innocent_
<pre>
2023-05-20 07:03:02 
2023-05-20 07:03:02 This program copies a UrBackup BTRFS source to a new destination
2023-05-20 07:03:02 
2023-05-20 07:03:02      Source BTRFS uuid 5dfc6e64-f75f-11ed-8792-3988dc35b6cc path /mnt/backups/urbackup
2023-05-20 07:03:02 Destination BTRFS uuid 98b28c07-0f2a-4acb-97a5-21cacf646c6c path /mnt/offsite-urbackup
2023-05-20 07:03:02 
2023-05-20 07:03:02 * building subvolumes for /mnt/backups/urbackup
2023-05-20 07:03:03 * building subvolumes for /mnt/offsite-urbackup
2023-05-20 07:03:05 
2023-05-20 07:03:05        Source (3459 subvols, 2427 parents, 123 orphans, 72.0% full): /mnt/backups/urbackup
2023-05-20 07:03:05   Destination (3447 subvols, 2306 parents,  10 orphans, 76.2% full): /mnt/offsite-urbackup
2023-05-20 07:03:05 
2023-05-20 07:03:05 * Using rsync to backup databases and client symlinks
2023-05-20 07:03:05   executing: /usr/bin/rsync -a --delete --relative "/var/urbackup" "/mnt/offsite-urbackup/zz.misc.backups"
2023-05-20 07:06:45   Successfully copied "/var/urbackup" to "/mnt/offsite-urbackup/zz.misc.backups"
2023-05-20 07:06:45   executing: /usr/bin/rsync -a --delete --relative "/mnt/backups/urbackup/clients" "/mnt/offsite-urbackup/zz.misc.backups"
2023-05-20 07:06:45   Successfully copied "/mnt/backups/urbackup/clients" to "/mnt/offsite-urbackup/zz.misc.backups"
2023-05-20 07:06:45   executing: /usr/bin/rsync -a --delete --relative "/mnt/backups/urbackup/urbackup" "/mnt/offsite-urbackup/zz.misc.backups"
2023-05-20 07:09:59   Successfully copied "/mnt/backups/urbackup/urbackup" to "/mnt/offsite-urbackup/zz.misc.backups"
2023-05-20 07:09:59 * Deleting the following stray destination subvolumes:
2023-05-20 07:09:59   ('/mnt/offsite-urbackup/client1/230217-2353_Image_SYSVOL', '/mnt/offsite-urbackup/client2/230519-0653_Image_ESP', '/mnt/offsite-urbackup/client3/230515-0852_Image_SYSVOL', '/mnt/offsite-urbackup/client2/230519-0649_Image_SYSVOL', '/mnt/offsite-urbackup/client1/230217-2359_Image_C', '/mnt/offsite-urbackup/client3/230515-0855_Image_ESP')
2023-05-20 07:10:04 * skipping: "/mnt/backups/urbackup/client4/230513-0602_Image_C"
2023-05-20 07:10:04   [subvol is not readonly]
2023-05-20 07:10:04 * skipping: "/mnt/backups/urbackup/client5/230513-0721_Image_C"
2023-05-20 07:10:04   [subvol is not readonly]
2023-05-20 07:10:04 * skipping: "/mnt/backups/urbackup/client6/230513-0841_Image_C"
2023-05-20 07:10:04   [subvol is not readonly]
2023-05-20 07:10:04 * skipping: "/mnt/backups/urbackup/client7/230515-1013_Image_C"
2023-05-20 07:10:04   [subvol is not readonly]
2023-05-20 07:10:05 * skipping: "/mnt/backups/urbackup/client8/230516-1258_Image_C"
2023-05-20 07:10:05   [subvol is not readonly]
2023-05-20 07:10:05 * skipping: "/mnt/backups/urbackup/client9/230517-2217_Image_C"
2023-05-20 07:10:05   [subvol is not readonly]
2023-05-20 07:10:05 * skipping: "/mnt/backups/urbackup/client10/230518-0644_Image_C"
2023-05-20 07:10:05   [subvol is not readonly]
2023-05-20 07:10:05 * sending "/mnt/backups/urbackup/client3/230519-0528_Image_C" to "/mnt/offsite-urbackup/client3/230519-0528_Image_C"
2023-05-20 07:32:47   time [0:22:40] -- rate [ 131MiB/s] -- size [ 175GiB]
2023-05-20 07:32:47 * skipping: "/mnt/backups/urbackup/client11/230519-0848_Image_SYSVOL"
2023-05-20 07:32:47   [subvol is not readonly]
2023-05-20 07:32:47 * skipping: "/mnt/backups/urbackup/client12/230519-1659_Image_C"
2023-05-20 07:32:47   [subvol is not readonly]
2023-05-20 07:32:47 * sending "/mnt/backups/urbackup/client13/230519-2247" to "/mnt/offsite-urbackup/client13/230519-2247"
2023-05-20 07:32:47   parent  "/mnt/backups/urbackup/client13/230518-2309"
2023-05-20 07:32:49   time [0:00:00] -- rate [25.7KiB/s] -- size [2.73KiB]
2023-05-20 07:32:49 * sending "/mnt/backups/urbackup/client1/230520-0142_Image_SYSVOL" to "/mnt/offsite-urbackup/client1/230520-0142_Image_SYSVOL"
2023-05-20 07:32:52   time [0:00:00] -- rate [40.6MiB/s] -- size [27.0MiB]
2023-05-20 07:32:52 * sending "/mnt/backups/urbackup/client1/230520-0143_Image_C" to "/mnt/offsite-urbackup/client1/230520-0143_Image_C"
2023-05-20 07:32:52   parent  "/mnt/backups/urbackup/client1/230513-0137_Image_C"
2023-05-20 07:35:29   time [0:02:28] -- rate [96.4MiB/s] -- size [14.0GiB]
2023-05-20 07:35:29 * sending "/mnt/backups/urbackup/client14/230520-0312_Image_SYSVOL" to "/mnt/offsite-urbackup/client14/230520-0312_Image_SYSVOL"
2023-05-20 07:35:33   time [0:00:03] -- rate [ 240MiB/s] -- size [ 941MiB]
2023-05-20 07:35:33 * sending "/mnt/backups/urbackup/client15/230520-0142" to "/mnt/offsite-urbackup/client15/230520-0142"
2023-05-20 07:35:33   parent  "/mnt/backups/urbackup/client15/230519-0100"
2023-05-20 07:35:37   time [0:00:01] -- rate [8.70MiB/s] -- size [15.3MiB]
2023-05-20 07:35:37 * sending "/mnt/backups/urbackup/client14/230520-0329_Image_ESP" to "/mnt/offsite-urbackup/client14/230520-0329_Image_ESP"
2023-05-20 07:35:38   time [0:00:00] -- rate [ 229MiB/s] -- size [ 100MiB]
2023-05-20 07:35:38 * skipping: "/mnt/backups/urbackup/client14/230520-0335_Image_C"
2023-05-20 07:35:38   [subvol is not readonly]
2023-05-20 07:35:38 * sending "/mnt/backups/urbackup/client16/230520-0512" to "/mnt/offsite-urbackup/client16/230520-0512"
2023-05-20 07:35:38   parent  "/mnt/backups/urbackup/client16/230519-0546"
2023-05-20 07:35:49   time [0:00:08] -- rate [ 275KiB/s] -- size [2.37MiB]
2023-05-20 07:35:49 
2023-05-20 07:35:49        Source (3459 subvols, 2427 parents, 123 orphans, 72.0% full): /mnt/backups/urbackup
2023-05-20 07:35:49   Destination (3447 subvols, 2306 parents,  10 orphans, 76.7% full): /mnt/offsite-urbackup
2023-05-20 07:35:49 
</pre>

#### Notes:
My understanding of the UrBackup file structure is as follows (please correct me if my knowledge is faulty):
* UrBackup stores the active databases and various support files in /var/urbackup
* UrBackup stores backup copies of the databases, etc. see: "Backup storage path" in settings. (eg. /mnt/backups/urbackup)
* UrBackup stores a client's most recent file backup (not image backup) as symlinks in /mnt/backups/urbackup/clients
* The "Backup storage path" is a btrfs filesystem:
  * However, the client folders directly under that path are not subvolumes, but are instead regular directories
  * The actual backup folders are subvolumes

#### FAQ:
##### Why create this, why not simply use program x, y or z?
I tried several other btrfs clone/backup/copy programs, but none of them worked exactly how I wanted.  Most required a snapshot at the main btrfs filesystem level.  That is to say, I would have needed to keep a snapshot of the source filesystem, then send/receive using that snapshot.  The snapshot approach would be *much* easier than writing this script.  However, there is one big negative consequence: you must always keep at least one common snapshot between the source filesystem and each destination. When you lose the last common snapshot you must restart with a full copy.  Ouch, that would take a really long time!  Also, as my backups continue grow (think UrBackup deletes old snapshots), that snapshot needs to stay around and the storage deltas from that snapshot can't be released until all offsite copies have a new common snapshot.
##### How does this program solve the snapshot issue mentioned above?
urbackup-clone-btrfs.py does not need a UrBackup main subvolume snapshot to work.  It simply re-creates the file structure and keeps the source and destination in sync using the original parent/child relationship information as defined by the btrfs subvolume list command.  It will iterate through each subvolume and check if a valid copy already exists, if not, use btrfs send and receive to create one.  An offsite disk can be missing without having a common snapshot, simply bring the offsite disk back and anything new is copied over.  Furthermore, use --delete-strays to remove any extraneous subvolumes on the destination that have been removed from the source.

