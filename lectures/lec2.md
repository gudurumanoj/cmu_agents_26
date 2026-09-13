## Tool Use for LM Agents
Tool is basically an interface through which a language model can invoke an external computer program. Examples include search, calculator, brower, python, api etc
Model by itself cannot do this because its basically a next token predictor and cannot do any external computation.
Tools enable the model to do something it cannot do by itself and also help/delegate operations which it can do but not efficiently.

Obv calling tools is only beneficial when their benefits outweigh latency, cost of the tool, failure and risks of the tool.

So how to decide/design the tools we want?
- Start from the abilities we want, then define a narrow interface for each kind of ability/interaction

Differnt tool examples:
- To end a loop, we can either expose it as a tool to the agent or can do it at a harness level where agent would finish and then we'd make a control call kind of thing
- code execution
- web search or info retrieval
- image generation
- custom tools

Code as a meta tool
- Code composes of control flow, libraries and multiple operations in one action
- Which implies it can call multiple tools in one action in code because multiple code tool apis can be called in one action in code making it much more richer and efficient -- From CodeAct paper
- Offers loops, variables, libraries all in one place to use sequential
    - Can get stuck in infinite loops, security issues in libraries
    - Harder to constrain since it has a broad action space
- Higher impact as in it uses sandboxing, resource limits, permissions and audit logs etc

The tool call tokens like `<tool_call>` etc neednt exactly be part of the tokenizer vocab while pre-training. While most of the latest llms do have it in their vocab and it gets trained as part of sft/mid-training where it sees high quality text etc. Since pre-training is training across the entire web, these `<tool_call>` neednt exactly be present in the vocab

For an LLM, everything is represented as a string. So whatever user facing or tool calls or tool responses etc exist, everything is translated into string format with "special tokens" to denote these separatinos of various roles/things

Every model has a different set of special tokens and they are not exactly the same. So each model has its own way of representing the same thing differently, hence each model comes with its own parser and each has its own tool definitions 

If finetunign from an already well trained model, we do want to maintain the template and all thats already been used to train and not try to change that behaviour

Using a tool call:
- Register at initialization time
    - tool spec + resolve + configure and have a `tools_map[name]` python fucntion to effectively execute/call/map tool calls
- Dispatch each model call
    - Parse, lookup, validate the args, execute and return the observation/tool call result. Validating and execution can result in errors!

Should have an id or something to match the tool call with the tool call result


#### Constrained Decoding
LMs arent guarenteed to generate valid tool calls in terms of structure like missing json brackets, commas etc
Json schema to let the model know about the tool call args and data types etc
Tool call constraints: Syntax (valid json), Shape (Expected fields), Types (data types), Values (allowed values/units)
Can check validity of schema's by using "grammars" to be more specific context-free grammar since json schema cannot be expressed via regular expressions. Push down automata for cfg while Finite State Automata for regular expressions

Token Masking: 
**XGrammer**: developed by peorple at cmu ml systems
Based on the output logits of llm inference and a valid set of tokens mask (built based on the structure and rules for allowed tokens), the output logits are masked and softmax is computed only on the allowed tokens adn sampled from there. This ensures we would generate valid outputs helping us reduce the chances of ill-formed tool calls

Exposing APIs using FastAPI in terms of REST API functions and not as http calls types -- gotta check/understand this properly

#### MCP
FastMCP - Designed to be fast mcp like fastapi

MCP - gives us an extra layer of security, which could be a major reason for using it because mcp has an mcp token and we only expose that key to the agent while other api keys like aws/openai/github tokens remain in the mcp server and we dont expose them to the agent and this way it wont be able to push those tokens to github publicly and cant misuse them, hence its good to use it for security reason

![Rest API v MCP diffs](assets/lec2/rest_v_mcp.png)

There's an official mcp registries, and also multiple mcp servers are present over web, can go through to understand how to code them, use them etc

#### Orchestrating multiple calls
Its parallel tool calling, ofc to reduce tool call latency by moving from sequential to parallel
We want to expose generating multiple tool calls in one action whcih are independent of each other and can be executed in parallel and use async/await to trigger them in parallel for the tool calls it generates
Since these calls can be called parallely, it also reduces cost to some extent. Costlier models actually do this and because of this ability overall task cost could go down despite them being costly per 1M tokens. This behaviour is induced via RL by incentivizing the model to finish faster

#### Evaluating tool call usage ability of LLMs
BFCL dataset
- v4 has agentic and tool call robustness, format sensitivity and all etc

![Tool Call Usage Evaluation](assets/lec2/tool_call_eval_stack.png)

Other things like
- Efficiency: Latency, cost, calls of the model using tools. We want the agent to be cost effective and fast in addition to it being correct and accurate
- Reliability: Retrying if tool call times out, if a tool isnt workign using a different tool etc
- Safety: Intended usage of tools

Provider tool call error rates
- Depending on the provider of the model (like together, fireworks etc) the tool call error rates vary because the model is served differently like decoding algo (speculative decoding and its variants like lossless, lossy), quantization etc
- This happens because they optimize for different things like cost, latency, accuracy etc
- Different providers might also be using different constraint decoding algos implemented from scratch. vLLM, SGLang have differnt kinds of these constrained decoding algos or they might even have the entire inference stack built from scratch