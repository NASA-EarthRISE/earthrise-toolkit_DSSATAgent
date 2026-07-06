"""
Agent Skills Loader

Implements the Agent Skills open standard for loading prompt templates
and reference data from SKILL.md files.

Supports composable skills: subagent skills with `composable: true` in
frontmatter are collected and composed into the chat agent's template
skills at runtime (e.g., agent-specific capabilities merged into
chat-capabilities).
"""

import json
import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


@dataclass
class _SkillEntry:
    """Internal representation of a discovered skill."""
    name: str
    description: str
    path: str
    composable: bool = False
    compose_into: Optional[str] = None
    # List of tool names (from `tools:` frontmatter) that this skill bundles.
    # These tools are bound to the LLM only when the skill is active.
    tools: List[str] = field(default_factory=list)
    # Derived from the skill's path: the parent directory above `skills/` is
    # the owning Django app (e.g. 'foo_agent'). Empty for chat-level skills.
    owner_agent: str = ""
    _body_cache: Optional[str] = field(default=None, repr=False)
    _refs_cache: Dict[str, str] = field(default_factory=dict, repr=False)


class SkillLoader:
    """
    Discovers, indexes, and loads Agent Skills from directories.

    Composable skills (from subagents) are collected separately and
    their content is injected into the chat agent's template skills
    at runtime via get_composed_prompt().
    """

    def __init__(self, skills_dir: str):
        self._skills_dir = os.path.abspath(skills_dir)
        self._index: Dict[str, _SkillEntry] = {}
        self._composable: Dict[str, List[_SkillEntry]] = {}  # compose_into -> [entries]
        self._discover()

    def _discover(self):
        """Scan skills_dir for subdirectories containing SKILL.md files."""
        if not os.path.isdir(self._skills_dir):
            logger.warning("Skills directory not found: %s", self._skills_dir)
            return

        for entry in sorted(os.listdir(self._skills_dir)):
            skill_dir = os.path.join(self._skills_dir, entry)
            if not os.path.isdir(skill_dir):
                continue

            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md):
                skill_md = os.path.join(skill_dir, "skill.md")
                if not os.path.isfile(skill_md):
                    continue

            try:
                with open(skill_md, "r") as f:
                    content = f.read()

                meta, _ = self._parse_frontmatter(content)
                name = meta.get("name", entry)
                description = meta.get("description", "")
                composable = meta.get("composable", False)
                compose_into = meta.get("compose_into")
                raw_tools = meta.get("tools", []) or []
                if isinstance(raw_tools, str):
                    tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
                else:
                    tools = [str(t).strip() for t in raw_tools if str(t).strip()]

                skill_entry = _SkillEntry(
                    name=name,
                    description=description,
                    path=skill_dir,
                    composable=composable,
                    compose_into=compose_into,
                    tools=tools,
                    owner_agent=self._derive_owner_agent(skill_dir),
                )

                if composable and compose_into:
                    # Composable skills are collected separately
                    self._composable.setdefault(compose_into, []).append(skill_entry)
                    logger.info("Discovered composable skill: %s (compose_into=%s)", name, compose_into)
                else:
                    self._index[name] = skill_entry
                    logger.info("Discovered skill: %s", name)
            except Exception as e:
                logger.error("Error loading skill from %s: %s", skill_dir, e)

    def list_skills(self) -> List[Dict[str, str]]:
        """Return metadata for all non-composable skills."""
        return [
            {"name": s.name, "description": s.description}
            for s in self._index.values()
        ]

    def list_loadable_skills(self) -> List[Dict[str, Any]]:
        """Return skills that expose a `tools:` bundle (loadable via load_skill).

        Each entry: {name, description, owner_agent, tools: [names...]}.
        Composable skills (compose_into) are excluded — they are prompt
        fragments, not loadable task skills.
        """
        out: List[Dict[str, Any]] = []
        for entry in self._index.values():
            if not entry.tools:
                continue
            out.append({
                "name": entry.name,
                "description": entry.description,
                "owner_agent": entry.owner_agent,
                "tools": list(entry.tools),
            })
        return out

    def get_entry(self, skill_name: str) -> Optional["_SkillEntry"]:
        """Return the internal skill entry (or None). Caller should treat as
        read-only metadata."""
        return self._index.get(skill_name)

    def get_skill_body(self, skill_name: str) -> str:
        """Return the full markdown body for a loadable skill (no section extract)."""
        entry = self._index.get(skill_name)
        if entry is None:
            raise KeyError(f"Skill not found: {skill_name}")
        if entry._body_cache is None:
            skill_md = os.path.join(entry.path, "SKILL.md")
            if not os.path.isfile(skill_md):
                skill_md = os.path.join(entry.path, "skill.md")
            with open(skill_md, "r") as f:
                content = f.read()
            _, body = self._parse_frontmatter(content)
            entry._body_cache = body
        return (entry._body_cache or "").strip()

    @staticmethod
    def _derive_owner_agent(skill_dir: str) -> str:
        """Infer the owning Django app from a skill directory path.

        Convention: skills live at `{app}/skills/{skill_name}/SKILL.md`. The
        owner is the basename two levels up. Chat-level skills (in
        `chat/skills/...`) return 'chat'.
        """
        # skill_dir ends with /skills/{skill_name}
        skills_dir = os.path.dirname(skill_dir)
        app_dir = os.path.dirname(skills_dir)
        owner = os.path.basename(app_dir)
        return owner or ""

    def get_prompt(self, skill_name: str, section: Optional[str] = None) -> str:
        """Load a prompt template from a skill's SKILL.md body."""
        entry = self._index.get(skill_name)
        if entry is None:
            raise KeyError(f"Skill not found: {skill_name}")

        if entry._body_cache is None:
            skill_md = os.path.join(entry.path, "SKILL.md")
            if not os.path.isfile(skill_md):
                skill_md = os.path.join(entry.path, "skill.md")
            with open(skill_md, "r") as f:
                content = f.read()
            _, body = self._parse_frontmatter(content)
            entry._body_cache = body

        if section is None:
            return entry._body_cache.strip()

        extracted = self._extract_section(entry._body_cache, section)
        if not extracted:
            raise KeyError(f"Section '{section}' not found in skill '{skill_name}'")
        return extracted

    def get_composed_content(self, compose_target: str, section: Optional[str] = None) -> str:
        """
        Collect and merge content from all composable skills targeting a category.

        Args:
            compose_target: 'capabilities', 'help', or 'results'
            section: Optional section heading to extract from each skill.

        Returns:
            Merged content from all composable skills for this target.
        """
        entries = self._composable.get(compose_target, [])
        logger.info("[SkillLoader] get_composed_content('%s', section='%s') — %d entries",
                    compose_target, section, len(entries))
        parts = []

        for entry in entries:
            if entry._body_cache is None:
                skill_md = os.path.join(entry.path, "SKILL.md")
                if not os.path.isfile(skill_md):
                    skill_md = os.path.join(entry.path, "skill.md")
                try:
                    with open(skill_md, "r") as f:
                        content = f.read()
                    _, body = self._parse_frontmatter(content)
                    entry._body_cache = body
                    logger.info("[SkillLoader] Loaded body for %s: %d chars, starts with: %r",
                                entry.name, len(body), body[:80])
                except Exception as e:
                    logger.warning("Could not load composable skill %s: %s", entry.name, e)
                    continue

            if section:
                text = self._extract_section(entry._body_cache, section)
                if not text:
                    logger.warning("[SkillLoader] Section '%s' not found in %s. Headings in body: %s",
                                   section, entry.name,
                                   re.findall(r'^## .+', entry._body_cache, re.MULTILINE))
            else:
                text = entry._body_cache.strip()

            if text:
                parts.append(text)

        return "\n\n".join(parts)

    def get_composed_content_for_agents(
        self,
        compose_target: str,
        agents: Optional[List[str]] = None,
        section: Optional[str] = None,
    ) -> str:
        """Like `get_composed_content`, but restrict to skills owned by the
        given agents. Pass `agents=None` to include everything (matches
        `get_composed_content`).

        Matching accepts both the skill's `owner_agent` (derived from
        directory, e.g. "foo_agent") and its Django agent_label
        (e.g. "dssat_agent") via a soft lookup. If the caller passes a
        label that doesn't match directly, the skill passes.
        """
        entries = self._composable.get(compose_target, [])
        if agents:
            wanted = set(agents)

            def _keep(entry: "_SkillEntry") -> bool:
                owner = entry.owner_agent or ""
                if owner in wanted:
                    return True
                # Soft fallback: match on short label prefix
                return any(owner.startswith(a) or a.startswith(owner) for a in wanted if a)

            entries = [e for e in entries if _keep(e)]

        logger.info(
            "[SkillLoader] get_composed_content_for_agents('%s', agents=%s, "
            "section='%s') — %d entries",
            compose_target, list(agents) if agents else None, section, len(entries),
        )
        parts = []
        for entry in entries:
            if entry._body_cache is None:
                skill_md = os.path.join(entry.path, "SKILL.md")
                if not os.path.isfile(skill_md):
                    skill_md = os.path.join(entry.path, "skill.md")
                try:
                    with open(skill_md, "r") as f:
                        content = f.read()
                    _, body = self._parse_frontmatter(content)
                    entry._body_cache = body
                except Exception as e:
                    logger.warning("Could not load composable skill %s: %s", entry.name, e)
                    continue

            if section:
                text = self._extract_section(entry._body_cache, section)
            else:
                text = entry._body_cache.strip()

            if text:
                parts.append(text)

        return "\n\n".join(parts)

    def get_reference(self, skill_name: str, filename: str) -> str:
        """Load a reference file from a skill's references/ directory."""
        entry = self._index.get(skill_name)
        if entry is None:
            raise KeyError(f"Skill not found: {skill_name}")

        if filename in entry._refs_cache:
            return entry._refs_cache[filename]

        ref_path = os.path.join(entry.path, "references", filename)
        if not os.path.isfile(ref_path):
            raise FileNotFoundError(f"Reference file not found: {ref_path}")

        with open(ref_path, "r") as f:
            content = f.read()

        entry._refs_cache[filename] = content
        return content

    def get_reference_json(self, skill_name: str, filename: str) -> Any:
        """Load a reference file and parse as JSON."""
        return json.loads(self.get_reference(skill_name, filename))

    @staticmethod
    def _parse_frontmatter(content: str) -> Tuple[Dict, str]:
        """Split SKILL.md into YAML frontmatter and markdown body."""
        content = content.strip()
        if not content.startswith("---"):
            return {}, content

        second_fence = content.find("---", 3)
        if second_fence == -1:
            return {}, content

        yaml_block = content[3:second_fence].strip()
        body = content[second_fence + 3:]

        try:
            meta = yaml.safe_load(yaml_block) or {}
        except yaml.YAMLError as e:
            logger.error("Error parsing YAML frontmatter: %s", e)
            meta = {}

        return meta, body

    @staticmethod
    def _extract_section(body: str, heading: str) -> str:
        """Extract content under a ## heading until the next ## or end."""
        pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, body, re.DOTALL | re.MULTILINE)
        if match:
            return match.group(1).strip()
        return ""
