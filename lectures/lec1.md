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
- Too verbose
- Too slow
- Deleting files without backup/consent etc
- Forget things, especially when context becomes large or compactions happen etc
- Misunderstand what we say
- Accessing things that they shouldn't be accessing
- Random knowledge gaps
- Writing code that doesn't work or too much code when less code is good
- Not knowing when to push back when doing unknown things
- Not making accurate tool calling when context becomes too big
- Customizability of the agent is limited
- Complex task management is necessary
- Proper understanding of the environment


Two ways to address these issues:
- LLM training: Complex and better solves the issue at root level this but very costly
    - Changing the model behaviour through training, mid/post
    - Teaching the reusable patterns for reasoning, tool use and recovery and making them native
    - Capabilities become part of the model
- Harness Engineering: BUilding the environment in a way that we can add custom logic to the agent and make it more customizable and add workflows around the model
    - System around the model -- like context, validation, retires, tests, safety boundaries etc
    - Capabilities emerge from the model-harness combination

Typically, we observe problems and we first try to fix them in harness first and if its a really serious repetitive problem, we then try to fix it in the model by training since harness engineering is much cheaper and faster
Some things are better suited for harness engineering and some for model training.

The smaller the model, the more it needs harness engineering to be effective. The larger model can generalize better and doesnt need as much harness engineering as the smaller model, though the larger model's capabilities can also be enhanced through harness engineering on tasks


Mid training/SFT and RL for training models:
- Mid training/SFT:
    - Training the model on a dataset of interactions with the environment like traces, tool call formats and full end to end demonstrations to instill some capabilities
- RL:
    - RL using rewards/preferences for reasoning etc
    - Agentic RL training by simulating the environment and around the harness to change trajectory level behaviour

Capability wise examples:
| Capability | Harness Engineering | LLM Training |
|------------|---------------------|----------------|
| Accurate tool calling | Grammar-constrained decoding in harness | Training on tool call formats, typically sft |
| Coherence over Long Context | - Context compression<br>- History, summary, memory, retrieval based lookups<br>- Sub agent delegation | Training on long context capabilities |
| Customizability | - Agent Memory<br>- Skills<br>- Custom tools for tasks | RL |
|Complex task management | - Plan mode<br>- Task decomposition<br>- Task prioritization<br>- Task scheduling<br>- Sub agent delegation | Training on complex long horizon tasks, typically RL |
| Environment Understanding | Learning skills corresponding to domain knowledge | Training on domain knowledge sft with well defined traces/rl with corresponding domain-specific environments |
| Safety | - Sandboxing<br>- Limit access to credentials, data, files etc<br>- Monitoring trajectories<br> - Guardrails | Safety aware RL |


Agents are systems, not just models and they are complex in nature
- Harness: Software with lot of moving parts
    - context, tools, memory, plannning etc
- Sandbox: Access and permissoin boundaries
- Need to do inference/use llm api
    - Context length issues
    - Caching
- Monitoring

Examples of different softwares
- COding agents: Claude Code, Codex, OpenHands, OpenCode, Pi. Simpler in interaction but has a lot of tools adn action spaces
- Orchestrators: Langgraph, CrewAI etc. MOre guardrail and less action spaces but more control and flexibility

Sandox:
- Isolate code and tool execution
- Limit cmpute, network, file access etc
- FOr eval and monitoring purposes, creating reproducible environments
- Softwares: Docker/Apptainer, Modal/Sail cloud providers

Inference:
- Caching matters a lot for speed and cost
- Serving model generations reliably
- Batch requests and reusing the KV Cache
- Managing streaming, parallelism, throughput
- Softwares: vLLM, SGLang, cloud api proviers like together/fireworks

Training Systems:
- Preparing data and collecting rollouts
- Coordinating distribured workers
- Rewards, checkpoints, updating weights and reproducible runs
- Sofrwares: SKyRL, Miles

Observability and monitoring:
- Capture traces and metrics
- Track quality, cost, failures and many other like tokens spent per task etc
- Compare trajectories, evlauations and be able to do analysis
- Softwares: Laminar/MLFlow/Transluce

Designing evaluations for multi-step tasks is really essential for long horizon tasks and complex tasks training