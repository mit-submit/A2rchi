from typing import Tuple
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

class PromptFormatter:
    def __init__(self, tokenizer, image=False):
        self.tokenizer = tokenizer
        self.special_tokens = tokenizer.special_tokens_map
        self.has_chat_template = hasattr(tokenizer, "chat_template") and tokenizer.chat_template is not None
        self.image = image # can include a image processing prompt formatting later if needed or wanted...

    # choosing a function according to the prompt, then some string manipulation to input correct special tokens (depends on llm) in correct places
    # returns a tuple of (formatted_prompt, end_tag), where end_tag signifies where the generated text begins (the end of the prompt)
    def format_prompt(self, prompt: str) -> Tuple[str, str]:
        if "Context:" in prompt and "Question:" in prompt:
            return self._submit_qa_prompt_formatting(prompt)

        elif "Chat History:" in prompt and "Follow Up Input:" in prompt:
            return self._submit_condense_prompt_formatting(prompt)

        else:
            return self._grading_prompt_formatting_basic(prompt)

    def _submit_qa_prompt_formatting(self, prompt: str) -> Tuple[str, str]:

        context_start = prompt.find("Context:")
        question_start = prompt.rfind("Question:")

        if "[INST]" in self.special_tokens.get("additional_special_tokens", []):
            logger.info("Using instructor template")
            return f"[INST] {prompt} [/INST]", "[/INST]"

        elif "<|im_start|>" in self.special_tokens.get("additional_special_tokens", []) and context_start != -1 and question_start != -1:
            logger.info("Using chat template for QA prompt")
            question_end = prompt.rfind("Helpful Answer:") if 'Helpful Answer:' in prompt else len(prompt)
            message = [
                {"role": "system", "content": prompt[:context_start]},
                {"role": "assistant", "content": prompt[context_start + len("Context:"):question_start]},
                {"role": "user", "content": prompt[question_start + len("Question:"):question_end]},
            ]
            return self.tokenizer.apply_chat_template(message, tokenize=False, add_generation_prompt=True), "assistant"

        else:
            logger.info("Using default formatting")
            return prompt, prompt[len(prompt)-15:]

    
    def _submit_condense_prompt_formatting(self, prompt: str) -> Tuple[str, str]:

        chat_history_start = prompt.find("Chat History:")
        follow_up_input_start = prompt.rfind("Follow Up Input:")

        if "[INST]" in self.special_tokens.get("additional_special_tokens", []):
            logger.info("Using instructor template")
            return f"[INST] {prompt} [/INST]", "[/INST]"

        elif "<|im_start|>" in self.special_tokens.get("additional_special_tokens", []) and chat_history_start != -1 and follow_up_input_start != -1:
            logger.info("Using chat template for condense prompt")
            message = [
                {"role": "system", "content": prompt[:chat_history_start].strip()},
                {"role": "user", "content": prompt[chat_history_start + len("Chat History:"):follow_up_input_start].strip()},
                {"role": "user", "content": prompt[follow_up_input_start + len("Follow Up Input:"):].strip()},
            ]
            return self.tokenizer.apply_chat_template(message, tokenize=False, add_generation_prompt=True), "assistant"

        else:
            logger.info("Using default formatting")
            return prompt, prompt[len(prompt)-15:]


    def _grading_prompt_formatting_basic(self, prompt: str) -> Tuple[str, str]:

        if "[INST]" in self.special_tokens.get("additional_special_tokens", []):
            logger.info("Using instructor template")
            return f"[INST] {prompt} [/INST]", "[/INST]"

        elif "<|im_start|>" in self.special_tokens.get("additional_special_tokens", []):
            logger.info("Using chat template")
            message = [
                {"role": "user", "content": prompt}
            ]
            return self.tokenizer.apply_chat_template(message, tokenize=False, add_generation_prompt=True), "assistant"

        else:
            logger.info("Using default formatting")
            return prompt, prompt[len(prompt)-15:]