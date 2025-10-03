from typing import Any, Dict, List, Optional

from langchain.callbacks.manager import CallbackManagerForChainRun
from langchain.chains.llm import LLMChain  # deprecated, should update


class ImageLLMChain(LLMChain):
    """
    LLMChain but overriding _call method to ensure it points to custom LLM
    """

    def _call(
        self,
        inputs: Dict[str, Any],
        run_manager: Optional[CallbackManagerForChainRun] = None,
    ) -> Dict[str, str]:
        images = inputs.get("images", [])
        
        # format prompt ourself now
        prompt_inputs = {k: v for k, v in inputs.items() if k != "images"}
        prompt = self.prompt.format(**prompt_inputs)
        
        # directly calling HuggingFaceImageLLM's _call method
        response = self.llm._call(
            prompt=prompt,
            images=images,
            run_manager=run_manager.get_child() if run_manager else None,
        )
        
        return {"text": response}