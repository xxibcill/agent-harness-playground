# Workflow Learning Ladder

This repository is an **educational project** for learning agent workflow patterns.
Each workflow in the sequence introduces exactly one new concept on top of the previous one.

## Canonical Workflow Order

| # | Workflow name | Status | Primary concept |
|---|--------------|--------|-----------------|
| 1 | `demo.echo` | Implemented | Direct response |
| 2 | `demo.route` | Implemented | Branching without tools |
| 3 | `demo.tool-single` | Planned | Single tool execution |
| 4 | `demo.tool-select` | Planned | Choosing among multiple tools |
| 5 | `demo.react.once` | Planned | One-shot reason then act |
| 6 | `demo.react` | Implemented | Looping reason-act cycle |
| 7 | `anthropic.respond` | Implemented | Provider-backed response |
| 8 | `anthropic.react` (capstone) | Implemented | Model-driven tool use |

## Teaching Notes

### 1. `demo.echo` -- Direct Response

The simplest smoke test in the repository. No branching, no tools, no external
calls. Given an input string it normalizes whitespace and returns `"Echo: <input>"`.

**What you learn:** the basic shape of a workflow -- receive input, produce
output, track token usage -- with zero moving parts.

### 2. `demo.route` -- Branching Without Tools

Introduces a LangGraph `StateGraph` with conditional edges. A `classify` node
sorts the input into one of four categories (greeting, question, command,
statement), then routes to the matching response node.

**What you learn:** how to branch inside a graph without yet thinking about
tool calls. The only new idea is *conditional routing*.

### 3. `demo.tool-single` -- Single Tool Execution

Calls exactly one deterministic tool (e.g. a calculator) embedded in the graph.
No selection logic -- the tool is always invoked.

**What you learn:** how a tool node fits into the graph. The only new idea is
*tool invocation as a graph node*.

### 4. `demo.tool-select` -- Choosing Among Multiple Tools

Registers several tools and adds a selection step that inspects the input to
pick the right one. Execution is still one-shot: pick a tool, call it, respond.

**What you learn:** *tool selection* -- inspecting input to decide which tool
to use. No looping yet.

### 5. `demo.react.once` -- One-shot Reason Then Act

Combines reasoning and tool selection into a single plan-then-execute pass.
The agent reasons about what to do, selects a tool, executes it, and responds
immediately -- no iteration.

**What you learn:** the *reason-act pattern* in its simplest one-pass form.

### 6. `demo.react` -- Looping Reason-Act Cycle

Extends the one-shot pattern from `demo.react.once` into a loop: reason, act,
observe the result, then reason again. After observing a tool result, the agent
incorporates that observation into its next reasoning pass before responding.
The graph cycles through the reason and use_tool nodes until no further tool
call is needed, then responds.

**What you learn:** *iteration inside a workflow graph* -- the core ReAct loop.
Unlike `demo.react.once`, this workflow can use multiple tools in sequence by
looping back through reasoning after each tool result.

### 7. `anthropic.respond` -- Provider-backed Response

The first workflow that calls an external LLM. Uses the Anthropic Messages API
to generate a response. Every concept before this point used deterministic logic;
this step introduces a *real model* as the reasoning engine.

**What you learn:** how to wire a provider API call into the same workflow
abstraction. The only new idea is *external model integration*.

### 8. `anthropic.react` (Optional Capstone) -- Model-driven Tool Use

Combines the ReAct loop from step 6 with the provider call from step 7. The
model decides which tools to call and when to stop iterating.

**What you learn:** how an LLM can drive tool selection and looping
autonomously -- the full agent pattern.

## Smoke Test

**`demo.echo` is the canonical smoke test.** It requires no API keys, no
external services, and no graph runtime. If `demo.echo` passes, the basic
workflow infrastructure is working.
