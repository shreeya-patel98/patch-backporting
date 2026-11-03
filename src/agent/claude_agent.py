"""Claude replacement for LangChain's AgentExecutor.

PortGPT's original loop (LangChain 0.2 + ChatOpenAI) can't drive current
Claude models correctly: the old connector predates thinking blocks and sends
sampling params the API now rejects. This keeps PortGPT's LangChain @tool
definitions as-is and runs them in a manual loop on the official Anthropic SDK.

A manual loop (instead of the SDK tool runner) is used so we can stop as soon
as PortGPT's validate tool marks the hunk/patch as succeeded.
"""

import json
import os

import anthropic
from langchain_core.utils.function_calling import convert_to_openai_tool

from tools.logger import logger

DEFAULT_MODEL = "claude-opus-5-5"
# $/1M tokens: input, output, cache read, cache write (5-min)
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
          "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5)}
# Tool results (file views, git log/show) are the main input-token driver.
MAX_TOOL_CHARS = 12000


def _to_claude_tool(lc_tool):
    fn = convert_to_openai_tool(lc_tool)["function"]
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
    }


class ClaudeAgentExecutor:
    """Minimal AgentExecutor look-alike: .invoke(inputs, config) runs one task."""

    # Token totals across all executors in this process, for cost reporting.
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    budget_exhausted = False

    @classmethod
    def cost(cls, model):
        pi, po, pr, pw = PRICES.get(model, PRICES[DEFAULT_MODEL])
        u = cls.usage
        return (u["input"] * pi + u["output"] * po
                + u["cache_read"] * pr + u["cache_write"] * pw) / 1e6

    def __init__(self, system_tmpl, user_tmpl, tools, data, max_iterations, stop_when=None):
        self.system_tmpl = system_tmpl
        self.user_tmpl = user_tmpl
        self.tools = {t.name: t for t in tools}
        self.tool_defs = [_to_claude_tool(t) for t in tools]
        self.max_iterations = max_iterations
        self.max_cost = float(getattr(data, "max_cost", None) or 3.0)
        self.stop_when = stop_when or (lambda: False)
        self.model = getattr(data, "anthropic_model", None) or DEFAULT_MODEL
        self.effort = getattr(data, "effort", None) or "medium"
        self.use_fallback = getattr(data, "anthropic_fallback", True)
        self.client = anthropic.Anthropic()  # credentials from ANTHROPIC_API_KEY etc.

    def _log(self, logfile, text):
        if logfile:
            with open(logfile, "a") as f:
                f.write(text + "\n")

    def _create(self, system, messages):
        kwargs = dict(
            model=self.model,
            max_tokens=32000,
            system=system,
            tools=self.tool_defs,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            # Cache the stable prefix (tools + system + earlier turns).
            cache_control={"type": "ephemeral"},
        )
        if self.use_fallback:
            # Kernel CVE text can trip safety classifiers; let the API reroute
            # a refused request instead of silently stopping.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        with self.client.beta.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    def invoke(self, inputs, config=None):
        logfile = (config or {}).get("logfile")
        system = self.system_tmpl.format(**inputs)
        messages = [{"role": "user", "content": self.user_tmpl.format(**inputs)}]

        if ClaudeAgentExecutor.budget_exhausted:
            return {"output": None}
        for step in range(self.max_iterations):
            resp = self._create(system, messages)
            u = resp.usage
            ClaudeAgentExecutor.usage["input"] += u.input_tokens or 0
            ClaudeAgentExecutor.usage["output"] += u.output_tokens or 0
            ClaudeAgentExecutor.usage["cache_read"] += u.cache_read_input_tokens or 0
            ClaudeAgentExecutor.usage["cache_write"] += u.cache_creation_input_tokens or 0
            spent = ClaudeAgentExecutor.cost(self.model)
            self._log(logfile, f"[step {step}] spent so far: ${spent:.2f} / ${self.max_cost:.2f}")
            if spent >= self.max_cost:
                ClaudeAgentExecutor.budget_exhausted = True
                logger.error(f"Cost cap reached (${spent:.2f} >= ${self.max_cost:.2f}); stopping.")
                return {"output": None}

            # Append content unchanged so thinking blocks are passed back intact.
            messages.append({"role": "assistant", "content": resp.content})
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    self._log(logfile, f"[step {step}] assistant: {b.text}")

            if resp.stop_reason == "refusal":
                logger.error(f"Claude declined the request: {resp.stop_details}")
                return {"output": None}
            if resp.stop_reason == "pause_turn":
                continue
            if resp.stop_reason != "tool_use":
                return {"output": "".join(b.text for b in resp.content if b.type == "text")}

            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                self._log(logfile, f"[step {step}] tool {b.name}({json.dumps(b.input)})")
                try:
                    out = str(self.tools[b.name].invoke(b.input))
                    if len(out) > MAX_TOOL_CHARS:
                        out = (out[:MAX_TOOL_CHARS] + f"\n[... truncated {len(out) - MAX_TOOL_CHARS}"
                               " chars; request a narrower range ...]")
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
                except Exception as e:  # report tool failures back to the model
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": f"Tool error: {e}", "is_error": True})
                self._log(logfile, f"[step {step}] result: {results[-1]['content'][:4000]}")
            messages.append({"role": "user", "content": results})

            if self.stop_when():
                return {"output": "validated"}

        logger.error(f"Reached max_iterations ({self.max_iterations})")
        return {"output": None}
