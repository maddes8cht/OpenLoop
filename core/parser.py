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
            parsed = cls._parse_json(match.group(1))
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