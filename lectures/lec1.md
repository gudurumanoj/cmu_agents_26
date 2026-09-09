Tool calls neednt just be always formatted as 
```text
<tool_call>
    < THe tool call json function etc >
</tool_call>
```

The LLM can just output the tool call code directly like 
```bash
ls | grep "*.py"
```
instead of using a separate tool call like `listdir` or something etc. In this case we will extract it and directly execute the code while in the former case we will have to parse the tool call json and then execute the code.

Both designs have their own pros and cons.

Some issues or common mishaps with agents:
- Too verbos
- Too slow
- Deleting files without backup/consent etc
