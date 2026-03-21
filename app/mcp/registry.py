from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class ToolAnnotations:
    read_only_hint: bool = False
    destructive_hint: bool = False
    idempotent_hint: bool = False
    open_world_hint: bool = (
        False  # False = tool only accesses the connected DB, no external systems
    )


@dataclass
class ToolDef:
    name: str
    title: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    annotations: ToolAnnotations = field(default_factory=ToolAnnotations)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
        self._defs: Dict[str, ToolDef] = {}

    def register(
        self, tool_def: ToolDef, handler: Callable[[Dict[str, Any]], Dict[str, Any]]
    ) -> None:
        self._defs[tool_def.name] = tool_def
        self._tools[tool_def.name] = handler

    def list_tools(self) -> Dict[str, Any]:
        tools: List[Dict[str, Any]] = []
        for tool in self._defs.values():
            a = tool.annotations
            entry: Dict[str, Any] = {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": {
                    "readOnlyHint": a.read_only_hint,
                    "destructiveHint": a.destructive_hint,
                    "idempotentHint": a.idempotent_hint,
                    "openWorldHint": a.open_world_hint,
                },
            }
            if tool.output_schema:
                entry["outputSchema"] = tool.output_schema
            tools.append(entry)
        tools.sort(key=lambda t: t["name"])
        return {"tools": tools}

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._tools[name](payload)
