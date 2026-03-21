from typing import Any, Dict

from app.config import Config
from app.sql.schema import SchemaIntrospector


class ResourceRegistry:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.introspector = SchemaIntrospector(config.db_url)

    def list_resources(self) -> Dict[str, Any]:
        resources = [
            {
                "uri": "resource://schema",
                "name": "Database Schema",
                "description": "Tables, columns, and indexes for the current database.",
                "mimeType": "application/json",
            },
            {
                "uri": "resource://config",
                "name": "Server Config",
                "description": "Non-secret runtime configuration summary.",
                "mimeType": "application/json",
            },
        ]
        return {"resources": resources}

    def read_resource(self, uri: str) -> Dict[str, Any]:
        if uri == "resource://schema":
            schema = self.introspector.get_schema()
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": _json_text(schema),
                    }
                ]
            }
        if uri == "resource://config":
            config_summary = {
                "mode": self.config.mode,
                "limit_default": self.config.limit_default,
                "timeout_ms": self.config.timeout_ms,
                "enable_ui": self.config.enable_ui,
                "enable_explanations": self.config.enable_explanations,
                "allow_destructive": self.config.allow_destructive,
            }
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": _json_text(config_summary),
                    }
                ]
            }
        return None


def _json_text(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)
