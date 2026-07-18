import json
import re
from typing import Optional


class StateParser:
    XML_PATTERN = re.compile(
        r"<state_update>\s*(.*?)\s*</state_update>",
        re.DOTALL | re.IGNORECASE,
    )
    JSON_BLOCK_PATTERN = re.compile(
        r"```(?:json)?\s*\n(.*?)\n```",
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
        match = cls.XML_PATTERN.search(text)
        if not match:
            return None
        return cls._parse_json(match.group(1))

    @classmethod
    def _extract_json_block(cls, text: str) -> Optional[dict]:
        match = cls.JSON_BLOCK_PATTERN.search(text)
        if not match:
            return None
        return cls._parse_json(match.group(1))

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
        return parsed
