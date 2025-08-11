# flake8: noqa
from langchain_core.prompts import PromptTemplate
from a2rchi.utils.config_loader import load_config
from a2rchi.chains.utils.prompt_validator import ValidatedPromptTemplate

config = load_config()["chains"]["prompts"]

prompt_config = {
    "QA": {
        "path": config["MAIN_PROMPT"],
        "input_variables": ["context", "question"],
    },
    "CONDENSE_QUESTION": {
        "path": config["CONDENSING_PROMPT"],
        "input_variables": ["chat_history", "question"],
    },
    "IMAGE_PROCESSING": {
        "path": config["IMAGE_PROCESSING_PROMPT"],
        "input_variables": [],
    },
    "GRADING_SUMMARY": {
        "path": config["GRADING_SUMMARY_PROMPT"],
        "input_variables": ["submission_text"],
    },
    "GRADING_ANALYSIS": {
        "path": config["GRADING_ANALYSIS_PROMPT"],
        "input_variables": ["rubric_text", "submission_text", "summary"],
    },
    "GRADING_FINAL_GRADE": {
        "path": config["GRADING_FINAL_GRADE_PROMPT"],
        "input_variables": ["submission_text", "rubric_text", "analysis"],
    }
}

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
        prompt_template=read_prompt(info["path"]),
        input_variables=info["input_variables"]
    )
    for name, info in prompt_config.items() if info["path"] != ""
}