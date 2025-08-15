#!/bin/tcsh

# Setup script for configuring containers on heavy node
# Run this after activating your Python virtual environment

echo "========================================="
echo "Heavy Node Container Setup Script"
echo "========================================="
echo ""

echo "Step 1: Creating local scratch storage directories..."
echo "Command: mkdir -p /scratch/containers/dalfonso/containers"
mkdir -p /scratch/containers/$USER/containers

echo "Command: mkdir -p /scratch/containers/dalfonso/build-tmp"
mkdir -p /scratch/containers/$USER/build-tmp

echo "✓ Directories created successfully"
echo ""

echo "Step 2: Configuring podman storage to use local filesystem..."
echo "Command: Creating ~/.config/containers/storage.conf"

# Create the containers config directory if it doesn't exist
mkdir -p ~/.config/containers

# Write the storage configuration
cat > ~/.config/containers/storage.conf << EOF
[storage]
driver = "overlay"
graphroot = "/scratch/containers/$USER/containers"
EOF

echo "✓ Storage configuration written"
echo ""

echo "Step 3: Setting environment variables for build process..."
echo "Command: setenv TMPDIR /scratch/containers/dalfonso/build-tmp"
setenv TMPDIR /scratch/containers/$USER/build-tmp

echo "Command: setenv BUILDAH_TMPDIR /scratch/containers/dalfonso/build-tmp"
setenv BUILDAH_TMPDIR /scratch/containers/$USER/build-tmp

echo "✓ Environment variables set"
echo ""

echo "Step 4: Verifying configuration..."
echo "Command: df -h /scratch/containers/"
df -h /scratch/containers/

echo ""
echo "Current environment variables:"
echo "TMPDIR: $TMPDIR"
echo "BUILDAH_TMPDIR: $BUILDAH_TMPDIR"
echo ""

echo "Step 5: Resetting podman to use new configuration..."
echo "Command: podman system reset"
echo "WARNING: This will remove all existing containers, images, and volumes!"
echo -n "Continue? (y/N): "
set response = $<
if ( "$response" == "y" || "$response" == "Y" ) then
    podman system reset
    echo "✓ Podman reset completed"
else
    echo "Skipping podman reset - you'll need to run 'podman system reset' manually"
endif

echo ""
echo "========================================="
echo "Setup Complete!"
echo "========================================="
echo ""
