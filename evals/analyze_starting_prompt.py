with open('docs/logs/litellm_prompt_dump.txt', 'r', encoding='utf-8', errors='ignore') as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - 150000))
    chunk = f.read()

entries = chunk.split('=' * 40)
# Find entry where tools are dumped
for e in entries:
    if '--- TOOLS (count)' in e:
        tools_part = e.split('--- TOOLS (count)')[1]
        print("Tools block length:", len(tools_part), "chars (~", len(tools_part)//4, "tokens)")
        # Print first 500 chars of tools
        print("Sample of tools:\n", tools_part[:600])
        break
