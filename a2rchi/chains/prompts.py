from a2rchi.utils.config_loader import load_config
from a2rchi.chains.utils.prompt_validator import ValidatedPromptTemplate

config = load_config()["chains"]["prompts"]

def read_prompt(prompt_filepath: str) -> str:
    try:
        with open(prompt_filepath, "r") as f:
            raw_prompt = f.read()

        return "\n".join(
            line for line in raw_prompt.split("\n") if not line.lstrip().startswith("#")
        )
    except FileNotFoundError:
        raise FileNotFoundError(f"Prompt file not found: {prompt_filepath}")

PROMPTS = {
    name: ValidatedPromptTemplate(
        name=name,
        prompt_template=read_prompt(path),
    )
    for name, path in config.items() if path != ""
}