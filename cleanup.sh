#-------------------------------------------------------------------------------------------
# basic cleanup script to remove images and containers
#-------------------------------------------------------------------------------------------
echo "WARNING: this script will delete all images and containers in podman are you sure you want to continue? [y/n]: "

read dec

if ! [ "$dec" = 'y' ]
then
    exit 0
fi

podman rm -af
pofman rm -af
podman rmi -af
podman rmi -af
