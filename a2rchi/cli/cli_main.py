import re
from jinja2 import Environment, PackageLoader, select_autoescape, ChainableUndefined
from typing import Tuple

import click
import os
import requests
import secrets
import shutil
import subprocess
import yaml
import time
import shlex
import threading

# DEFINITIONS
env = Environment(
    loader=PackageLoader("a2rchi"),
    autoescape=select_autoescape(),
    undefined=ChainableUndefined,
)
A2RCHI_DIR = os.environ.get('A2RCHI_DIR',os.path.join(os.path.expanduser('~'), ".a2rchi"))
BASE_CONFIG_TEMPLATE = "base-config.yaml"
BASE_DOCKERFILE_LOCATION = "dockerfiles"
BASE_GRAFANA_DATASOURCES_TEMPLATE = "grafana/datasources.yaml"
BASE_GRAFANA_DASHBOARDS_TEMPLATE = "grafana/dashboards.yaml"
BASE_GRAFANA_A2RCHI_DEFAULT_DASHBOARDS_TEMPLATE = "grafana/a2rchi-default-dashboard.json"
BASE_GRAFANA_CONFIG_TEMPLATE = "grafana/grafana.ini"
BASE_COMPOSE_TEMPLATE = "base-compose.yaml"
BASE_INIT_SQL_TEMPLATE = "base-init.sql"

class InvalidCommandException(Exception):
    pass

class BashCommandException(Exception):
    pass

def _prepare_secret(a2rchi_name_dir, secret_name, locations_of_secrets):
    """
    Prepares a secret by locating its file in the specified directories, 
    reading its content, and saving it to a target directory.

    The function searches for a secret file named `"{secret_name.lower()}.txt"`
    in the directories provided in `locations_of_secrets`. If multiple files 
    with the same name are found, an error is raised to prevent ambiguity. 
    If no file is found, a `FileNotFoundError` is raised. The secret's content 
    is read from the file and written to the `secrets` subdirectory within 
    `a2rchi_name_dir`.

    Args:
        a2rchi_name_dir (str): The base directory where the `secrets` 
            directory will be created or used.
        secret_name (str): The name of the secret to locate. The function 
            expects a file named `"{secret_name.lower()}.txt"` in the given 
            directories.
        locations_of_secrets (list[str]): A list of directories to search 
            for the secret file.

    Raises:
        ValueError: If multiple files with the secret name are found in the 
            specified directories.
        FileNotFoundError: If no file with the secret name is found in the 
            specified directories.
    
    Example:
        >>> a2rchi_name_dir = "/path/to/a2rchi"
        >>> secret_name = "API_KEY"
        >>> locations_of_secrets = ["/path/to/dir1", "/path/to/dir2"]
        >>> _prepare_secret(a2rchi_name_dir, secret_name, locations_of_secrets)
        Secret for 'API_KEY' prepared at /path/to/a2rchi/secrets/api_key.txt.
    """
    # Ensure the secrets directory exists
    secrets_dir = os.path.join(a2rchi_name_dir, "secrets")
    os.makedirs(secrets_dir, exist_ok=True)

    # Look for the secret file in the specified locations
    secret_filename = f"{secret_name.lower()}.txt"
    found_secrets = []

    for location in locations_of_secrets:
        potential_path = os.path.expanduser(os.path.join(location, secret_filename))
        if os.path.isfile(potential_path):
            found_secrets.append(potential_path)

    # Check for multiple occurrences of the secret
    if len(found_secrets) > 1:
        raise ValueError(
            f"Error: Multiple secret files found for '{secret_name}' in locations: {found_secrets}"
        )
    elif len(found_secrets) == 0:
        raise FileNotFoundError(
            f"Error: No secret file found for '{secret_name}' in the specified locations."
        )

    # Read the secret from the found file
    secret_file_path = found_secrets[0]
    with open(secret_file_path, 'r') as secret_file:
        secret_value = secret_file.read().strip()

    # Write the secret to the target directory
    target_secret_path = os.path.join(secrets_dir, secret_filename)
    with open(target_secret_path, 'w') as target_file:
        target_file.write(secret_value)

