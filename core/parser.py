import json
import re
from typing import Optional


class StateParser:
    XML_PATTERN = re.compile(
        r"<state_update>\s*(.*?)\s*</state_update>",
        re.DOTALL | re.IGNORECASE,
    )

    JSON_BLOCK_PATTERN = re.compile(
        r"```(?:json)?\s*(.*?)\s*```",
        re.DOTALL,
    )

    @classmethod
    def extract_state_update(cls, stdout: str) -> Optional[dict]:
        if not stdout:
            return None

        raw = cls._extract_xml(stdout)
        if raw is not None:
            return raw

        raw = cls._extract_json_block(stdout)
        if raw is not None:
            return raw

        return None

    @classmethod
    def _extract_xml(cls, text: str) -> Optional[dict]:
        matches = list(cls.XML_PATTERN.finditer(text))
        for match in reversed(matches):
            raw = match.group(1)
            parsed = cls._parse_json(raw)
            if parsed is not None:
                return parsed
            parsed = cls._parse_markdown_state(raw)
            if parsed is not None:
                return parsed
        return None

    @classmethod
    def _extract_json_block(cls, text: str) -> Optional[dict]:
        matches = list(cls.JSON_BLOCK_PATTERN.finditer(text))
        for match in reversed(matches):
            parsed = cls._parse_json(match.group(1))
            if parsed is not None:
                return parsed
        return None

    @classmethod
    def _parse_json(cls, raw: str) -> Optional[dict]:
        raw = raw.strip()
        if not raw:
            return None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None

        if not parsed:
            return None

        return parsed

    # ---- Markdown/YAML fallback parser ----

    _MD_KEY_RE = re.compile(
        r"^\s*-\s+\*\*(.+?)\*\*\s*:\s*(.*)"
    )
    _MD_ITEM_RE = re.compile(
        r"^\s{2,}-\s+(.*)"
    )
    _MD_SEPARATOR_RE = re.compile(
        r"\s*[─→➜>\-:]\s*"
    )

    @classmethod
    def _parse_markdown_state(cls, text: str) -> Optional[dict]:
        lines = text.strip().split("\n")
        if not lines:
            return None

        result = {}
        current_list_key = None
        current_list = None
        found_any = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            top_match = cls._MD_KEY_RE.match(stripped)
            if top_match:
                found_any = True
                if current_list_key is not None and current_list is not None:
                    result[current_list_key] = current_list
                    current_list_key = None
                    current_list = None

                key = top_match.group(1).strip().strip("*")
                value = top_match.group(2).strip()

                if value:
                    result[key] = cls._coerce_md_value(value)
                else:
                    current_list_key = key
                    current_list = []
                continue

            if current_list_key is not None:
                item_match = cls._MD_ITEM_RE.match(line)
                if item_match:
                    item_text = item_match.group(1).strip()
                    item_text = cls._MD_SEPARATOR_RE.split(item_text, maxsplit=1)[0].strip()
                    item_text = item_text.strip("`").strip()
                    if item_text:
                        current_list.append(item_text)
                    continue

        if current_list_key is not None and current_list is not None:
            result[current_list_key] = current_list

        if not found_any or not result:
            return None

        return result

    @classmethod
    def _coerce_md_value(cls, value: str) -> object:
        v = value.strip()
        if v.startswith("`") and v.endswith("`"):
            v = v[1:-1].strip()

        if v.lower() in ("true", "yes", "on"):
            return True
        if v.lower() in ("false", "no", "off"):
            return False
        if v.lower() in ("none", "null", "~"):
            return None

        try:
            return int(v)
        except (ValueError, TypeError):
            pass

        if "." in v:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass

        return v