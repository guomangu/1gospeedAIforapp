"""JSON Schemas and GBNF grammars for the 6 stateless micro-agents."""

import json
import re

_COMMON_GBNF = (
    'ws ::= ([ \\t\\n] ws)?\n'
    'string ::= "\\"" ([^"\\\\] | "\\\\" (["\\\\/bfnrt] | "u" [0-9a-fA-F]{4}))* "\\""\n'
)
_STR_ARR = {"type": "array", "items": {"type": "string"}}


def _obj(req: list[str], props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": req, "properties": props}


SCHEMAS: dict[str, dict] = {
    "0_ARCHITECT": _obj(["files"], {"files": {"type": "array", "maxItems": 6, "items": _obj(
        ["path", "purpose", "depends_on", "req_ids", "is_entry"],
        {"path": {"type": "string"}, "purpose": {"type": "string"}, "depends_on": _STR_ARR, "req_ids": _STR_ARR, "is_entry": {"type": "boolean"}},
    )}}),
    "1_DRAFT": _obj(["filename", "code", "exports_summary"], {
        "filename": {"type": "string"}, "code": {"type": "string"}, "exports_summary": {"type": "string"},
    }),
    "2_EXTRACT": _obj(["purpose_summary", "side_effects"], {
        "purpose_summary": {"type": "string"}, "side_effects": {"type": "string"},
    }),
    "3_CRITIQUE": _obj(["converged", "orders"], {
        "converged": {"type": "boolean"},
        "orders": {"type": "array", "maxItems": 6, "items": _obj(
            ["target_path", "action", "reason"],
            {"target_path": {"type": "string"}, "action": {"enum": ["FIX_INTERFACE", "SPLIT_FILE", "ADD_MISSING", "REMOVE_DUPLICATE"]}, "reason": {"type": "string"}},
        )},
    }),
    "4_REBALANCE": _obj(["actions_taken", "files"], {
        "actions_taken": {"type": "string"},
        "files": {"type": "array", "maxItems": 2, "items": _obj(["path", "code"], {"path": {"type": "string"}, "code": {"type": "string"}})},
    }),
    "5_SYNTHESIS": _obj(["optimized_code", "test_path", "test_code"], {
        "optimized_code": {"type": "string"}, "test_path": {"type": "string"}, "test_code": {"type": "string"},
    }),
}

GBNF_GRAMMARS: dict[str, str] = {
    "1_DRAFT": 'root ::= "{" ws "\\"filename\\":" ws string "," ws "\\"code\\":" ws string "," ws "\\"exports_summary\\":" ws string ws "}"\n' + _COMMON_GBNF,
    "2_EXTRACT": 'root ::= "{" ws "\\"purpose_summary\\":" ws string "," ws "\\"side_effects\\":" ws string ws "}"\n' + _COMMON_GBNF,
    "5_SYNTHESIS": 'root ::= "{" ws "\\"optimized_code\\":" ws string "," ws "\\"test_path\\":" ws string "," ws "\\"test_code\\":" ws string ws "}"\n' + _COMMON_GBNF,
}


def schema_for(role: str, code_exts: tuple[str, ...] = (), min_items: int = 1) -> dict:
    """Return role JSON schema, constraining 0_ARCHITECT file count and stack code_exts."""
    schema = json.loads(json.dumps(SCHEMAS[role]))
    if role == "0_ARCHITECT":
        if min_items > 1:
            schema["properties"]["files"]["minItems"] = min_items
        alts = "|".join(re.escape(e.lstrip(".")) for e in code_exts if e.strip("."))
        if alts:
            pat = rf"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*(/[a-zA-Z0-9_][a-zA-Z0-9_.-]*)*\.({alts})$"
            schema["properties"]["files"]["items"]["properties"]["path"]["pattern"] = pat
    return schema
