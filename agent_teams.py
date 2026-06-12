"""
Agent Teams (A2A) — Multi-agent coordination for INXOTIVE HUB.
Lead agent coordinates specialists, agents share context, results synthesized.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("agent-teams")

ROUTER_URL = "http://localhost:20128/v1/chat/completions"
API_KEY = os.environ.get("NINE_ROUTER_API_KEY", "")

HEADERS = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}

# Agent prompts (short versions for team context)
AGENT_DESCRIPTIONS = {
    "researchx": "Research analyst — deep analysis with data",
    "opencode": "System architect — code & infrastructure",
    "tradex": "Crypto analyst — market & technical analysis",
    "webdev": "Web developer — React, FastAPI, Vercel",
    "bizmind": "Business strategist — healthcare digital agency",
    "dr_pharma": "Clinical pharmacist — drug therapy & guidelines",
    "flowbot": "Productivity coach — systems & workflows",
    "claudecode": "Business strategist — agency operations",
    "securityx": "Security auditor — OWASP, secrets, vulnerabilities",
    "architectx": "Software architect — system design & scalability",
    "codereview": "Code reviewer — quality, bugs, standards",
    "datax": "Data analyst — stats, pandas, visualization",
    "devopsx": "DevOps specialist — Docker, systemd, monitoring",
}


async def _call_agent(agent_key: str, task: str, context: str = "", model: str = "max-free") -> str:
    """Call a single agent via 9Router with context."""
    system = AGENT_DESCRIPTIONS.get(agent_key, f"Kamu {agent_key}, asisten INXOTIVE.")
    system += f"\n\nKonteks:\n{context[:2000]}" if context else ""
    system += "\nBeri analisis mendalam dalam Bahasa Indonesia."

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(ROUTER_URL, headers=HEADERS, json={
                "model": model, "messages": messages,
                "max_tokens": 2048, "temperature": 0.5,
            })
            if r.status_code == 200:
                body = r.text
                depth = 0
                json_end = 0
                for i, c in enumerate(body):
                    if c == '{': depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0: json_end = i + 1; break
                data = json.loads(body[:json_end] if json_end else body)
                msg = data.get("choices", [{}])[0].get("message", {})
                return (msg.get("content", "") or msg.get("reasoning_content", "") or "(empty)")
            return f"(HTTP {r.status_code})"
    except Exception as e:
        return f"(Error: {e})"


class AgentTeam:
    """A team of agents working together on a task."""

    def __init__(self, name: str, agents: List[str], lead_agent: str = "researchx", model: str = "max-free"):
        self.name = name
        self.agents = agents
        self.lead_agent = lead_agent
        self.model = model
        self.results: Dict[str, str] = {}
        self.shared_context: List[str] = []

    async def run_task(self, task: str, mode: str = "sequential") -> Dict:
        """Run a task with the team. Returns structured results."""
        if mode == "sequential":
            return await self._run_sequential(task)
        elif mode == "debate":
            return await self._run_debate(task)
        elif mode == "hierarchical":
            return await self._run_hierarchical(task)
        else:
            return {"success": False, "error": f"Unknown mode: {mode}"}

    async def _run_sequential(self, task: str) -> Dict:
        """Chain of thought — each agent builds on previous output."""
        context = task
        self.shared_context = [f"Task: {task}"]

        for agent in self.agents:
            prompt = f"Berdasarkan konteks sebelumnya, berikan analisis dari perspektif {agent}."
            result = await _call_agent(agent, prompt, context, self.model)
            self.results[agent] = result
            self.shared_context.append(f"\n[{agent}]:\n{result[:1000]}")
            context = "\n".join(self.shared_context[-2:])

        # Lead synthesis
        synthesis_prompt = f"Task: {task}\n\nTeam analysis:\n{chr(10).join(f'- {a}: {r[:300]}' for a, r in self.results.items())}\n\nSynthesize all perspectives into a final recommendation."
        synthesis = await _call_agent(self.lead_agent, synthesis_prompt, "", self.model)

        return {
            "success": True,
            "mode": "sequential",
            "team": self.name,
            "individual_results": self.results,
            "synthesis": synthesis,
        }

    async def _run_debate(self, task: str) -> Dict:
        """Independent perspectives, then lead synthesizes."""
        async def _get_perspective(agent: str):
            result = await _call_agent(agent, f"Analisis masalah ini: {task}", "", self.model)
            self.results[agent] = result
            self.shared_context.append(f"[{agent}]:\n{result[:500]}")
            return result

        await asyncio.gather(*[_get_perspective(a) for a in self.agents], return_exceptions=True)

        debate = "\n\n".join(self.shared_context)
        synthesis = await _call_agent(
            self.lead_agent,
            f"Task: {task}\n\nBerikut pendapat dari berbagai spesialis:\n{debate}\n\nBeri sintesis akhir yang komprehensif.",
            "", self.model,
        )

        return {"success": True, "mode": "debate", "team": self.name, "individual_results": self.results, "synthesis": synthesis}

    async def _run_hierarchical(self, task: str) -> Dict:
        """Lead breaks down task, assigns to specialists, synthesizes."""
        breakdown = await _call_agent(
            self.lead_agent,
            f"Task: {task}\n\nBreak down this task into {len(self.agents)} subtasks, one for each specialist. Format: each line 'AGENT: subtask'",
            "", self.model,
        )

        sub_tasks = {}
        for line in breakdown.split("\n"):
            for agent in self.agents:
                if agent in line.lower():
                    sub_tasks[agent] = line
                    break

        async def _execute_subtask(agent: str):
            prompt = sub_tasks.get(agent, f"Analisis dari perspektif {agent}")
            result = await _call_agent(agent, prompt, f"Task: {task}", self.model)
            self.results[agent] = result
            self.shared_context.append(f"[{agent}]:\n{result[:500]}")

        await asyncio.gather(*[_execute_subtask(a) for a in self.agents], return_exceptions=True)

        combined = "\n\n".join(self.shared_context)
        synthesis = await _call_agent(self.lead_agent, f"Synthesize these findings:\n{combined}\n\nFinal recommendation?", "", self.model)

        return {"success": True, "mode": "hierarchical", "team": self.name, "individual_results": self.results, "synthesis": synthesis}


class AgentTeamManager:
    """Manages multiple agent teams."""

    def __init__(self):
        self.teams: Dict[str, AgentTeam] = {}

    def create_team(self, name: str, agents: List[str], lead_agent: str = "researchx", model: str = "max-free") -> AgentTeam:
        team = AgentTeam(name, agents, lead_agent, model)
        self.teams[name] = team
        return team

    async def run(self, team_name: str, task: str, mode: str = "sequential") -> Dict:
        if team_name not in self.teams:
            return {"success": False, "error": f"Team '{team_name}' not found"}
        return await self.teams[team_name].run_task(task, mode)

    def list_teams(self) -> List[Dict]:
        return [{"name": n, "agents": t.agents, "lead": t.lead_agent} for n, t in self.teams.items()]


agent_team_manager = AgentTeamManager()

# Predefined teams
agent_team_manager.create_team("market", ["researchx", "tradex", "datax"], "tradex")
agent_team_manager.create_team("tech", ["opencode", "codereview", "architectx", "debugger"], "architectx")
agent_team_manager.create_team("business", ["claudecode", "bizmind", "compliance"], "claudecode")
agent_team_manager.create_team("security", ["securityx", "codereview", "opencode"], "securityx")
