from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentDefinition:
    name: str
    role: str
    expected_output_format: str = "json_block"
    can_complete: bool = False
    system_prompt: str = ""
    source_path: Path | None = None


class AgentLoader:
    def __init__(self, agents_dir: str = "./agents"):
        self._agents_dir = Path(agents_dir)
        self.warnings: list[str] = []

    def list_agents(self) -> list[str]:
        return sorted(self._scan())

    def list_agents_by_group(self) -> list[tuple[str | None, list[str]]]:
        """Agents grouped by their top-level subdirectory under ``agents_dir``.

        Files directly inside ``agents_dir`` (no subdirectory) map to group
        ``None`` and come first. Groups (and agents within them) are sorted
        alphabetically. Deeper nesting is flattened into the top-level group.
        """
        index = self._scan()
        grouped: dict[str | None, list[str]] = {}

        for name, path in index.items():
            if path.parent == self._agents_dir:
                group: str | None = None
            else:
                group = path.parent.relative_to(self._agents_dir).parts[0]
            grouped.setdefault(group, []).append(name)

        result: list[tuple[str | None, list[str]]] = []
        root = sorted(grouped.pop(None, []))
        if root:
            result.append((None, root))
        result.extend(
            (group, sorted(names)) for group, names in sorted(grouped.items())
        )
        return result

    def get_agent(self, name: str) -> AgentDefinition:
        index = self._scan()
        path = index.get(name)

        if path is None:
            available = ", ".join(sorted(index)) or "none"
            raise FileNotFoundError(
                f"Agent '{name}' not found under {self._agents_dir} "
                f"(available: {available})"
            )

        return self._load_file(path)

    def load_all(self) -> list[AgentDefinition]:
        index = self._scan()
        return [self._load_file(path) for path in sorted(index.values())]

    def _scan(self) -> dict[str, Path]:
        """Recursively index all agent ``.md`` files by frontmatter ``name``.

        Files with missing/malformed frontmatter or no ``name`` field are
        skipped and reported via :attr:`warnings`. Two files resolving to
        the same name raise ``ValueError`` with both paths.
        """
        index: dict[str, Path] = {}
        self.warnings = []

        if not self._agents_dir.exists():
            return index

        for path in sorted(self._agents_dir.rglob("*.md")):
            try:
                name = AgentLoader._read_name(path)
            except ValueError as exc:
                self.warnings.append(f"Skipped {path}: {exc}")
                continue

            if name in index:
                raise ValueError(
                    f"Duplicate agent name '{name}': {index[name]}, {path}"
                )

            index[name] = path

        return index

    @staticmethod
    def _read_name(path: Path) -> str:
        content = path.read_text(encoding="utf-8")
        frontmatter, _ = AgentLoader._parse_frontmatter(content)

        name = str(frontmatter.get("name", "")).strip()
        if not name:
            raise ValueError(
                "Missing required field 'name' in YAML frontmatter"
            )

        return name

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
            source_path=path,
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