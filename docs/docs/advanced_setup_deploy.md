# Advanced Setup & Deployment

Topics related to advanced setup and deployment of A2RCHI.

## Configuring Podman

To ensure your Podman containers stay running for extended periods, you need to enable lingering. To do this, run:

```bash
loginctl enable-linger
```

To check or confirm the lingering status, run:

```bash
loginctl user-status | grep -m1 Linger
```

See the Red Hat [documentation](https://access.redhat.com/solutions/7054698) for additional context.

## Running LLMs locally on your GPUs

There are a few additional system requirements for this to work:

1. Make sure you have NVIDIA drivers installed.
2. (Optional) For the containers where A2RCHI will run to access the GPUs, install the [NVIDIA container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
3. Configure the container runtime to access the GPUs.

<details>
<summary>For Podman</summary>

Run the following command:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

Then list the devices:

```bash
nvidia-ctk cdi list
```

You should see output similar to:

```text
INFO[0000] Found 9 CDI devices
...
nvidia.com/gpu=0
nvidia.com/gpu=1
...
nvidia.com/gpu=all
...
```

These listed "CDI devices" will be referenced to run A2RCHI on the GPUs, so make sure they are present. To learn more, consult the [Podman GPU documentation](https://podman-desktop.io/docs/podman/gpu).

</details>

<details>
<summary>For Docker</summary>

Run the following command:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
```

The remaining steps mirror the Podman flow. NOTE: this has not yet been fully tested with Docker. Refer to the [NVIDIA toolkit documentation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#configuration) for details.

</details>

Once these requirements are met, the `a2rchi create [...] --gpu-ids <gpus>` option will deploy A2RCHI across your GPUs.

## Helpful Notes for Production Deployments

You may wish to use the CLI in order to stage production deployments. This section covers some useful notes to keep in mind.

### Running multiple deployments on the same machine

The CLI allows multiple deployments to run on the same daemon in the case of Docker (Podman has no daemon). The container networks between all the deployments are separate, so there is very little risk of them accidentally communicating with one another.

However, you need to be careful with the external ports. Suppose you're running two deployments and both of them are running the chat on external port 8000. There is no way to view both deployments at the same time from the same port, so instead you should forward the deployments to other external ports. Generally, this can be done in the configuration:

```yaml
services:
  chat_app:
    external_port: 7862  # default is 7861
  uploader_app:
    external_port: 5004  # default is 5003
  grafana:
    external_port: 3001  # default is 3000
  chromadb:
    chromadb_external_port: 8001  # default is 8000
```

### Persisting data between deployments

Volumes persist between deployments, so if you deploy an instance and upload additional documents, you do not need to redo this every time you deploy. If you are editing any data, explicitly remove this information from the volume, or remove the volume itself with:

```bash
docker/podman volume rm <volume-name>
```

To see what volumes are currently present, run:

```bash
docker/podman volume ls
```
