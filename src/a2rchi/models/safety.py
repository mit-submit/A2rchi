import torch
from transformers import AutoConfig, AutoModelForSeq2SeqLM, AutoTokenizer

from src.utils.logging import get_logger

logger = get_logger(__name__)


class SalesforceSafetyChecker:
    def __init__(self):
        config = AutoConfig.from_pretrained("Salesforce/safety-flan-t5-base")
        self.tokenizer = AutoTokenizer.from_pretrained("Salesforce/safety-flan-t5-base")
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            "Salesforce/safety-flan-t5-base", config=config
        )
        self.model.eval()

    def __call__(self, output_text):
        prefix = "Is the <Text> field safe or unsafe?"
        input_ids = self.tokenizer(prefix + " <Text> " + output_text + " <Context> ", return_tensors="pt").input_ids

        if len(input_ids[0]) > 512:
            logger.warning("Input length is > 512 token. Safety check result could be incorrect.")

        with torch.no_grad():
            outputs = self.model.generate(input_ids, output_scores=True, return_dict_in_generate=True, max_new_tokens=20)

        is_safe = self.tokenizer.decode(outputs.sequences[0], skip_special_tokens=True).split(" ")[0] == "safe"
        report = ""
        if not is_safe:
            true_false_ids = self.tokenizer("true false").input_ids[:2]
            keys = [
                "toxicity",
                "hate",
                "identity",
                "violence",
                "physical",
                "sexual",
                "profanity",
                "biased",
            ]
            scores = {}
            for k, i in zip(keys, range(3, 20, 2)):
                scores[k] = round(outputs.scores[i][0, true_false_ids].softmax(dim=0)[0].item(), 5)

            report += "|" + "|".join(f"{n:^10}" for n in scores.keys()) + "|\n"
            report += "|" + "|".join(f"{n:^10}" for n in scores.values()) + "|\n"
        return "Salesforce Content Safety Flan T5 Base", is_safe, report
