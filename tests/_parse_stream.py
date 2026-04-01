"""Parse NDJSON stream output and summarize events."""
import sys, json

data = open(sys.argv[1]).read().strip()
# Find where the actual NDJSON starts (skip command echo)
lines = data.split('\n')
for line in lines:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue
    t = obj.get('type', '')
    if t == 'tool_start':
        print(f"TOOL_START: {obj.get('tool_name','?')}")
    elif t == 'tool_output':
        out = obj.get('output', '')
        print(f"TOOL_OUTPUT: ({len(out)} chars) {out[:120]}...")
    elif t == 'final':
        resp = obj.get('response', '')
        print(f"FINAL (len={len(resp)}): {resp[:300]}...")
        print(f"  usage: {obj.get('usage')}")
        print(f"  model: {obj.get('model_used')}")
        print(f"  conversation_id: {obj.get('conversation_id')}")
    elif t == 'chunk':
        pass
    elif t == 'meta':
        pass
    else:
        print(f"{t}: {str(obj)[:150]}")
