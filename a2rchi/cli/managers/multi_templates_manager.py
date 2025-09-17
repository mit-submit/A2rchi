"""
CHANGES THAT NEED TO BE MADE HERE BEFORE WE SWAP THIS IN FOR THE REGULAR TEMPLATE MANAGER:
    - config manager should handle giving all the stuff that needs to be used here instead of just getting the a2rchi config, as in, 
    anywhere where we pass in a2rchi_config as an argument to a function, just have the config manager get the info through a function 
    and pass it in accordingly 
    - templated configs need to be all copied over to the context directory (.a2rchi), template them in here, map the prompts and copy them to the 
    context directory (for right now im thinking of having each prompt mapped to  a uuid that just lives in something like a prompts folder, 
    that way we basically dont have to think of anything crazy to handle this well)
    - grafana stuff has to be handled for multiple configs 

** i put comments above each of the functions that need to be changed right now 
"""
from a2rchi.cli.service_registry import service_registry
from a2rchi.utils.logging import get_logger
from a2rchi.cli.managers.multi_config_manager import MultiConfigManager

from jinja2 import Environment
from pathlib import Path
from typing import Dict, List, Any

import os
import shutil

logger = get_logger(__name__)

# Template file constants
BASE_CONFIG_TEMPLATE = "base-config.yaml"
BASE_COMPOSE_TEMPLATE = "base-compose.yaml"
BASE_INIT_SQL_TEMPLATE = "base-init.sql"
BASE_GRAFANA_DATASOURCES_TEMPLATE = "grafana/datasources.yaml"
BASE_GRAFANA_DASHBOARDS_TEMPLATE = "grafana/dashboards.yaml"
BASE_GRAFANA_A2RCHI_DEFAULT_DASHBOARDS_TEMPLATE = "grafana/a2rchi-default-dashboard.json"
BASE_GRAFANA_CONFIG_TEMPLATE = "grafana/grafana.ini"

# this function is used for benchmarking, do not remove
def get_git_information():
    import subprocess
    meta_data = {}
    wd = Path(__file__).parent 

    if (
        subprocess.call(
            ["git", "branch"],
            cwd=wd,
            stderr=subprocess.STDOUT,
            stdout=open(os.devnull, "w"),
        )
        != 0
    ):
        meta_data["git_info"] = {
            "hash": "Not a git repository!",
            "diff": "Not a git repository",
        }
    else:
        meta_data["last_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=wd, encoding="UTF-8"
        )
        diff_comm = ["git", "diff"]
        meta_data["git_diff"] = subprocess.check_output(
            diff_comm, encoding="UTF-8", cwd=wd
        )
    return meta_data

