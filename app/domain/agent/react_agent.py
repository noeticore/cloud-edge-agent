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
You are a helpful AI assistant. You have access to the following tools:

{tool_descriptions}

To use a tool, respond EXACTLY in this format (no extra text):

 Thought: <your reasoning>
 Action: <tool_name>
 Action Input: <JSON arguments>

After receiving a tool result, continue reasoning or give your final answer:

 Thought: <your reasoning>
 Final Answer: <your answer to the user>

Rules:
- You may call multiple tools in sequence before giving a final answer.
- Always start with a Thought.
- If you don't need a tool, go directly to Final Answer.
- Be concise and helpful."""


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


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def _parse_llm_output(
    text: str,
) -> tuple[str, str | None, dict | None, str | None]:
    """Parse ReAct-formatted LLM output.

    Returns:
        (thought, action, action_input, final_answer)
    """
    thought = ""
    action = None
    action_input = None
    final_answer = None

    for line in text.strip().split("\n"):
        stripped = line.strip()
        if stripped.startswith("Thought:"):
            thought = stripped[len("Thought:"):].strip()
        elif stripped.startswith("Action:"):
            action = stripped[len("Action:"):].strip()
        elif stripped.startswith("Action Input:"):
            raw = stripped[len("Action Input:"):].strip()
            try:
                action_input = json.loads(raw)
            except json.JSONDecodeError:
                action_input = {"query": raw}
        elif stripped.startswith("Final Answer:"):
            final_answer = stripped[len("Final Answer:"):].strip()

    return thought, action, action_input, final_answer


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def _make_agent_node(llm: LLMClient):
    """Create the 'agent' node — calls LLM and parses the response."""

    async def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        iteration = state["iteration"]

        logger.info("langgraph_agent_think", iteration=iteration + 1)
        response = await llm.invoke(messages)
        raw_text = response.content

        thought, action, action_input, final_answer = _parse_llm_output(raw_text)

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
            }

        # No recognizable action or final answer — treat as final
        step = AgentStep(
            thought=thought,
            action="respond",
            action_input={},
            observation=raw_text,
        )
        return {
            "steps": state["steps"] + [step],
            "answer": raw_text,
            "done": True,
            "iteration": iteration + 1,
        }

    return agent_node


def _make_tools_node(registry: ToolRegistry):
    """Create the 'tools' node — executes the last action."""

    async def tools_node(state: AgentState) -> dict:
        steps = state["steps"]
        last_step = steps[-1]

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
    graph.add_node("agent", _make_agent_node(llm))
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
        }

        logger.info("langgraph_agent_start", query=query[:80])

        # Run the graph
        final_state = await self._graph.ainvoke(initial_state)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        answer = final_state.get("answer") or "I couldn't complete the task."
        steps = final_state.get("steps", [])

        logger.info(
            "langgraph_agent_done",
            steps=len(steps),
            latency_ms=round(elapsed_ms, 1),
        )

        return AgentResult(
            answer=answer,
            steps=steps,
            total_tokens=0,
            latency_ms=round(elapsed_ms, 1),
        )
