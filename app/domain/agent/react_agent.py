"""ReAct Agent — powered by LangGraph StateGraph.

State machine:
    ┌─────────┐   has action?    ┌───────┐
    │  agent  │ ──── YES ──────→ │ tools │
    │ (think) │                  │(exec) │
    └────┬────┘                  └───┬───┘
         │ NO                        │
         ▼                           │
       END  ◄────────────────────────┘
                (back to agent)

Uses our own LLMClient interface — no langchain-openai dependency.
"""

import json
import time
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.core.logger.logger import get_logger
from app.domain.agent.agent import AgentResult, AgentRole, AgentStep, BaseAgent
from app.domain.llm.llm_client import LLMClient, LLMMessage
from app.domain.tool.registry import ToolRegistry

logger = get_logger(__name__)

MAX_ITERATIONS = 8

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a knowledgeable and helpful AI assistant. You have access to the following tools:

{tool_descriptions}

To use a tool, respond EXACTLY in this format (no extra text):

 Thought: <your reasoning>
 Action: <tool_name>
 Action Input: <JSON arguments>

After receiving a tool result, continue reasoning or give your final answer:

 Thought: <your reasoning>
 Final Answer: <your answer to the user>

Rules:
- Always start with a Thought.
- If you already know the answer, give it directly as Final Answer WITHOUT using any tools.
- Only use tools when you genuinely lack the knowledge (e.g. real-time info, or a very specific fact you are unsure about).
- If a tool returns no useful results, DO NOT retry. Use your own knowledge to answer instead.
- Never call the same tool more than once for the same question.
- Give rich, detailed answers. Include explanations, examples, and context to help the user truly understand the topic.
- Structure longer answers with clear sections or bullet points when appropriate.
- Answer in the user's language."""


def _build_tool_descriptions(registry: ToolRegistry) -> str:
    """Format tool metadata for the system prompt."""
    lines = []
    for tool_meta in registry.list_tools():
        params = tool_meta.get("parameters", {})
        props = params.get("properties", {})
        param_str = ", ".join(
            f"{k}: {v.get('type', 'any')}" for k, v in props.items()
        )
        lines.append(
            f"- **{tool_meta['name']}**({param_str}): {tool_meta['description']}"
        )
    return "\n".join(lines) if lines else "(no tools available)"


