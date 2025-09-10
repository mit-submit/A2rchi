# Advanced Setup & Deployment

Topics related to advanced setup and deployment of A2rchi.

## Configuring Podman

To ensure your Podman containers stay running for extended periods, you need to enable lingering. To do this, the following command should work:
```bash
loginctl enable-linger
```
To check/confirm the lingering status, simply do
```bash
loginctl user-status | grep -m1 Linger
```
See the redhat [docs](https://access.redhat.com/solutions/7054698) to read more.

## Running LLMs locally on your GPUs

There are a few additional system requirements for this to work:

1. First, make sure you have nvidia drivers installed.

2. (Optional) For the containers where A2rchi will run to access the GPUs, please install the [nvidia container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). If you are using one of the supported services, these containers are already configured to use GPUs.

3. Configure the container runtime to access the GPUs.

    <details><summary>For Podman</summary>
    For Podman, run

    ```bash
    sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
    ```
          
    Then, the following command

    ```
    nvidia-ctk cdi list
    ```

    should show an output that includes
          
    ```nohighlight
    INFO[0000] Found 9 CDI devices 
    ...
    nvidia.com/gpu=0
    nvidia.com/gpu=1
    ...
    nvidia.com/gpu=all
    ...
    ```
          
    These listed "CDI devices" will be referenced to run A2rchi on the GPUs, so make sure this is there. To see more about accessing GPUs with Podman, click [here](https://podman-desktop.io/docs/podman/gpu).
    </details>

    <details><summary>For Docker</summary>
    If you have Docker, run

    ```bash
    sudo nvidia-ctk runtime configure --runtime=docker
    ```

    What follows should be the same as above -- NOTE: this has not been tested yet with Docker. To see more about accessing GPUs with Docker, click [here](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#configuration).
    </details>

Once these requirements are met, the `a2rchi create [...] --gpu-ids <gpus>` option will deploy A2rchi across your GPUs.

## Helpful Notes for Production Deployments

You may wish to use the CLI in order to stage production deployments. This section covers some useful notes to keep in mind.

### Running multiple deployments on the same machine

The CLI is built to allow multiple deployments to run on the same daemon in the case of Docker (Podman has no daemon). The container networks between all the deployments are separate, so there is very little risk of them accidentally communicating with one another.

However, you need to be careful with the external ports. Suppose you're running two deployments and both of them are running the chat on external port 8000. There is no way to view both deployments at the same time from the same port, so instead you should split to forwarding the deployments to other external ports. Generally, this can be done in the configuration:
```
interfaces:
  chat_app:
    EXTERNAL_PORT: 7862 # default is 7681
  uploader_app:
    EXTERNAL_PORT: 5004 # default is 5003
  grafana:
    EXTERNAL_PORT: 3001 # default is 3000

utils:
  data_manager:
    chromadb_external_port: 8001 # default is 8000
```

### Persisting data between deployments

Volumes persist between deployments, so if you deploy an instance, and upload some further documents, you will not need to redo this every time you deploy. Of course, if you are editing any data, you should explicitly remove this information from the volume, or simply remove the volume itself with
```bash
docker/podman volume rm <volume-name>
```
You can see what volumes are currently up with
```bash
docker/podman volume ls
```