def _prepare_grading_rubrics(a2rchi_name_dir, rubric_dir, num_problems):
        rubrics = []
        for problem in range(1, num_problems + 1):
            _print_msg(f"Preparing rubric for problem {problem}")
            rubric_path = os.path.expanduser(os.path.join(rubric_dir, f"solution_with_rubric_{problem}.txt"))
            if not os.path.isfile(rubric_path):
                raise FileNotFoundError(f"Rubric file for problem {problem} not found at {rubric_path}")
            target_rubric_path = os.path.join(a2rchi_name_dir, f"solution_with_rubric_{problem}.txt")
            shutil.copyfile(rubric_path, target_rubric_path)
            rubrics.append(f"solution_with_rubric_{problem}")
            _print_msg(f"Rubric for problem {problem} prepared at {target_rubric_path}.")

        return rubrics

def _validate_config(config, required_fields):
    """
    a function to validate presence of required fields in nested dictionaries
    """
    for field in required_fields:
        keys = field.split('.')
        value = config
        for key in keys:
            if key not in value:
                raise ValueError(f"Missing required field: '{field}' in the configuration")
            value = value[key]  # Drill down into nested dictionaries


def _run_bash_command(command_str: str, verbose=False, cwd=None) -> Tuple[str, str]:
    """Run a shell command and stream output in real-time, capturing stdout and stderr."""
    command_str_lst = shlex.split(command_str)
    process = subprocess.Popen(
        command_str_lst,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered
        cwd=cwd
    )

    stdout_lines = []
    stderr_lines = []

    def _read_stream(stream, collector, stream_name):
        for line in iter(stream.readline, ''):
            collector.append(line)
            if verbose:
                _print_msg(f"{line}")  # keep formatting tight
        stream.close()

    # start threads for non-blocking reads
    stdout_thread = threading.Thread(target=_read_stream, args=(process.stdout, stdout_lines, "stdout"))
    stderr_thread = threading.Thread(target=_read_stream, args=(process.stderr, stderr_lines, "stderr"))
    stdout_thread.start()
    stderr_thread.start()

    # wait for command to finish
    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        stdout_thread.join()
        stderr_thread.join()
        raise

    return ''.join(stdout_lines), ''.join(stderr_lines)

def _create_volume(volume_name, podman=False):
    # root podman or docker
    if podman:
        ls_volumes = "podman volume ls"
        create_volume = f"podman volume create {volume_name}"
    else:
        ls_volumes = "docker volume ls"
        create_volume = f"docker volume create --name {volume_name}"

    # first, check to see if volume already exists
    stdout, stderr = _run_bash_command(ls_volumes)
    if stderr:
        raise BashCommandException(stderr)

    for line in stdout.split("\n"):
        # return early if the volume exists
        if volume_name in line:
            _print_msg(f"Volume '{volume_name}' already exists. No action needed.")
            return

    # otherwise, create the volume
    _print_msg(f"Creating volume: {volume_name}")
    _, stderr = _run_bash_command(create_volume)
    if stderr:
        raise BashCommandException(stderr)


def _read_prompts(a2rchi_config):
    # initialize variables
    main_prompt, condense_prompt = None, None

    # read prompts and return them
    with open(a2rchi_config['main_prompt'], 'r') as f:
        main_prompt = f.read()

    with open(a2rchi_config['condense_prompt'], 'r') as f:
        condense_prompt = f.read()

    return main_prompt, condense_prompt


def _parse_gpu_ids_option(ctx, param, value):
    if value is None:
        return None
    if value.lower() == "all":
        return "all"
    try:
        return [int(x.strip()) for x in value.split(",")]
    except ValueError:
        raise click.BadParameter('--gpu-ids option must be "all" (or equivalently just the --gpu flag) or comma-separated integers (e.g., "0,1") to specify which GPUs to use (try nvidia-smi to see available GPUs and respective available memory)')


def _print_msg(msg):
    print(f"[a2rchi]>> {msg}")


@click.group()
def cli():
    pass