# ---------------------------------------------------------------------------
# Graph State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """State that flows through the LangGraph state machine."""

    messages: list[LLMMessage]
    steps: list[AgentStep]
    answer: str | None
    done: bool
    iteration: int
    max_iterations: int
    total_tokens: int


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def _parse_llm_output(
    text: str,
) -> tuple[str, str | None, dict | None, str | None]:
    """Parse ReAct-formatted LLM output.

    Returns:
        (thought, action, action_input, final_answer)

    Handles multi-line Final Answer, treats empty Action as no action,
    and ignores orphaned Action Input lines (Action Input without Action).
    """
    thought = ""
    action = None
    action_input = None
    final_answer = None

    lines = text.strip().split("\n")
    final_answer_lines: list[str] = []
    in_final_answer = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("Thought:"):
            thought = stripped[len("Thought:"):].strip()
            in_final_answer = False
        elif stripped.startswith("Action:"):
            raw = stripped[len("Action:"):].strip()
            if raw and raw.lower() != "action input:":
                action = raw
            in_final_answer = False
        elif stripped.startswith("Action Input:"):
            raw = stripped[len("Action Input:"):].strip()
            if action is not None:
                # Only parse Action Input if we have a valid Action
                try:
                    action_input = json.loads(raw)
                except json.JSONDecodeError:
                    action_input = {"query": raw}
            # else: orphaned Action Input — ignore it
            in_final_answer = False
        elif stripped.startswith("Final Answer:"):
            # Start collecting multi-line final answer
            first_line = stripped[len("Final Answer:"):].strip()
            if first_line:
                final_answer_lines.append(first_line)
            in_final_answer = True
        elif in_final_answer:
            # Continue collecting final answer lines
            if stripped:
                final_answer_lines.append(stripped)

    if final_answer_lines:
        final_answer = "\n".join(final_answer_lines)

    return thought, action, action_input, final_answer


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def _make_agent_node(llm: LLMClient, registry: ToolRegistry):
    """Create the 'agent' node — calls LLM and parses the response.

    Validates tool names against the registry to prevent executing
    malformed output like 'Action Input:' as a tool.
    """

    async def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        iteration = state["iteration"]
        total_tokens = state.get("total_tokens", 0)

        logger.info("langgraph_agent_think", iteration=iteration + 1)
        response = await llm.invoke(messages)
        raw_text = response.content

        # Accumulate token usage
        if response.usage:
            total_tokens += response.usage.total_tokens

        thought, action, action_input, final_answer = _parse_llm_output(raw_text)

        # Validate tool name — reject malformed output.
        # Handle "Action: Final Answer" (LLM puts Final Answer after Action:)
        if action and not registry.has(action):
            if action.lower() == "final answer":
                # LLM wrote Action: Final Answer — extract the real answer
                # from the Action Input or raw text
                if action_input and isinstance(action_input, dict):
                    final_answer = action_input.get("answer") or action_input.get("query") or ""
                if not final_answer:
                    # Try to extract from raw text after "Final Answer"
                    for line in raw_text.split("\n"):
                        stripped = line.strip()
                        if stripped.lower().startswith("final answer:"):
                            final_answer = stripped[len("final answer:"):].strip()
                            break
                if not final_answer:
                    final_answer = thought
                action = None
                action_input = None
            else:
                logger.warning(
                    "agent_invalid_tool_name",
                    action=action,
                    hint="Treating as no-action, falling back to thought",
                )
                action = None
                action_input = None

        if final_answer is not None:
            step = AgentStep(
                thought=thought,
                action="respond",
                action_input={},
                observation=final_answer,
            )
            return {
                "steps": state["steps"] + [step],
                "answer": final_answer,
                "done": True,
                "iteration": iteration + 1,
                "total_tokens": total_tokens,
            }

        if action:
            step = AgentStep(
                thought=thought,
                action=action,
                action_input=action_input or {},
            )
            # Append LLM output + placeholder for observation
            return {
                "messages": messages
                + [
                    LLMMessage(role="assistant", content=raw_text),
                    LLMMessage(role="user", content="__PENDING_OBSERVATION__"),
                ],
                "steps": state["steps"] + [step],
                "done": False,
                "iteration": iteration + 1,
                "total_tokens": total_tokens,
            }

        # No recognizable action or final answer — treat as final.
        # Strip empty Action/Action Input lines so the user doesn't see
        # raw ReAct scaffolding in the answer.
        clean_answer = raw_text
        if thought and not action and not final_answer:
            clean_answer = thought

        step = AgentStep(
            thought=thought,
            action="respond",
            action_input={},
            observation=clean_answer,
        )
        return {
            "steps": state["steps"] + [step],
            "answer": clean_answer,
            "done": True,
            "iteration": iteration + 1,
            "total_tokens": total_tokens,
        }

    return agent_node


