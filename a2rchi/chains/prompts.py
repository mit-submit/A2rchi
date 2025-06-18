# flake8: noqa
from langchain_core.prompts import PromptTemplate
from a2rchi.utils.config_loader import Config_Loader
from a2rchi.chains.utils.prompt_validators import ValidatedPromptTemplate
from a2rchi.chains.utils.prompt_validators import (
    validate_main_prompt,
    validate_condense_prompt,
    validate_image_prompt,
    validate_grading_summary_prompt,
    validate_grading_analysis_prompt,
    validate_grading_final_grade_prompt
)

config = Config_Loader().config["chains"]["prompts"]

if config["MAIN_PROMPT"]:
    print("main prompt there")

if config["IMAGE_PROCESSING_PROMPT"]:
    print("image processing prompt there")

prompt_config = {
    "QA": {
        "path": config["MAIN_PROMPT"],
        "input_variables": ["context", "question"],
        "validate": validate_main_prompt,
    },
    "CONDENSE_QUESTION": {
        "path": config["CONDENSING_PROMPT"],
        "input_variables": ["chat_history", "question"],
        "validate": validate_condense_prompt,
    },
    "IMAGE_PROCESSING": {
        "path": config["IMAGE_PROCESSING_PROMPT"],
        "input_variables": [],
        "validate": validate_image_prompt  # don't think there is anything to validate here...,
    },
    "GRADING_SUMMARY": {
        "path": config["GRADING_SUMMARY_PROMPT"],
        "input_variables": ["final_student_solution"],
        "validate": validate_grading_summary_prompt,
    },
    "GRADING_ANALYSIS": {
        "path": config["GRADING_ANALYSIS_PROMPT"],
        "input_variables": ["official_explanation", "final_student_solution", "solution_summary"],
        "validate": validate_grading_analysis_prompt,
    },
    "GRADING_FINAL_GRADE": {
        "path": config["GRADING_FINAL_GRADE_PROMPT"],
        "input_variables": ["final_student_solution", "official_explanation", "analysis_result"],
        "validate": validate_grading_final_grade_prompt,
    }
}

# need these for deciding which prompts to template/exist?:
grading_summary = Config_Loader().config["chains"]["chain"]["GRADING_SUMMARY_MODEL_NAME"]
grading_analysis = Config_Loader().config["chains"]["chain"]["GRADING_ANALYSIS_MODEL_NAME"]

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
        template=read_prompt(info["path"]),
        input_variables=info["input_variables"],
        validator=info["validate"]
    )
    for name, info in prompt_config.items() if info["path"] != ""
}