@click.command()
@click.option('--name', type=str, required=True, help="Name of the a2rchi deployment.")
@click.option('--a2rchi-config', '-f', 'a2rchi_config_filepath', type=str, required=True, help="Path to compose file.")
@click.option('--grafana', '-g', 'use_grafana', is_flag=True, help="Flag to add Grafana dashboard in deployment.")
@click.option('--document-uploader', '-du', 'use_uploader_service', is_flag=True, help="Flag to add service for admins to upload data")
@click.option('--cleo-and-mailer', '-cm', 'use_cleo_and_mailer', is_flag=True, help="Flag to add service for a2rchi interface with cleo and a mailer")
@click.option('--jira', '-j', 'use_jira', is_flag=True, help="Flag to add service for a2rchi interface with Jira")
@click.option('--piazza', '-piazza', 'use_piazza_service', is_flag=True, help="Flag to add piazza service to read piazza posts and suggest answers to a slack channel.")
@click.option('--grader', '-grader', 'use_grader_service', is_flag=True, help="Flag to add service for grading service (image to text, then grading, on web interface)")
@click.option('--mattermost', '-mattermost', 'use_mattermost_service', is_flag=True, help="Flag to add mattermost service to read mattermost posts and suggest answers to a mattermost channel.")
@click.option('--podman', '-p', 'use_podman', is_flag=True, help="Boolean to use podman instead of docker.")
@click.option('--gpu', 'all_gpus', flag_value="all", help='Flag option for GPUs. Same as "--gpu-ids all"')
@click.option('--gpu-ids', 'gpu_ids', callback=_parse_gpu_ids_option, help='GPU configuration: "all" or comma-separated IDs (integers), e.g., "0,1". Current support for podman to do this.')
@click.option('--tag', '-t', 'image_tag', type=str, default=2000, help="Tag for the collection of images you will create to build chat, chroma, and any other specified services")
@click.option('--hostmode', '-hm', 'host_mode', type=bool, default=False, help="Boolean to use host mode networking for the containers.")
@click.option('--verbosity', '-v', 'verbosity', type=int, default=3, help="Set verbosity level for python's logging module. Default is 3. Mapping is 0: CRITICAL, 1: ERROR, 2: WARNING, 3: INFO, 4: DEBUG.")
def create(
    name, 
    a2rchi_config_filepath,
    use_grafana, 
    use_uploader_service, 
    use_cleo_and_mailer,
    use_jira,
    use_piazza_service,
    use_grader_service,
    use_mattermost_service,
    use_podman,
    all_gpus,
    gpu_ids,
    image_tag,
    host_mode,
    verbosity
):
    """
    Create an instance of a RAG system with the specified name. By default,
    this command will create the following services:

    1. A chat interface (for users to communicate with the agent)
    2. A ChromaDB vector store (for storing relevant document chunks)
    3. A Postgres database (for storing the conversation history)

    Users may also include additional services, such as a Grafana dashboard
    (for monitoring LLM and system performance).
    """
    # parse and clean command arguments
    if name is not None:
        name = name.strip()
    else:
        raise click.ClickException(f"Please provide a name for the deployment using the --name flag.")
    
    if all_gpus and gpu_ids:
        raise click.ClickException("Use either the --gpu flag or --gpu-ids, not both!")

    if a2rchi_config_filepath is not None:
        a2rchi_config_filepath = a2rchi_config_filepath.strip()

    # create temporary directory for template files
    a2rchi_name_dir = os.path.join(A2RCHI_DIR, f"a2rchi-{name}")
    os.makedirs(a2rchi_name_dir, exist_ok=True)

    # initialize dictionary of template variables for docker compose file
    tag = image_tag
    compose_template_vars = {
        "chat_image": f"chat-{name}",
        "chat_tag": tag,
        "chat_container_name": f"chat-{name}",
        "chromadb_image": f"chromadb-{name}",
        "chromadb_tag": tag,
        "chromadb_container_name": f"chromadb-{name}",
        "postgres_container_name": f"postgres-{name}",
        "use_podman": use_podman,
        "gpu_ids": gpu_ids or all_gpus,
    }

    # create docker volumes; these commands will no-op if they already exist
    _print_msg("Creating volumes")
    _create_volume(f"a2rchi-{name}", podman=use_podman)
    _create_volume(f"a2rchi-pg-{name}", podman=use_podman)
    if gpu_ids or all_gpus:
        _create_volume(f"a2rchi-models", podman=use_podman)
    compose_template_vars["chat_volume_name"] = f"a2rchi-{name}"
    compose_template_vars["postgres_volume_name"] = f"a2rchi-pg-{name}"
     # if using host mode, set the host mode variable
    compose_template_vars["host_mode"] = host_mode

    # Define required fields in user configuration of A2rchi
    required_fields = [
        'name', 
        'global.TRAINED_ON',
        'chains.prompts.CONDENSING_PROMPT', 'chains.prompts.MAIN_PROMPT',
        'chains.chain.MODEL_NAME', 'chains.chain.CONDENSE_MODEL_NAME',
    ]

    if use_piazza_service:
        required_fields.append('utils.piazza.network_id')

    if use_grader_service:
        required_fields = [
            'name',
            'global.TRAINED_ON',
            'interfaces.grader_app.num_problems', 'interfaces.grader_app.local_rubric_dir', 'interfaces.grader_app.local_users_csv_dir',
            'chains.prompts.IMAGE_PROCESSING_PROMPT', 'chains.prompts.GRADING_FINAL_GRADE_PROMPT',
            'chains.chain.IMAGE_PROCESSING_MODEL_NAME', 'chains.chain.GRADING_FINAL_GRADE_MODEL_NAME',
        ]
    

    # load user configuration of A2rchi
    with open(a2rchi_config_filepath, 'r') as f:
        a2rchi_config = yaml.load(f, Loader=yaml.FullLoader)
        _validate_config(a2rchi_config, required_fields=required_fields)
        if host_mode:
            a2rchi_config["postgres_hostname"] = "localhost"
            a2rchi_config.setdefault("utils", {}).setdefault("data_manager", {})["chromadb_host"] = "localhost"
        else:   
            a2rchi_config["postgres_hostname"] = compose_template_vars["postgres_container_name"]
        if "collection_name" not in a2rchi_config:
            a2rchi_config["collection_name"] = f"collection_{name}"

    locations_of_secrets = a2rchi_config["locations_of_secrets"]

    # prepare grader service if requested
    compose_template_vars["use_grader_service"] = use_grader_service
    if use_grader_service:
        _print_msg("Preparing Grader Service")
        compose_template_vars["grader_image"] = f"grader-{name}"
        compose_template_vars["grader_tag"] = tag

        compose_template_vars["grader_volume_name"] = f"a2rchi-grader-{name}"
        _create_volume(compose_template_vars["grader_volume_name"], podman=use_podman)

        _prepare_secret(a2rchi_name_dir, "admin_password", locations_of_secrets)

        # prepare grader app logins file (users.csv)
        users_csv_dir = a2rchi_config['interfaces']['grader_app']['local_users_csv_dir']
        users_csv_path = os.path.expanduser(os.path.join(users_csv_dir, "users.csv"))
        _print_msg(f"Preparing users.csv from {users_csv_path}")
        if not os.path.isfile(users_csv_path):
            raise FileNotFoundError(f"users.csv file not found in directory {users_csv_dir}")
        target_users_csv_path = os.path.join(a2rchi_name_dir, "users.csv")
        shutil.copyfile(users_csv_path, target_users_csv_path)

        # prepare rubrics for n problems (config: interfaces.grader_app.num_problems) 
        rubric_dir = a2rchi_config['interfaces']['grader_app']['local_rubric_dir']
        num_problems = a2rchi_config['interfaces']['grader_app']['num_problems']

        rubrics = _prepare_grading_rubrics(a2rchi_name_dir, rubric_dir, num_problems)

        compose_template_vars["rubrics"] = rubrics



    # if deployment includes grafana, create docker volume and template deployment files
    compose_template_vars["use_grafana"] = use_grafana
    if use_grafana:
        _create_volume(f"a2rchi-grafana-{name}", podman=use_podman)

        # fetch grafana password or raise error if not set
        if "GRAFANA_PG_PASSWORD" not in os.environ:
            raise RuntimeError("Missing required environment variable for grafana service: GRAFANA_PG_PASSWORD")

        grafana_pg_password = os.environ["GRAFANA_PG_PASSWORD"]

        _print_msg("Preparing Grafana")
        # add grafana to compose and SQL init
        compose_template_vars["grafana_volume_name"] = f"a2rchi-grafana-{name}"
        compose_template_vars["grafana_image"] = f"grafana-{name}"
        compose_template_vars["grafana_tag"] = tag
        compose_template_vars["grafana_container_name"] = f"grafana-{name}"

        # template grafana datasources file to include postgres pw for grafana
        grafana_datasources_template = env.get_template(BASE_GRAFANA_DATASOURCES_TEMPLATE)
        grafana_datasources = grafana_datasources_template.render({"grafana_pg_password": grafana_pg_password})

        # write complete datasources file to folder
        os.makedirs(os.path.join(a2rchi_name_dir, "grafana"), exist_ok=True)
        with open(os.path.join(a2rchi_name_dir, "grafana", "datasources.yaml"), 'w') as f:
            #yaml.dump(grafana_datasources, f)
            f.write(grafana_datasources)

        # copy dashboards.yaml, a2rchi-default-dashboards.json, grafana.ini to grafana dir
        grafana_dashboards_template = env.get_template(BASE_GRAFANA_DASHBOARDS_TEMPLATE)
        grafana_dashboards = grafana_dashboards_template.render()
        with open(os.path.join(a2rchi_name_dir, "grafana", "dashboards.yaml"), 'w') as f:
            # yaml.dump(grafana_dashboards, f)
            f.write(grafana_dashboards)

        a2rchi_dashboards_template = env.get_template(BASE_GRAFANA_A2RCHI_DEFAULT_DASHBOARDS_TEMPLATE)
        a2rchi_dashboards = a2rchi_dashboards_template.render(
            prod_config_name=a2rchi_config["name"],
            prod_model_name=a2rchi_config["chains"]["chain"]["MODEL_NAME"]
        )
        with open(os.path.join(a2rchi_name_dir, "grafana", "a2rchi-default-dashboard.json"), 'w') as f:
            # json.dump(a2rchi_dashboards, f)
            f.write(a2rchi_dashboards)

        grafana_config_template = env.get_template(BASE_GRAFANA_CONFIG_TEMPLATE)
        grafana_config = grafana_config_template.render()
        with open(os.path.join(a2rchi_name_dir, "grafana", "grafana.ini"), 'w') as f:
            f.write(grafana_config)

        # Extract ports from configuration and add to compose_template_vars #TODO: remove default values from cli_main.py
        # Grafana service ports
        grafana_port_host = a2rchi_config.get('interfaces', {}).get('grafana', {}).get('EXTERNAL_PORT', 3000)
        compose_template_vars['grafana_port_host'] = grafana_port_host

    compose_template_vars["use_uploader_service"] = use_uploader_service
    if use_uploader_service:
         _print_msg("Preparing Uploader Service")

         # Add uploader service to compose
         compose_template_vars["use_uploader_service"] = use_uploader_service
         compose_template_vars["uploader_image"] = f"uploader-{name}"
         compose_template_vars["uploader_tag"] = tag

         # Extract ports from configuration and add to compose_template_vars #TODO: remove default values from cli_main.py
         # Uploader service ports
         uploader_port_host = a2rchi_config.get('interfaces', {}).get('uploader_app', {}).get('EXTERNAL_PORT', 5003)
         uploader_port_container = a2rchi_config.get('interfaces', {}).get('uploader_app', {}).get('PORT', 5001)
         compose_template_vars['uploader_port_host'] = uploader_port_host
         compose_template_vars['uploader_port_container'] = uploader_port_container

         _prepare_secret(a2rchi_name_dir, "flask_uploader_app_secret_key", locations_of_secrets)
         _prepare_secret(a2rchi_name_dir, "uploader_salt", locations_of_secrets)

    compose_template_vars["use_piazza_service"] = use_piazza_service
    if use_piazza_service:
        _print_msg("Preparing Piazza Service")

        compose_template_vars["piazza_image"] = f"piazza-{name}"
        compose_template_vars["piazza_tag"] = tag

        # piazza secrets
        _prepare_secret(a2rchi_name_dir, "piazza_email", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "piazza_password", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "slack_webhook", locations_of_secrets)

    compose_template_vars["use_mattermost_service"] = use_mattermost_service
    if use_mattermost_service:
        _print_msg("Preparing Mattermost Service")

        compose_template_vars["mattermost_image"] = f"mattermost-{name}"
        compose_template_vars["mattermost_tag"] = tag

        # mattermost secrets
        _prepare_secret(a2rchi_name_dir, "mattermost_webhook", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "mattermost_channel_id_read", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "mattermost_channel_id_write", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "mattermost_pak", locations_of_secrets)

    compose_template_vars["use_cleo_and_mailer"] = use_cleo_and_mailer
    if use_cleo_and_mailer:
        _print_msg("Preparing Cleo and Emailer Service")

        # Add uploader service to compose
        compose_template_vars["use_cleo_and_mailer"] = use_cleo_and_mailer
        compose_template_vars["cleo_image"] = f"cleo-{name}"
        compose_template_vars["cleo_tag"] = tag
        compose_template_vars["mailbox_image"] = f"mailbox-{name}"
        compose_template_vars["mailbox_tag"] = tag

        _prepare_secret(a2rchi_name_dir, "imap_user", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "imap_pw", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "cleo_url", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "cleo_user", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "cleo_pw", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "cleo_project", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "sender_server", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "sender_port", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "sender_replyto", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "sender_user", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "sender_pw", locations_of_secrets)

    
    if use_jira:
        _prepare_secret(a2rchi_name_dir, "jira_pat", locations_of_secrets)
        compose_template_vars["jira"] = True
    

    _print_msg("Preparing Postgres")
    # prepare init.sql for postgres initialization
    init_sql_template = env.get_template(BASE_INIT_SQL_TEMPLATE)
    init_sql = init_sql_template.render({
        "use_grafana": use_grafana,
        "grafana_pg_password": grafana_pg_password if use_grafana else "",
    })
    with open(os.path.join(a2rchi_name_dir, "init.sql"), 'w') as f:
        f.write(init_sql)
    
    model_fields = ["MODEL_NAME", "CONDENSE_MODEL_NAME"] if not use_grader_service else ["IMAGE_PROCESSING_MODEL_NAME", "GRADING_FINAL_GRADE_MODEL_NAME"]
    chain_config = a2rchi_config["chains"]["chain"]

    # prepare needed api token secrets
    if any("OpenAI" in chain_config[model] for model in model_fields) or not "HuggingFace" in a2rchi_config.get("utils", {}).get("embeddings", {}).get("EMBEDDING_NAME", ""):
        _prepare_secret(a2rchi_name_dir, "openai_api_key", locations_of_secrets)
        compose_template_vars["openai"] = True
    if any("Anthropic" in chain_config[model] for model in model_fields):
        _prepare_secret(a2rchi_name_dir, "anthropic_api_key", locations_of_secrets)
        compose_template_vars["anthropic"] = True
    if "HuggingFace" in a2rchi_config.get("utils", {}).get("embeddings", {}).get("EMBEDDING_NAME", ""):
        _print_msg("WARNING: You are using a HuggingFace embedding model. The default is public and doesn't require a token, but if you want to use a private model you will need one.")
        #_prepare_secret(a2rchi_name_dir, "hf_token", locations_of_secrets)
        #compose_template_vars["huggingface"] = True

    _prepare_secret(a2rchi_name_dir, "pg_password", locations_of_secrets)
    # SSO secrets
    if a2rchi_config.get("utils",{}).get("sso", {}).get("ENABLED", False):
        _print_msg("Preparing SSO secrets")
        compose_template_vars["sso"] = True
        _prepare_secret(a2rchi_name_dir, "sso_username", locations_of_secrets)
        _prepare_secret(a2rchi_name_dir, "sso_password", locations_of_secrets)


    # copy prompts (make this cleaner prob)
    if use_grader_service:
        shutil.copyfile(a2rchi_config["chains"]["prompts"]["IMAGE_PROCESSING_PROMPT"], os.path.join(a2rchi_name_dir, "image_processing.prompt"))
        shutil.copyfile(a2rchi_config["chains"]["prompts"]["GRADING_FINAL_GRADE_PROMPT"], os.path.join(a2rchi_name_dir, "grading_final_grade.prompt"))
        compose_template_vars["summary"] = True
        compose_template_vars["analysis"] = True
        try:
            shutil.copyfile(a2rchi_config["chains"]["prompts"]["GRADING_SUMMARY_PROMPT"], os.path.join(a2rchi_name_dir, "grading_summary.prompt"))
        except KeyError:
            compose_template_vars["summary"] = False
            _print_msg("Grading summary prompt not defined in configuration, there will be no grading summary step in the grading chain.")
        try:
            shutil.copyfile(a2rchi_config["chains"]["prompts"]["GRADING_ANALYSIS_PROMPT"], os.path.join(a2rchi_name_dir, "grading_analysis.prompt"))
        except KeyError:
            compose_template_vars["analysis"] = False
            _print_msg("Grading analysis prompt not defined in configuration, there will be no grading analysis step in the grading chain.")
    else:
        shutil.copyfile(a2rchi_config["chains"]["prompts"]["MAIN_PROMPT"], os.path.join(a2rchi_name_dir, "main.prompt"))
        shutil.copyfile(a2rchi_config["chains"]["prompts"]["CONDENSING_PROMPT"], os.path.join(a2rchi_name_dir, "condense.prompt"))


    # copy input lists
    weblists_path = os.path.join(a2rchi_name_dir, "weblists")
    os.makedirs(weblists_path, exist_ok=True)
    web_input_lists = a2rchi_config["chains"].get("input_lists", [])
    web_input_lists = web_input_lists or [] # protect against NoneType
    for web_input_list in web_input_lists:
        shutil.copyfile(web_input_list, os.path.join(weblists_path, os.path.basename(web_input_list)))

    # load and render config template
    config_template = env.get_template(BASE_CONFIG_TEMPLATE)
    config = config_template.render(verbosity=verbosity, **a2rchi_config)

    # write final templated configuration
    with open(os.path.join(a2rchi_name_dir, "config.yaml"), 'w') as f:
        f.write(config)

    with open(os.path.join(a2rchi_name_dir, "config.yaml"), 'r') as f:
        filled_config = yaml.load(f, Loader=yaml.FullLoader)

    # Chat service ports
    chat_port_host = filled_config.get('interfaces').get('chat_app').get('EXTERNAL_PORT')
    chat_port_container = filled_config.get('interfaces').get('chat_app').get('PORT')
    compose_template_vars['chat_port_host'] = chat_port_host
    compose_template_vars['chat_port_container'] = chat_port_container
    # ChromaDB service ports
    chromadb_port_host = filled_config.get('utils').get('data_manager').get('chromadb_external_port')
    compose_template_vars['chromadb_port_host'] = chromadb_port_host
    # Postgres service ports are never externally exposed, so they don't need to be managed!

    # grader service ports
    compose_template_vars["grader_port_host"] = filled_config.get('interfaces').get('grader_app').get('EXTERNAL_PORT')
    compose_template_vars["grader_port_container"] = filled_config.get('interfaces').get('grader_app').get('PORT')

    # load compose template
    _print_msg("Preparing Compose")
    compose_template = env.get_template(BASE_COMPOSE_TEMPLATE)
    compose = compose_template.render({**compose_template_vars})
    with open(os.path.join(a2rchi_name_dir, "compose.yaml"), 'w') as f:
        # yaml.dump(compose, f)
        f.write(compose)

    # copy over the code into the a2rchi dir
    shutil.copytree("a2rchi", os.path.join(a2rchi_name_dir, "a2rchi_code"))
    shutil.copyfile("pyproject.toml", os.path.join(a2rchi_name_dir, "pyproject.toml"))
    shutil.copyfile("requirements.txt", os.path.join(a2rchi_name_dir, "requirements.txt"))
    shutil.copyfile("LICENSE", os.path.join(a2rchi_name_dir, "LICENSE"))

    # create a2rchi system using docker
    if use_podman:
        compose_up = f"podman compose -f {os.path.join(a2rchi_name_dir, 'compose.yaml')} up -d --build --force-recreate --always-recreate-deps"
    else:
        compose_up = f"docker compose -f {os.path.join(a2rchi_name_dir, 'compose.yaml')} up -d --build --force-recreate --always-recreate-deps"
    _print_msg("Starting compose")
    stdout, stderr = _run_bash_command(compose_up, verbose=True, cwd=a2rchi_name_dir)
    _print_msg("DONE compose")

