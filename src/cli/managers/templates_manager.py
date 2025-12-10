import copy
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List

from jinja2 import Environment

from src.cli.service_registry import service_registry
from src.cli.utils.service_builder import DeploymentPlan
from src.utils.logging import get_logger

logger = get_logger(__name__)


# Template file constants
BASE_CONFIG_TEMPLATE = "base-config.yaml"
BASE_COMPOSE_TEMPLATE = "base-compose.yaml"
BASE_INIT_SQL_TEMPLATE = "base-init.sql"
BASE_GRAFANA_DATASOURCES_TEMPLATE = "grafana/datasources.yaml"
BASE_GRAFANA_DASHBOARDS_TEMPLATE = "grafana/dashboards.yaml"
BASE_GRAFANA_A2RCHI_DEFAULT_DASHBOARDS_TEMPLATE = "grafana/a2rchi-default-dashboard.json"
BASE_GRAFANA_CONFIG_TEMPLATE = "grafana/grafana.ini"


def get_git_information() -> Dict[str, str]:

    meta_data: Dict[str, str] = {}
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


def get_git_version() -> str:
    """Get the current git version using 'git describe --tags --always --dirty'."""
    
    try:
        version = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).parent
        ).strip().decode("utf-8")
        return version
    except Exception:
        return "unknown"


@dataclass
class TemplateContext:
    plan: DeploymentPlan
    config_manager: Any
    secrets_manager: Any
    options: Dict[str, Any]
    base_dir: Path = field(init=False)
    prompt_mappings: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base_dir = self.plan.base_dir

    def pop_option(self, key: str, default: Any = None) -> Any:
        return self.options.pop(key, default)

    def get_option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    @property
    def benchmarking(self) -> bool:
        return bool(self.options.get("benchmarking"))


