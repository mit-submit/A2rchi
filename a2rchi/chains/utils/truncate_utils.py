from typing import Optional

def truncate_prompt(prompt: str, tokenizer, max_model_len: Optional[int] = None) -> str:
    print("[VLLM DEBUG] Full original prompt:\n", prompt)

    if "Chat History:\nUser:" not in prompt:
        return prompt

    header, chat = prompt.split("Chat History:\nUser:", maxsplit=1)
    user_blocks = chat.split("User:")
    user_blocks = ["User:" + block for block in user_blocks if block.strip()]

    max_blocks = min(10, len(user_blocks))
    while max_blocks > 0:
        truncated_body = ''.join(user_blocks[-max_blocks:])
        final_prompt = f"{header}Chat History:\n{truncated_body}"
        input_ids = tokenizer(final_prompt, return_tensors="pt", truncation=False).input_ids[0]
        if max_model_len is None or len(input_ids) <= max_model_len:
            print(f"[VLLM DEBUG] Using last {max_blocks} User: blocks. Token length = {len(input_ids)}")
            print("[VLLM DEBUG] Final prompt after truncation:\n", final_prompt)
            return final_prompt
        max_blocks -= 1

    fallback_body = ''.join(user_blocks[-1:]) if user_blocks else ""
    fallback_input_ids = tokenizer(fallback_body, return_tensors="pt", truncation=False).input_ids[0]
    truncated_ids = fallback_input_ids[-max_model_len:]
    truncated_body = tokenizer.decode(truncated_ids, skip_special_tokens=True)
    final_prompt = f"{header}Chat History:\n{truncated_body}"
    print("[VLLM DEBUG] Fallback token-level truncation used.\n", final_prompt)
    return final_prompt

