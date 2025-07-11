#-------------------------------------------------------------------------------------------
# deep cleanup script to remove images, containers, and volumes
#-------------------------------------------------------------------------------------------
echo "WARNING: this script will delete all images, containers, and volumes in podman are you sure you want to continue? [y/n]: "

read dec

if ! [ "$dec" = 'y' ]
then
    exit 0
fi

podman rm -af
pofman rm -af
podman rmi -af
podman rmi -af
podman volumes rm -a -f
podman volumes rm -a -f