class TemplateManager:
    """Manages template rendering and file preparation using service registry"""

    def __init__(self, jinja_env: Environment):
        self.env = jinja_env
        self.registry = service_registry
        self._service_hooks: Dict[str, Callable[[TemplateContext], None]] = {
            "grafana": self._render_grafana_assets,
            "grader": self._copy_grader_assets,
        }

    def prepare_deployment_files(
        self,
        plan: DeploymentPlan,
        config_manager,
        secrets_manager,
        **options,
    ) -> None:
        context = TemplateContext(
            plan=plan,
            config_manager=config_manager,
            secrets_manager=secrets_manager,
            options=dict(options),
        )

        logger.info(
            f"Preparing deployment artifacts for `{plan.name}` in {str(context.base_dir)}"
        )

        for stage in self._build_workflow(context):
            logger.debug(f"Starting template stage {stage.__name__}")
            stage(context)
            logger.debug(f"Completed template stage {stage.__name__}")

        logger.info(f"Finished preparing deployment artifacts for {plan.name}")

    # workflow construction
    def _build_workflow(self, context: TemplateContext) -> List[Callable[[TemplateContext], None]]:
        stages: List[Callable[[TemplateContext], None]] = [
            self._stage_prompts,
            self._stage_configs,
            self._stage_service_artifacts,
            self._stage_postgres_init,
            self._stage_compose,
            self._stage_web_lists,
            self._stage_source_copy,
        ]

        if context.benchmarking:
            stages.append(self._stage_benchmarking)

        return stages

    # individual stages
    def _stage_prompts(self, context: TemplateContext) -> None:
        context.prompt_mappings = self._collect_prompt_mappings(context)

    def _stage_configs(self, context: TemplateContext) -> None:
        self._render_config_files(context)

    def _stage_service_artifacts(self, context: TemplateContext) -> None:
        for name, hook in self._service_hooks.items():
            if context.plan.get_service(name).enabled:
                logger.info(f"Rendering supplemental assets for service {name}")
                hook(context)

    def _stage_postgres_init(self, context: TemplateContext) -> None:
        self._render_postgres_init(context)

    def _stage_compose(self, context: TemplateContext) -> None:
        self._render_compose_file(context)

    def _stage_web_lists(self, context: TemplateContext) -> None:
        self._copy_web_input_lists(context)

    def _stage_source_copy(self, context: TemplateContext) -> None:
        self._copy_source_code(context.base_dir)

    def _stage_benchmarking(self, context: TemplateContext) -> None:
        query_file = context.pop_option("query_file")
        if not query_file:
            logger.warning("Benchmarking requested but no query file provided; skipping copy")
        else:
            query_file_dest = context.base_dir / "queries.txt"
            shutil.copyfile(query_file, query_file_dest)

        git_info = get_git_information()
        git_info_path = context.base_dir / "git_info.yaml"

        import yaml

        with open(git_info_path, "w") as f:
            yaml.dump(git_info, f)

    # prompt preparation
    def _collect_prompt_mappings(self, context: TemplateContext) -> Dict[str, Dict[str, str]]:
        prompts_path = context.base_dir / "prompts"
        prompts_path.mkdir(exist_ok=True)

        configs = context.config_manager.get_configs()
        prompt_mappings: Dict[str, Dict[str, str]] = {}
        for config in configs:
            name = config["name"]
            pipeline_names = config.get("a2rchi", {}).get("pipelines") or []
            for pipeline_name in pipeline_names:
                pipeline_config = config.get("a2rchi", {}).get("pipeline_map", {}).get(pipeline_name, {})
                prompts_config = pipeline_config.get("prompts", {})
                prompt_mappings[name] = self._copy_pipeline_prompts(context.base_dir, prompts_config)

        return prompt_mappings

    def _copy_pipeline_prompts(self, base_dir: Path, prompts_config: Dict[str, Any]) -> Dict[str, str]:
        prompt_mappings: Dict[str, str] = {}

        for _, section_prompts in prompts_config.items():
            if not isinstance(section_prompts, dict):
                continue

            for prompt_key, prompt_path in section_prompts.items():
                if not prompt_path or prompt_path == "null":
                    continue

                source_path = Path(prompt_path).expanduser()
                if not source_path.exists():
                    logger.warning(f"Prompt file not found: {prompt_path}")
                    continue

                target_path = base_dir / "prompts" / source_path.name
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, target_path)

                prompt_mappings[prompt_key] = f"/root/A2rchi/prompts/{source_path.name}"
                logger.debug(f"Copied prompt {prompt_key} to {target_path}")

        return prompt_mappings

    # config rendering
    def _render_config_files(self, context: TemplateContext) -> None:
        configs_path = context.base_dir / "configs"
        configs_path.mkdir(exist_ok=True)

        a2rchi_configs = context.config_manager.get_configs()
        for a2rchi_config in a2rchi_configs:
            name = a2rchi_config["name"]
            updated_config = copy.deepcopy(a2rchi_config)

            prompt_mapping = context.prompt_mappings.get(name, {})
            pipeline_names = updated_config.get("a2rchi", {}).get("pipelines") or []

            for pipeline_name in pipeline_names:
                pipeline_config = (
                    updated_config.get("a2rchi", {}).get("pipeline_map", {}).get(pipeline_name, {})
                )
                prompts_config = pipeline_config.get("prompts", {})

                for section_prompts in prompts_config.values():
                    if not isinstance(section_prompts, dict):
                        continue
                    for prompt_key in list(section_prompts.keys()):
                        if prompt_key in prompt_mapping:
                            section_prompts[prompt_key] = prompt_mapping[prompt_key]
                        else:
                            logger.debug(
                                f"Prompt key {prompt_key} not found in prepared prompts for config {name}"
                            )

            if context.plan.host_mode:
                updated_config["host_mode"] = context.plan.host_mode
                chroma_cfg = updated_config.get("services", {}).get("chromadb", {})
                external_port = chroma_cfg.get("chromadb_external_port")
                if external_port:
                    updated_config.setdefault("services", {}).setdefault("chromadb", {})[
                        "chromadb_port"
                    ] = external_port

            config_template = self.env.get_template(BASE_CONFIG_TEMPLATE)
            config_rendered = config_template.render(verbosity=context.plan.verbosity, **updated_config)

            with open(configs_path / f"{name}.yaml", "w") as f:
                f.write(config_rendered)
            logger.info(f"Rendered configuration file {configs_path / name}.yaml")

    # service-specific assets
    def _render_grafana_assets(self, context: TemplateContext) -> None:
        base_dir = context.base_dir
        grafana_dir = base_dir / "grafana"
        grafana_dir.mkdir(exist_ok=True)

        grafana_pg_password = context.secrets_manager.get_secret("GRAFANA_PG_PASSWORD")
        postgres_port = context.config_manager.config.get("services", {}).get("postgres", {}).get("port", 5432)

        datasources_template = self.env.get_template(BASE_GRAFANA_DATASOURCES_TEMPLATE)
        datasources = datasources_template.render(
            grafana_pg_password=grafana_pg_password,
            host_mode=context.plan.host_mode,
            postgres_port=postgres_port,
        )
        with open(grafana_dir / "datasources.yaml", "w") as f:
            f.write(datasources)

        dashboards_template = self.env.get_template(BASE_GRAFANA_DASHBOARDS_TEMPLATE)
        dashboards = dashboards_template.render()
        with open(grafana_dir / "dashboards.yaml", "w") as f:
            f.write(dashboards)

        a2rchi_config = context.config_manager.get_configs()[0]
        pipeline_name = a2rchi_config.get("a2rchi", {}).get("pipeline")
        pipeline_config = (
            a2rchi_config.get("a2rchi", {})
            .get("pipeline_map", {})
            .get(pipeline_name, {}) if pipeline_name else {}
        )
        models_config = pipeline_config.get("models", {})
        model_name = next(iter(models_config.values())) if models_config else "DumbLLM"

        dashboard_template = self.env.get_template(BASE_GRAFANA_A2RCHI_DEFAULT_DASHBOARDS_TEMPLATE)
        dashboard = dashboard_template.render(
            prod_config_name=context.plan.name,
            prod_model_name=model_name,
        )
        with open(grafana_dir / "a2rchi-default-dashboard.json", "w") as f:
            f.write(dashboard)

        config_template = self.env.get_template(BASE_GRAFANA_CONFIG_TEMPLATE)
        grafana_config = config_template.render()
        with open(grafana_dir / "grafana.ini", "w") as f:
            f.write(grafana_config)

    def _copy_grader_assets(self, context: TemplateContext) -> None:
        a2rchi_config = context.config_manager.get_configs()[0]
        grader_config = a2rchi_config.get("services", {}).get("grader_app", {})

        users_csv_dir = grader_config.get("local_users_csv_dir")
        if users_csv_dir:
            users_csv_path = Path(users_csv_dir).expanduser() / "users.csv"
            if users_csv_path.exists():
                shutil.copyfile(users_csv_path, context.base_dir / "users.csv")

        rubric_dir = grader_config.get("local_rubric_dir")
        num_problems = grader_config.get("num_problems", 1)

        if rubric_dir:
            for problem in range(1, num_problems + 1):
                rubric_path = Path(rubric_dir).expanduser() / f"solution_with_rubric_{problem}.txt"
                if rubric_path.exists():
                    target_path = context.base_dir / f"solution_with_rubric_{problem}.txt"
                    shutil.copyfile(rubric_path, target_path)

    # postgres + compose rendering
    def _render_postgres_init(self, context: TemplateContext) -> None:
        grafana_enabled = context.plan.get_service("grafana").enabled
        grafana_pg_password = (
            context.secrets_manager.get_secret("GRAFANA_PG_PASSWORD") if grafana_enabled else ""
        )

        init_sql_template = self.env.get_template(BASE_INIT_SQL_TEMPLATE)
        init_sql = init_sql_template.render(
            use_grafana=grafana_enabled,
            grafana_pg_password=grafana_pg_password,
        )

        dest = context.base_dir / "init.sql"
        with open(dest, "w") as f:
            f.write(init_sql)
        logger.debug(f"Wrote PostgreSQL init script to {dest}")

    def _render_compose_file(self, context: TemplateContext) -> None:
        template_vars = context.plan.to_template_vars()
        template_vars.update(self._extract_port_config(context))
        template_vars.setdefault("postgres_port", context.config_manager.config.get("services", {}).get("postgres", {}).get("port", 5432))

        template_vars["app_version"] = get_git_version()

        # Compose template still expects optional lists
        template_vars.setdefault("prompt_files", [])
        template_vars.setdefault("rubrics", [])

        if context.plan.get_service("grader").enabled:
            template_vars["rubrics"] = self._get_grader_rubrics(context.config_manager)

        compose_template = self.env.get_template(BASE_COMPOSE_TEMPLATE)
        compose_rendered = compose_template.render(**template_vars)

        dest = context.base_dir / "compose.yaml"
        with open(dest, "w") as f:
            f.write(compose_rendered)
        logger.info(f"Rendered compose file {dest}")

    def _extract_port_config(self, context: TemplateContext) -> Dict[str, Any]:
        port_config: Dict[str, Any] = {}

        for service_name, service_def in self.registry.get_all_services().items():
            if service_def.default_host_port:
                port_config[f"{service_name}_port_host"] = service_def.default_host_port
            if service_def.default_container_port:
                port_config[f"{service_name}_port_container"] = service_def.default_container_port

            if service_def.port_config_path:
                try:
                    config_value: Any = context.config_manager.get_configs()[0]
                    for key in service_def.port_config_path.split('.'):
                        config_value = config_value[key]

                    if isinstance(config_value, dict):
                        host_port = config_value.get('external_port', service_def.default_host_port)
                        container_port = config_value.get('port', service_def.default_container_port)
                    else:
                        host_port = config_value
                        container_port = service_def.default_container_port

                    if host_port:
                        port_config[f"{service_name}_port_host"] = host_port
                    if container_port:
                        port_config[f"{service_name}_port_container"] = container_port
                except (KeyError, TypeError):
                    continue

        return port_config

    def _get_grader_rubrics(self, config_manager) -> List[str]:
        a2rchi_config = config_manager.get_configs()[0]
        grader_config = a2rchi_config.get('services', {}).get('grader_app', {})
        num_problems = grader_config.get('num_problems', 1)
        return [f"solution_with_rubric_{i}" for i in range(1, num_problems + 1)]

    # input list / source copying helpers
    def _copy_web_input_lists(self, context: TemplateContext) -> None:
        # Always create weblists directory (required by Dockerfiles, even if empty)
        weblists_path = context.base_dir / "weblists"
        weblists_path.mkdir(exist_ok=True)
        logger.debug(f"Created weblists directory at {weblists_path}")
        
        input_lists = context.config_manager.get_input_lists()
        if not input_lists:
            return

        for input_list in input_lists:
            if os.path.exists(input_list):
                shutil.copyfile(input_list, weblists_path / os.path.basename(input_list))
                logger.debug(f"Copied input list {input_list}")
            else:
                logger.warning(f"Configured input list {input_list} not found; skipping")

    def _copy_source_code(self, base_dir: Path) -> None:
        # Try to locate the repository root in a robust way. Prefer CWD when
        # it contains expected marker files (pyproject.toml, LICENSE, .git)
        # — this is what the template/preview code typically uses. If CWD
        # doesn't look like the repo root, fall back to walking up from this
        # file's location. Avoid assuming a fixed number of parent hops which
        # breaks in PR-preview, installed-package, or temporary test layouts.

        try:
            import src.cli.utils._repository_info
            repo_root = Path(src.cli.utils._repository_info.REPO_PATH)
        except Exception as e:
            logger.warning(f"Could not import repository path information. {str(e)}",
                            "Falling back to current working directory.")
            repo_root = Path(__file__).resolve()

        source_files = [
            ("src", "a2rchi_code"),
            ("pyproject.toml", "pyproject.toml"),
            ("LICENSE", "LICENSE"),
        ]

        for src, dst in source_files:
            src_path = repo_root / src
            dst_path = base_dir / dst
            logger.debug(f"Copying source from {src_path} to {dst_path}")
            if src_path.is_dir():
                if dst_path.exists():
                    shutil.rmtree(dst_path)
                shutil.copytree(src_path, dst_path)
            elif src_path.exists():
                shutil.copyfile(src_path, dst_path)
            else:
                raise FileNotFoundError(f"Source path {src_path} does not exist. Something went wrong in the repo structure.")
