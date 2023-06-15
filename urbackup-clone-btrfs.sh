#!/bin/bash

## this is an example bash script to ensure urbackup-clone-btrfs
## will only run one instance (using flock)
## this also relies on a helper script urbackup-clone-btrfs.mount.py

# change these variables as needed
source="/mnt/backups/urbackup"
destination="/mnt/backups/offsite-urbackup"
logfile="/var/log/urbackup-clone-btrfs.log"


##
## Only allow a single instances to run
##

# setup fd 9 for flock
exec 9<"$0"

# flock test, can we get exclusive lock?
if ! flock -n -x 9; then
    # another instance must be running already
    >&2 echo "$0 cannot get flock, another instance is already running."
    exit 1
fi


##
## Main program starts here
##

# redirect stdout to logfile, stderr is unaffected
exec >"$logfile"

# this is used to determine script runtime
start=$SECONDS

# this causes python stdout to unbuffer (we see output on time, not later)
export PYTHONUNBUFFERED=1

# mount the encrypted offsite drives
echo
echo "Mount encrypted offsite drive(s)..."
/usr/local/bin/urbackup-clone-btrfs/urbackup-clone-btrfs.mount.py mount | /usr/bin/ts '%Y-%m-%d %H:%M:%S'
/bin/sleep 5

# run the clone
echo
echo "Clone ${source} to ${destination}"
/usr/local/bin/urbackup-clone-btrfs/urbackup-clone-btrfs.py ${source} ${destination} --delete-strays -v
/bin/sleep 5

# finally, unmount and lock the drives
echo
echo "Unmount and lock offsite drive(s)..."
/usr/local/bin/urbackup-clone-btrfs/urbackup-clone-btrfs.mount.py unmount | /usr/bin/ts '%Y-%m-%d %H:%M:%S'
/bin/sleep 5

# calculate and display the elapsed time
echo
elapsed=$(( SECONDS - start ))
eval "echo Elapsed time: $(date -ud "@$elapsed" +'$((%s/3600/24))d %Hh %Mm %Ss')"
