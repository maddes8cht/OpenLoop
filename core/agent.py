from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentDefinition:
    name: str
    role: str
    expected_output_format: str = "json_block"
    can_complete: bool = False
    system_prompt: str = ""


class AgentLoader:
    def __init__(self, agents_dir: str = "./agents"):
        self._agents_dir = Path(agents_dir)

    def list_agents(self) -> list[str]:
        if not self._agents_dir.exists():
            return []

        return sorted(
            f.stem for f in self._agents_dir.iterdir()
            if f.suffix == ".md"
        )

    def get_agent(self, name: str) -> AgentDefinition:
        path = self._agents_dir / f"{name}.md"

        if not path.exists():
            raise FileNotFoundError(
                f"Agent '{name}' not found at {path}"
            )

        return self._load_file(path)

    def load_all(self) -> list[AgentDefinition]:
        return [self.get_agent(name) for name in self.list_agents()]

    def _load_file(self, path: Path) -> AgentDefinition:
        content = path.read_text(encoding="utf-8")
        frontmatter, system_prompt = self._parse_frontmatter(content)

        name = frontmatter.get("name", path.stem)
        role = frontmatter.get("role", "")

        expected_output_format = frontmatter.get(
            "expected_output_format",
            "json_block",
        )

        can_complete_raw = frontmatter.get("can_complete", "false")
        can_complete = str(can_complete_raw).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        return AgentDefinition(
            name=name,
            role=role,
            expected_output_format=expected_output_format,
            can_complete=can_complete,
            system_prompt=system_prompt,
        )

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        lines = content.split("\n")

        if not lines or lines[0].strip() != "---":
            raise ValueError(
                "Missing YAML frontmatter: file must start with '---'"
            )

        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break

        if end_idx is None:
            raise ValueError(
                "Unclosed YAML frontmatter: missing closing '---'"
            )

        frontmatter = {}

        for line in lines[1:end_idx]:
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                continue

            if ":" in stripped:
                key, _, value = stripped.partition(":")
                frontmatter[key.strip()] = value.strip()

        system_prompt = "\n".join(lines[end_idx + 1 :]).strip()

        if "name" not in frontmatter:
            raise ValueError(
                "Missing required field 'name' in YAML frontmatter"
            )

        return frontmatter, system_prompt