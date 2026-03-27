#!/usr/bin/env python3
"""Test streaming events from the chat API."""
import json
import urllib.request

url = "http://localhost:2786/api/get_chat_response_stream"
payload = json.dumps({
    "last_message": "What is the marker text in the seed files?",
    "conversation_id": 1,
    "config_name": "pr_preview_config",
    "client_id": "61ca7f61-678d-4a19-857e-d6ff38ecbeb1",
    "include_agent_steps": True,
    "include_tool_steps": True,
}).encode()

req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
events = []

with urllib.request.urlopen(req, timeout=120) as resp:
    for line in resp:
        line = line.decode().strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            events.append(ev)
        except json.JSONDecodeError:
            pass

print("=== EVENT SEQUENCE ===")
builtin_tools = {"bash", "edit", "read_file", "grep", "task"}
tools_seen = set()

for i, ev in enumerate(events):
    t = ev.get("type", "?")
    extra = ""
    if t == "meta":
        extra = f" trace_id={ev.get('trace_id', '?')}"
    elif t == "tool_start":
        name = ev.get("tool_name", "?")
        tools_seen.add(name)
        extra = f" tool={name} args={json.dumps(ev.get('tool_args', {}))}"
    elif t == "tool_end":
        name = ev.get("tool_name", "?")
        tools_seen.add(name)
        extra = f" tool={name}"
    elif t == "tool_output":
        output_len = len(ev.get("output", ""))
        extra = f" output_len={output_len} truncated={ev.get('truncated', '?')}"
    elif t == "text":
        content = ev.get("content", "")
        extra = f" len={len(content)}"
        if len(content) > 50:
            extra += f" preview={content[:50]}..."
    elif t == "final":
        extra = f" msg_id={ev.get('archi_msg_id', '?')} conv_id={ev.get('conversation_id', '?')}"
    elif t == "usage":
        extra = f" prompt={ev.get('prompt_tokens')} completion={ev.get('completion_tokens')} total={ev.get('total_tokens')}"
    elif t == "thinking_start":
        extra = f" step_id={ev.get('step_id', '?')}"
    elif t == "thinking_end":
        extra = f" step_id={ev.get('step_id', '?')}"
    
    # Only print non-text events (too many text events)
    if t != "text":
        print(f"  [{i:3d}] {t}{extra}")
    if t == "error":
        print(f"       ERROR DETAIL: {json.dumps(ev)}")

# Summary
print()
text_count = sum(1 for e in events if e["type"] == "text")
print(f"Total events: {len(events)} ({text_count} text tokens + {len(events) - text_count} control events)")
print(f"Tools seen: {sorted(tools_seen)}")
print(f"Built-in tools detected: {tools_seen & builtin_tools if tools_seen & builtin_tools else 'NONE'}")

# Check for required event types
required = {"meta", "thinking_start", "text", "usage", "final"}
seen_types = {e["type"] for e in events}
missing = required - seen_types
print(f"Required event types present: {required & seen_types}")
if missing:
    print(f"MISSING required events: {missing}")
else:
    print("All required event types present: PASS")

# If tools were used, check for tool events
if tools_seen:
    tool_types = {"tool_start", "tool_output"} & seen_types
    print(f"Tool event types: {tool_types}")
