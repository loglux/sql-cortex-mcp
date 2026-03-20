"""Regression tests for tool registry."""

from app.mcp.registry import ToolDef, ToolRegistry


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolDef(
            name="test.echo",
            title="Echo",
            description="Returns input as-is.",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
            output_schema={"type": "object"},
        ),
        lambda payload: {"echo": payload.get("msg")},
    )
    return reg


def test_list_tools_returns_registered() -> None:
    reg = _make_registry()
    result = reg.list_tools()
    assert len(result["tools"]) == 1
    assert result["tools"][0]["name"] == "test.echo"


def test_call_known_tool() -> None:
    reg = _make_registry()
    result = reg.call("test.echo", {"msg": "hello"})
    assert result == {"echo": "hello"}


def test_call_unknown_tool_returns_error() -> None:
    reg = _make_registry()
    result = reg.call("nonexistent", {})
    assert "error" in result


def test_tools_list_sorted() -> None:
    reg = ToolRegistry()
    for name in ["z.tool", "a.tool", "m.tool"]:
        reg.register(
            ToolDef(name=name, title=name, description="", input_schema={}, output_schema={}),
            lambda p: {},
        )
    names = [t["name"] for t in reg.list_tools()["tools"]]
    assert names == sorted(names)