def _make_tools_node(registry: ToolRegistry):
    """Create the 'tools' node — executes the last action.

    Includes a duplicate-call guard: if the agent calls the same tool
    with the same arguments it already tried, the observation instructs
    it to use its own knowledge instead of retrying.
    """

    async def tools_node(state: AgentState) -> dict:
        steps = state["steps"]
        last_step = steps[-1]

        # Check for duplicate tool calls
        current_call = (last_step.action, json.dumps(last_step.action_input, sort_keys=True))
        previous_calls = {
            (s.action, json.dumps(s.action_input, sort_keys=True))
            for s in steps[:-1]
            if s.action not in ("respond",)
        }

        if current_call in previous_calls:
            observation = (
                "You already tried this exact tool call and it didn't help. "
                "DO NOT call this tool again. Use your own knowledge to give "
                "a Final Answer now."
            )
            logger.info(
                "langgraph_duplicate_tool_call",
                tool=last_step.action,
            )
        else:
            logger.info(
                "langgraph_tool_exec",
                tool=last_step.action,
                args=list(last_step.action_input.keys()),
            )
            result = await registry.execute(
                last_step.action, **last_step.action_input
            )
            observation = result.output if result.success else f"Error: {result.error}"

        # Update the last step with observation
        updated_step = AgentStep(
            thought=last_step.thought,
            action=last_step.action,
            action_input=last_step.action_input,
            observation=observation,
        )
        updated_steps = steps[:-1] + [updated_step]

        # Replace the pending observation message with actual observation
        messages = state["messages"]
        if messages and messages[-1].content == "__PENDING_OBSERVATION__":
            messages = messages[:-1]
        messages = messages + [
            LLMMessage(
                role="user",
                content=f"Observation: {observation}\n\nContinue reasoning or give your Final Answer.",
            )
        ]

        return {
            "messages": messages,
            "steps": updated_steps,
        }

    return tools_node


# ---------------------------------------------------------------------------
# Conditional edge
# ---------------------------------------------------------------------------

def _should_continue(state: AgentState) -> str:
    """Route to 'tools' if there's an action, or END if done."""
    if state["done"]:
        return "end"
    if state["iteration"] >= state.get("max_iterations", MAX_ITERATIONS):
        return "end"
    return "tools"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_react_graph(
    llm: LLMClient,
    registry: ToolRegistry,
) -> StateGraph:
    """Build and compile the ReAct LangGraph state machine.

    Returns a compiled graph ready for invocation.
    """
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("agent", _make_agent_node(llm, registry))
    graph.add_node("tools", _make_tools_node(registry))

    # Entry point
    graph.set_entry_point("agent")

    # Conditional edges from agent
    graph.add_conditional_edges(
        "agent",
        _should_continue,
        {
            "tools": "tools",
            "end": END,
        },
    )

    # Tools always go back to agent
    graph.add_edge("tools", "agent")

    return graph.compile()


# ---------------------------------------------------------------------------
# Public API — same interface as before
# ---------------------------------------------------------------------------

class ReActAgent(BaseAgent):
    """LangGraph-powered ReAct agent.

    Same interface as before — `run(query)` returns `AgentResult`.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        role: AgentRole = AgentRole.EDGE,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self.role = role
        self._llm = llm_client
        self._tools = tool_registry
        self._max_iterations = max_iterations
        self._graph = build_react_graph(llm_client, tool_registry)

    @property
    def tool_registry(self) -> ToolRegistry:
        """Return the tool registry used by this agent."""
        return self._tools

    async def run(self, query: str, context: dict | None = None) -> AgentResult:
        """Execute the ReAct loop via LangGraph."""
        start_time = time.monotonic()

        system_prompt = _SYSTEM_PROMPT.format(
            tool_descriptions=_build_tool_descriptions(self._tools)
        )

        initial_state: AgentState = {
            "messages": [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=query),
            ],
            "steps": [],
            "answer": None,
            "done": False,
            "iteration": 0,
            "max_iterations": self._max_iterations,
            "total_tokens": 0,
        }

        logger.info("langgraph_agent_start", query=query)

        # Run the graph
        final_state = await self._graph.ainvoke(initial_state)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        answer = final_state.get("answer") or "I couldn't complete the task."
        steps = final_state.get("steps", [])
        total_tokens = final_state.get("total_tokens", 0)

        logger.info(
            "langgraph_agent_done",
            steps=len(steps),
            latency_ms=round(elapsed_ms, 1),
        )

        return AgentResult(
            answer=answer,
            steps=steps,
            total_tokens=total_tokens,
            latency_ms=round(elapsed_ms, 1),
        )
