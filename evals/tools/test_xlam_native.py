import urllib.request, json

tools = [
    {
        "name": "run_sandboxed_python",
        "description": "Execute Python code in a secure, isolated sandbox. Use for calculations, data analysis, or generating output.",
        "parameters": {
            "code": {"type": "string", "description": "Python code to execute"}
        }
    },
    {
        "name": "run_host_command",
        "description": "Execute shell commands on host system.",
        "parameters": {"command": {"type": "string"}}
    }
]

task_instruction = (
    "You are an expert tool selector. You are given a user request and available functions. "
    "Select all functions that will be needed to accomplish the user's goal. "
    "If no function is relevant, return an empty list."
)

format_instruction = (
    'Output in JSON format:\n'
    '{\n'
    '    "tool_calls": [\n'
    '        {"name": "func_name"}\n'
    '    ]\n'
    '}'
)

query = "Run a Python script to calculate the first 20 fibonacci numbers"

prompt = (
    f"[BEGIN OF TASK INSTRUCTION]\n{task_instruction}\n[END OF TASK INSTRUCTION]\n\n"
    f"[BEGIN OF AVAILABLE TOOLS]\n{json.dumps(tools)}\n[END OF AVAILABLE TOOLS]\n\n"
    f"[BEGIN OF FORMAT INSTRUCTION]\n{format_instruction}\n[END OF FORMAT INSTRUCTION]\n\n"
    f"[BEGIN OF QUERY]\n{query}\n[END OF QUERY]\n\n"
)

req = urllib.request.Request(
    "http://localhost:1234/v1/chat/completions",
    data=json.dumps({
        "model": "xlam-1b-fc-r",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 256
    }).encode(),
    headers={"Content-Type": "application/json"}
)

with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode())
    print("PYTHON SCRIPT TEST RESULT:")
    print(data["choices"][0]["message"]["content"])
