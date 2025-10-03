def read_prompt(prompt_filepath: str) -> str:
    try:
        with open(prompt_filepath, "r") as f:
            raw_prompt = f.read()

        return "\n".join(
            line for line in raw_prompt.split("\n") if not line.lstrip().startswith("#")
        )
    except FileNotFoundError:
        raise FileNotFoundError(f"Prompt file not found: {prompt_filepath}")