class MultiTemplateManager:
    """Manages template rendering and file preparation using service registry"""
    
    def __init__(self, jinja_env: Environment, config_manager: MultiConfigManager):
        self.env = jinja_env
        self.registry = service_registry
        self.config_manager = config_manager

    # TODO: handle passing all of the info through config manager instead of expecting an a2rchi config
    def prepare_deployment_files(self, compose_config, secrets_manager, **kwargs) -> None:
        """Prepare all necessary files for deployment"""
        base_dir = compose_config.base_dir

        # Prepare prompts based on enabled services
        enabled_services = compose_config.get_enabled_services()


        pipelines_and_info = self.config_manager.get_pipelines()
        prompt_mappings = self._prepare_prompts(base_dir, pipelines_and_info, enabled_services)
        
        # Prepare main configuration file
        self._prepare_config_files(base_dir, a2rchi_config, prompt_mappings, compose_config.verbosity, **kwargs)
        
        # Prepare service-specific files
        if compose_config.get_service('grafana').enabled:
            # DEAL WITH THESE FUNCTIONS LAST
            self._prepare_grafana_files(base_dir, a2rchi_config, compose_config.name, secrets_manager, **kwargs)
        
        if compose_config.get_service('grader').enabled:
            # DEAL WITH THESE FUNCTIONS LAST
            self._prepare_grader_files(base_dir, a2rchi_config)
        
        # Prepare PostgreSQL initialization
        self._prepare_postgres_init(base_dir, compose_config, secrets_manager)
        
        # Prepare Compose file
        self._prepare_compose_file(base_dir, compose_config, a2rchi_config, **kwargs)
        
        # Copy web input lists if they exist
        self.config_manager.get_input_lists()
        self._copy_web_input_lists(base_dir, input_lists)
        
        # Copy source code
        self._copy_source_code(base_dir)
        
        if benchmarking:
            query_file_dest = base_dir / "queries.txt"
            shutil.copyfile(query_file, query_file_dest)

            import yaml
            git_info = get_git_information()
            git_info_path = base_dir / "git_info.yaml"
            with open(git_info_path, "w") as f:
                yaml.dump(git_info, f)
    
    def _prepare_compose_file(self, base_dir: Path, compose_config, config_manager: MultiConfigManager, **kwargs) -> None:
        """Prepare the Compose file - pure rendering with all data from compose_config"""
        
        # Get template variables from compose config
        template_vars = compose_config.to_template_vars()
        
        # Add port configuration from registry
        service_configs = config_manager.get_service_configs()
        template_vars.update(self._extract_port_config(service_configs, **kwargs))
        
        # Add optional template variables
        template_vars.setdefault('prompt_files', [])
        template_vars.setdefault('rubrics', [])
        
        # Add grader rubrics if grader is enabled
        if compose_config.get_service('grader').enabled:
            rubrics = config_manager.get_grader_rubrics()
            template_vars['rubrics'] = rubrics
        
        # Render compose template
        compose_template = self.env.get_template(BASE_COMPOSE_TEMPLATE)
        compose = compose_template.render(**template_vars)
        
        with open(base_dir / "compose.yaml", 'w') as f:
            f.write(compose)
    
    def _extract_port_config(self, service_configs: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Extract port configurations using service registry"""
        port_config = {}
        
        for service_name, service_def in self.registry.get_all_services().items():
            # Always add default ports first
            if service_def.default_host_port:
                port_config[f'{service_name}_port_host'] = service_def.default_host_port
            if service_def.default_container_port:
                port_config[f'{service_name}_port_container'] = service_def.default_container_port
            
            # Try to override with config values if path is specified
            if service_def.port_config_path:
                try:
                    config_value = service_configs
                    for key in service_def.port_config_path.split('.'):
                        config_value = config_value[key]
                    
                    if isinstance(config_value, dict):
                        host_port = config_value.get('external_port', service_def.default_host_port)
                        container_port = config_value.get('port', service_def.default_container_port)
                    else:
                        host_port = config_value
                        container_port = service_def.default_container_port
                    
                    if host_port:
                        port_config[f'{service_name}_port_host'] = host_port
                    if container_port:
                        port_config[f'{service_name}_port_container'] = container_port
                        
                except (KeyError, TypeError):
                    # Config path not found, defaults already set above
                    pass

        return port_config

    # hopefully this should work
    def _prepare_config_files(self, base_dir: Path, config_manager: MultiConfigManager, 
                        prompt_mappings: Dict[str, str], verbosity: int, **kwargs) -> None:
        """Prepare the main A2RCHI configuration file with updated prompt paths"""
        
        logger.debug(f"Received prompt_mappings: {prompt_mappings}")
        
        # Update prompt paths with mappings
        
        import copy
        for file_path, config in config_manager.get_raw_configs():
            updated_config = copy.deepcopy(config)
            pipeline_names = updated_config.get("a2rchi", {}).get("pipelines")
            for pipeline_name in pipeline_names:
                if pipeline_name:
                    pipeline_config = updated_config.get("a2rchi", {}).get("pipeline_map", {}).get(pipeline_name, {})
                    prompts_config = pipeline_config.get("prompts", {})
                                    
                    # TODO: FIX THIS
                    # Update prompt paths in all sections
                    for section_name, section_prompts in prompts_config.items():
                        if not isinstance(section_prompts, dict):
                            continue
                        for prompt_key in section_prompts.keys():
                            if prompt_key in prompt_mappings:
                                old_value = section_prompts[prompt_key]
                                section_prompts[prompt_key] = prompt_mappings[prompt_key]
                                logger.debug(f"Updated {prompt_key}: '{old_value}' -> '{prompt_mappings[prompt_key]}'")
                            else:
                                logger.error(f"Prompt_key '{prompt_key}' NOT found in mappings")
        
            if kwargs['host_mode']:
                updated_config["host_mode"] = kwargs["host_mode"]
                if config.get("services", {}).get("chromadb", {}).get("chromadb_external_port", None):
                    updated_config["services"]["chromadb"]["chromadb_port"] = config["services"]["chromadb"]["chromadb_external_port"]

            config_template = self.env.get_template(BASE_CONFIG_TEMPLATE)
            templated_config = config_template.render(verbosity=verbosity, **updated_config)
                
            config_path = base_dir / f"configs/{file_path.name}" 
            with open(config_path, 'w') as f:
                f.write(templated_config)
    
    # TODO: FIX for multiple 
    def _prepare_prompts(self, base_dir: Path, config_manager: MultiConfigManager ) -> Dict[str, str]:
        """Prepare prompt files dynamically from pipeline configuration and return mappings"""
        pipeline_names = a2rchi_config.get("a2rchi", {}).get("pipelines")
        if not pipeline_names:
            return {}
        
        # to implement this, I want prompt mappings to go Unique filepath -> uuid that it turns into later,
        # store each mapping
        self._copy_pipeline_prompts(base_dir, prompts_config)

        return prompt_mappings
    
    # TODO: ultimately the unique filepaths to a prompt file should be mapped to uuid and sent to the appropriate folder in the context config
    # then have the docker files copy them into the container 
    def _copy_pipeline_prompts(self, base_dir: Path, prompts_config: Dict[str, Any]) -> Dict[str, str]:
        """Copy all prompt files defined in pipeline configuration and return mappings"""
        prompt_mappings = {}

        # Process all sections (required, optional, etc.)
        for _, section_prompts in prompts_config.items():            
            if not isinstance(section_prompts, dict):
                continue
                
            for prompt_key, prompt_path in section_prompts.items():
                
                if not prompt_path or prompt_path == 'null':
                    continue
                    
                source_path = Path(prompt_path).expanduser()
                if not source_path.exists():
                    logger.warning(f"Prompt file not found: {prompt_path}")
                    continue
                
                try:
                    target_path = base_dir / "prompts" / source_path.name
                    
                    # Create directory if it doesn't exist
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    shutil.copyfile(source_path, target_path)
                    
                    # Verify the file was actually copied
                    if target_path.exists():
                        logger.debug(f"Copied prompt: {source_path} -> {target_path}")
                    else:
                        logger.error(f"Copy failed - file does not exist: {target_path}")
                        
                except Exception as e:
                    logger.error(f"Error copying {source_path} to {target_path}: {e}")

                # Store mappings for config update
                container_path = f"/root/A2rchi/prompts/{source_path.name}"
                prompt_mappings[prompt_key] = container_path
        
        return prompt_mappings
    
    # TODO: the only thing that needs to be done here is we need to make the config manager pass the first model that it finds (maybe this gets fixed later)
    def _prepare_grafana_files(self, base_dir: Path, a2rchi_config: Dict[str, Any], deployment_name: str, secrets, **kwargs) -> None:
        """Prepare Grafana configuration files"""
        grafana_dir = base_dir / "grafana"
        grafana_dir.mkdir(exist_ok=True)
        
        grafana_pg_password = secrets.get_secret('GRAFANA_PG_PASSWORD')
        
        # Prepare datasources.yaml
        datasources_template = self.env.get_template(BASE_GRAFANA_DATASOURCES_TEMPLATE)
        datasources = datasources_template.render(grafana_pg_password=grafana_pg_password, host_mode=kwargs['host_mode'])
        with open(grafana_dir / "datasources.yaml", 'w') as f:
            f.write(datasources)
        
        # Prepare dashboards.yaml
        dashboards_template = self.env.get_template(BASE_GRAFANA_DASHBOARDS_TEMPLATE)
        dashboards = dashboards_template.render()
        with open(grafana_dir / "dashboards.yaml", 'w') as f:
            f.write(dashboards)
        
        # Prepare default dashboard
        # TODO: HERE JUST PASS IN THE FIRST REQUIRED MODEL, ONE MODEL AT LEAST ALWAYS IS REQUIRED (figure out the logic in config manager for this)
        pipeline_name = a2rchi_config.get("a2rchi", {}).get("pipeline")
        pipeline_config = a2rchi_config.get("a2rchi", {}).get("pipeline_map", {}).get(pipeline_name, {}) if pipeline_name else {}
        models_config = pipeline_config.get("models", {})
        model_name = next(iter(models_config.values())) if models_config else "DumbLLM"
        
        dashboard_template = self.env.get_template(BASE_GRAFANA_A2RCHI_DEFAULT_DASHBOARDS_TEMPLATE)
        dashboard = dashboard_template.render(
            prod_config_name=deployment_name,
            prod_model_name=model_name
        )
        with open(grafana_dir / "a2rchi-default-dashboard.json", 'w') as f:
            f.write(dashboard)
        
        # Prepare grafana.ini
        config_template = self.env.get_template(BASE_GRAFANA_CONFIG_TEMPLATE)
        config = config_template.render()
        with open(grafana_dir / "grafana.ini", 'w') as f:
            f.write(config)
    
    # TODO: dont pass in a2rchi config, just have the config manager handle getting the appropriate info
    def _prepare_grader_files(self, base_dir: Path, a2rchi_config: Dict[str, Any]) -> None:
        """Prepare grader-specific files"""
        grader_config = a2rchi_config.get('services', {}).get('grader_app', {})
        
        # Prepare users.csv
        users_csv_dir = grader_config.get('local_users_csv_dir')
        if users_csv_dir:
            users_csv_path = Path(users_csv_dir).expanduser() / "users.csv"
            if users_csv_path.exists():
                shutil.copyfile(users_csv_path, base_dir / "users.csv")
        
        # Prepare rubrics
        rubric_dir = grader_config.get('local_rubric_dir')
        num_problems = grader_config.get('num_problems', 1)
        
        if rubric_dir:
            for problem in range(1, num_problems + 1):
                rubric_path = Path(rubric_dir).expanduser() / f"solution_with_rubric_{problem}.txt"
                if rubric_path.exists():
                    target_path = base_dir / f"solution_with_rubric_{problem}.txt"
                    shutil.copyfile(rubric_path, target_path)
    
    def _prepare_postgres_init(self, base_dir: Path, compose_config, secrets) -> None:
        """Prepare PostgreSQL initialization script"""
        grafana_enabled = compose_config.get_service('grafana').enabled
        grafana_pg_password = secrets.get_secret('GRAFANA_PG_PASSWORD') if grafana_enabled else ""
        
        init_sql_template = self.env.get_template(BASE_INIT_SQL_TEMPLATE)
        init_sql = init_sql_template.render(
            use_grafana=grafana_enabled,
            grafana_pg_password=grafana_pg_password
        )
        
        with open(base_dir / "init.sql", 'w') as f:
            f.write(init_sql)
    
    # TODO: again, make the config manager handle this, luckily this should all be static so the config manager can 
    # just easily hand back the info we need here
    def _get_grader_rubrics(self, a2rchi_config: Dict[str, Any]) -> List[str]:
        """Get list of rubric files for grader service"""
        grader_config = a2rchi_config.get('interfaces', {}).get('grader_app', {})
        num_problems = grader_config.get('num_problems', 1)
        return [f"solution_with_rubric_{i}" for i in range(1, num_problems + 1)]
    
    def _copy_web_input_lists(self, base_dir: Path, input_lists: List | None ) -> None:
        """Copy web input lists if they exist"""
        if input_lists is None:
            return
        
        weblists_path = base_dir / "weblists"
        weblists_path.mkdir(exist_ok=True)
        
        for input_list in input_lists:
            if os.path.exists(input_list):
                shutil.copyfile(input_list, weblists_path / os.path.basename(input_list))
    
    def _copy_source_code(self, base_dir: Path) -> None:
        """Copy source code to deployment directory"""
        source_files = [
            ("a2rchi", "a2rchi_code"),
            ("pyproject.toml", "pyproject.toml"),
            ("requirements.txt", "requirements.txt"),
            ("LICENSE", "LICENSE")
        ]
        
        for src, dst in source_files:
            src_path = Path(src)
            dst_path = base_dir / dst
            
            if src_path.is_dir():
                if dst_path.exists():
                    shutil.rmtree(dst_path)
                shutil.copytree(src, dst_path)
            elif src_path.exists():
                shutil.copyfile(src, dst_path)
