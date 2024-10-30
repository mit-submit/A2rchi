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

# DEFINITIONS
env = Environment(
    loader=PackageLoader("a2rchi"),
    autoescape=select_autoescape(),
    undefined=ChainableUndefined,
)
A2RCHI_DIR = os.path.join(os.path.expanduser('~'), ".a2rchi")
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

def _run_bash_command(command_str: str, verbose = False) -> Tuple[str, str]:
    """Helper function to execute a bash command and print output in real-time without hanging."""
    command_str_lst = command_str.split(" ")

    # Start the process
    process = subprocess.Popen(command_str_lst, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Initialize empty strings for stdout and stderr
    stdout, stderr = "", ""

    # Continuously read from stdout and stderr
    while True:
        # Check if there’s output from stdout
        if process.poll() is not None:
            break  # Process has finished, so we can exit the loop
        
        # Read each line from stdout and stderr as it becomes available
        out_line = process.stdout.readline()
        if out_line:
            if verbose:
                _print_msg(out_line)  # Print each line as it is produced
            stdout += out_line

        err_line = process.stderr.readline()
        if err_line:
            if verbose:
                _print_msg(err_line)  # Print each error line as it is produced
            stderr += err_line

        # Allow other tasks to run if this process takes time
        time.sleep(0.1)

    # Capture any remaining output after process completes
    remaining_stdout, remaining_stderr = process.communicate()
    stdout += remaining_stdout
    stderr += remaining_stderr

    return stdout, stderr

def _create_docker_volume(volume_name):
    # first, check to see if volume already exists
    stdout, stderr = _run_bash_command("docker volume ls")
    if stderr:
        raise BashCommandException(stderr)

    for line in stdout.split("\n"):
        # return early if the volume exists
        if volume_name in line:
            return

    # otherwise, create the volume
    _print_msg(f"Creating docker volume: {volume_name}")
    _, stderr = _run_bash_command(f"docker volume create --name {volume_name}")
    if stderr:
        raise BashCommandException(stderr)


def _read_prompts(a2rchi_config):
    # initialize variables
    main_prompt, condense_prompt, summary_prompt = None, None, None

    # read prompts and return them
    with open(a2rchi_config['main_prompt'], 'r') as f:
        main_prompt = f.read()

    with open(a2rchi_config['condense_prompt'], 'r') as f:
        condense_prompt = f.read()

    with open(a2rchi_config['summary_prompt'], 'r') as f:
        summary_prompt = f.read()

    return main_prompt, condense_prompt, summary_prompt


def _print_msg(msg):
    print(f"[a2rchi]>> {msg}")


@click.group()
def cli():
    pass


@click.command()
@click.option('--name', type=str, default=None, help="Name of the a2rchi deployment.")
@click.option('--grafana', '-g', 'include_grafana', type=bool, default=False, help="Boolean to add Grafana dashboard in deployment.")
@click.option('--a2rchi-config', '-f', 'a2rchi_config_filepath', type=str, default=None, help="Path to compose file.")
def create(name, include_grafana, a2rchi_config_filepath):
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
        raise InvalidCommandException(
            f"Please provide a name for the deployment using the --name flag."
        )

    if a2rchi_config_filepath is not None:
        a2rchi_config_filepath = a2rchi_config_filepath.strip()

    # create temporary directory for template files
    a2rchi_name_dir = os.path.join(A2RCHI_DIR, f"a2rchi-{name}")
    os.makedirs(a2rchi_name_dir, exist_ok=True)

    # initialize dictionary of template variables for docker compose file
    tag = "2000" # TODO, make tagging better
    os.environ['TAG'] = tag #TODO: also this should be done with templating, not environment variables
    compose_template_vars = {
        "chat_image": f"chat-{name}",
        "chat_tag": tag,
        "chat_container_name": f"chat-{name}",
        "chromadb_image": f"chromadb-{name}",
        "chromadb_tag": tag,
        "chromadb_container_name": f"chromadb-{name}",
        "postgres_container_name": f"postgres-{name}",
    }

    # create docker volumes; these commands will no-op if they already exist
    _print_msg("Creating docker volumes")
    _create_docker_volume(f"a2rchi-{name}")
    _create_docker_volume(f"a2rchi-pg-{name}")
    compose_template_vars["chat_volume_name"] = f"a2rchi-{name}"
    compose_template_vars["postgres_volume_name"] = f"a2rchi-pg-{name}"

    # fetch or generate grafana password
    grafana_pg_password = os.environ.get("GRAFANA_PG_PASSWORD", secrets.token_hex(8))

    # if deployment includes grafana, create docker volume and template deployment files
    compose_template_vars["include_grafana"] = include_grafana
    if include_grafana:
        _create_docker_volume(f"a2rchi-grafana-{name}")

        _print_msg("Preparing Grafana")
        # add grafana to compose and SQL init
        compose_template_vars["include_grafana"] = include_grafana
        compose_template_vars["grafana_volume_name"] = f"a2rchi-grafana-{name}"
        compose_template_vars["grafana_image"] = "mdr223/a2rchi"
        compose_template_vars["grafana_tag"] = "grafana-0.0.1"
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
        a2rchi_dashboards = a2rchi_dashboards_template.render()
        with open(os.path.join(a2rchi_name_dir, "grafana", "a2rchi-default-dashboard.json"), 'w') as f:
            # json.dump(a2rchi_dashboards, f)
            f.write(a2rchi_dashboards)

        grafana_config_template = env.get_template(BASE_GRAFANA_CONFIG_TEMPLATE)
        grafana_config = grafana_config_template.render()
        with open(os.path.join(a2rchi_name_dir, "grafana", "grafana.ini"), 'w') as f:
            f.write(grafana_config)

    _print_msg("Preparing Postgres")
    # prepare init.sql for postgres initialization
    init_sql_template = env.get_template(BASE_INIT_SQL_TEMPLATE)
    init_sql = init_sql_template.render({
        "include_grafana": include_grafana,
        "grafana_pg_password": grafana_pg_password if include_grafana else "",
    })
    with open(os.path.join(a2rchi_name_dir, "init.sql"), 'w') as f:
        f.write(init_sql)

    # TODO: make more general purpose
    # prepare secrets
    os.makedirs(os.path.join(a2rchi_name_dir, "secrets"), exist_ok=True)
    with open(os.path.join(a2rchi_name_dir, "secrets", "openai_api_key.txt"), 'w') as f:
        f.write(f"{os.environ.get('OPENAI_API_KEY')}")

    with open(os.path.join(a2rchi_name_dir, "secrets", "hf_token.txt"), 'w') as f:
        f.write(f"{os.environ.get('HF_TOKEN')}")

    with open(os.path.join(a2rchi_name_dir, "secrets", "pg_password.txt"), 'w') as f:
        f.write(f"{os.environ.get('PG_PASSWORD')}")

    # Define required fields in user configuration of A2rchi
    required_fields = [
        'name', 
        'global.TRAINED_ON',
        'chains.input_lists', 
        'chains.prompts.CONDENSING_PROMPT', 'chains.prompts.MAIN_PROMPT', 'chains.prompts.SUMMARY_PROMPT'
    ]

    # load user configuration of A2rchi
    with open(a2rchi_config_filepath, 'r') as f:
        a2rchi_config = yaml.load(f, Loader=yaml.FullLoader)
        _validate_config(a2rchi_config, required_fields=required_fields)
        a2rchi_config["postgres_hostname"] = compose_template_vars["postgres_container_name"]
        if "collection_name" not in a2rchi_config:
            a2rchi_config["collection_name"] = f"collection_{name}"

    # copy prompts
    shutil.copyfile(a2rchi_config["chains"]["prompts"]["MAIN_PROMPT"], os.path.join(a2rchi_name_dir, "main.prompt"))
    shutil.copyfile(a2rchi_config["chains"]["prompts"]["CONDENSING_PROMPT"], os.path.join(a2rchi_name_dir, "condense.prompt"))
    shutil.copyfile(a2rchi_config["chains"]["prompts"]["SUMMARY_PROMPT"], os.path.join(a2rchi_name_dir, "summary.prompt"))

    # copy input lists
    weblists_path = os.path.join(a2rchi_name_dir, "weblists")
    os.makedirs(weblists_path, exist_ok=True)
    for web_input_list in a2rchi_config["chains"]["input_lists"]:
        shutil.copyfile(web_input_list, os.path.join(weblists_path, os.path.basename(web_input_list)))

    # load and render config template
    config_template = env.get_template(BASE_CONFIG_TEMPLATE)
    config = config_template.render(**a2rchi_config)

    # write final templated configuration
    with open(os.path.join(a2rchi_name_dir, "config.yaml"), 'w') as f:
        f.write(config)

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
    _print_msg("Building Base Image")
    _, _ = _run_bash_command(f"docker build -f {os.path.join(a2rchi_name_dir, 'a2rchi_code/templates/dockerfiles/Dockerfile-base')} -t a2rchi-base:{tag} --progress=plain {os.path.join(a2rchi_name_dir)}", verbose=True) #TODO: not great printing out output #TODO: should be name specific to the deployment
    _print_msg("Starting docker compose")
    stdout, stderr = _run_bash_command(f"docker compose -f {os.path.join(a2rchi_name_dir, 'compose.yaml')} up -d --build --force-recreate --always-recreate-deps", verbose=True) #TODO: not great printing out output


@click.command()
@click.option('--name', type=str, default=None, help="Name of the a2rchi deployment.")
def delete(name):
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

    # stop compose
    a2rchi_name_dir = os.path.join(A2RCHI_DIR, f"a2rchi-{name}")
    _print_msg("Stopping docker compose")
    _run_bash_command(f"docker compose -f {os.path.join(a2rchi_name_dir, 'compose.yaml')} down")

    # remove files in a2rchi directory
    _print_msg("Removing files in a2rchi directory")
    _run_bash_command(f"rm -r {a2rchi_name_dir}")


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
    shutil.copyfile(a2rchi_config["summary_prompt"], os.path.join(a2rchi_name_dir, "summary.prompt"))

    _print_msg("Updating config")

    # read prompts
    main_prompt, condense_prompt, summary_prompt = _read_prompts(a2rchi_config)

    # get config containing hostname and port for chat service
    config_dict = yaml.load(config, Loader=yaml.FullLoader)
    chat_config = config_dict['interfaces']['chat_app']

    resp = requests.post(
        f"http://{chat_config['HOSTNAME']}:{chat_config['EXTERNAL_PORT']}/api/update_config",
        json={
            "config": config,
            "main_prompt": main_prompt,
            "condense_prompt": condense_prompt,
            "summary_prompt": summary_prompt,
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