@click.command()
@click.option('--name', type=str, help="Name of the a2rchi deployment.")
@click.option('--rmi', is_flag=True, help="Remove images after deleting the deployment.")
def delete(name, rmi):
    """
    Delete instance of RAG system with the specified name.
    """
    # parse and clean command arguments
    if name is not None:
        name = name.strip()
    else:
        raise InvalidCommandException(
            f"Please provide a name for the deployment using the --name flag."
        )

    a2rchi_name_dir = os.path.join(A2RCHI_DIR, f"a2rchi-{name}")
    compose_file = os.path.join(a2rchi_name_dir, 'compose.yaml')
    extra_args = ""

    if rmi: extra_args += "--rmi all"

    def is_installed(cmd):
        return shutil.which(cmd) is not None

    def is_running(compose_cmd):
        try:
            ps_cmd = f"{compose_cmd} -f {compose_file} ps"
            stdout, _ = _run_bash_command(ps_cmd)
            # If any service is listed as "Up", it's running
            return any("Up" in line for line in stdout.splitlines())
        except Exception:
            return False

    # check whether the images are running on either docker or podman
    compose_stopped = False
    if is_installed("podman"):
        if is_running("podman compose"):
            _print_msg("Stopping podman compose deployment")
            _run_bash_command(f"podman compose -f {compose_file} down {extra_args}")
            compose_stopped = True
    if is_installed("docker"):
        if is_running("docker compose"):
            _print_msg("Stopping docker compose deployment")
            _run_bash_command(f"docker compose -f {compose_file} down {extra_args}")
            compose_stopped = True

    if not compose_stopped:
        _print_msg("No running docker or podman compose deployment found, or neither is installed.")

    # remove files in a2rchi directory
    _print_msg("Removing files in a2rchi directory")
    _run_bash_command(f"rm -rf {a2rchi_name_dir}")


