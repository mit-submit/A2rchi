from typing import Any, Dict, List, Optional
from langchain_classic.chains.llm import LLMChain  # deprecated, should update

class ImageLLMChain(LLMChain):
    """
    LLMChain but overriding _call method to ensure it points to custom LLM
    """

    def _call(
        self,
        inputs: Dict[str, Any],
    ) -> Dict[str, str]:
        images = inputs.get("images", [])
        
        # format prompt ourself now
        prompt_inputs = {k: v for k, v in inputs.items() if k != "images"}
        prompt = self.prompt.format(**prompt_inputs)
        
        # directly calling HuggingFaceImageLLM's _call method
        response = self.llm._call(
            prompt=prompt,
            images=images,
        )
        
        return {"text": response}