@click.command()
@click.option('--name', type=str, default=None, help="Name of the a2rchi deployment.")
@click.option('--a2rchi-config', '-f', 'a2rchi_config_filepath', type=str, default=None, help="Path to compose file.")
def update(name, a2rchi_config_filepath): #TODO: not sure if this works anymore, or if we actually need it
    """
    Update instance of RAG system with the specified name using a new configuration.
    """
    # parse and clean command arguments
    if name is not None:
        name = name.strip()
    else:
        raise InvalidCommandException(
            f"Please provide a name for the deployment using the --name flag."
        )

    if a2rchi_config_filepath is not None:
        a2rchi_config_filepath = a2rchi_config_filepath.strip()

    # load user configuration of A2rchi
    with open(a2rchi_config_filepath, 'r') as f:
        a2rchi_config = yaml.load(f, Loader=yaml.FullLoader)
        a2rchi_config["postgres_hostname"] = f"postgres-{name}"
        if "collection_name" not in a2rchi_config:
            a2rchi_config["collection_name"] = f"collection_{name}"

    # load and render config template
    config_template = env.get_template(BASE_CONFIG_TEMPLATE)
    config = config_template.render(**a2rchi_config)

    # write final templated configuration to keep consistent w/state of container
    a2rchi_name_dir = os.path.join(A2RCHI_DIR, f"a2rchi-{name}")
    a2rchi_config_rendered_fp = os.path.join(a2rchi_name_dir, "config.yaml")
    with open(a2rchi_config_rendered_fp, 'w') as f:
        f.write(config)

    # copy prompts to keep consistent w/state of container
    shutil.copyfile(a2rchi_config["main_prompt"], os.path.join(a2rchi_name_dir, "main.prompt"))
    shutil.copyfile(a2rchi_config["condense_prompt"], os.path.join(a2rchi_name_dir, "condense.prompt"))

    _print_msg("Updating config")

    # read prompts
    main_prompt, condense_prompt = _read_prompts(a2rchi_config)

    # get config containing hostname and port for chat service
    config_dict = yaml.load(config, Loader=yaml.FullLoader)
    chat_config = config_dict['interfaces']['chat_app']

    resp = requests.post(
        f"http://{chat_config['HOSTNAME']}:{chat_config['EXTERNAL_PORT']}/api/update_config",
        json={
            "config": config,
            "main_prompt": main_prompt,
            "condense_prompt": condense_prompt,
        }
    )
    _print_msg(resp.json()['response'])


def main():
    """
    Entrypoint for a2rchi cli tool implemented using Click.
    """
    # cli.add_command(help)
    cli.add_command(create)
    cli.add_command(delete)
    cli.add_command(update)
    cli()
