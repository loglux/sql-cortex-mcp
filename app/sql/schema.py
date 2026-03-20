from typing import Any, Dict

from sqlalchemy import create_engine, inspect


class SchemaIntrospector:
    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url)

    def get_schema(self, table: str | None = None) -> Dict[str, Any]:
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        if table:
            tables = [t for t in tables if t == table]

        result = {}
        for t in tables:
            columns = inspector.get_columns(t)
            indexes = inspector.get_indexes(t)
            foreign_keys = inspector.get_foreign_keys(t)
            result[t] = {
                "columns": [
                    {
                        "name": c["name"],
                        "type": str(c["type"]),
                        "nullable": c.get("nullable", True),
                        "default": c.get("default"),
                    }
                    for c in columns
                ],
                "indexes": indexes,
                "foreign_keys": [
                    {
                        "constrained_columns": fk.get("constrained_columns", []),
                        "referred_table": fk.get("referred_table", ""),
                        "referred_columns": fk.get("referred_columns", []),
                        "name": fk.get("name"),
                    }
                    for fk in foreign_keys
                ],
            }

        return result

    def get_schema_simple(self) -> Dict[str, Any]:
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        result: Dict[str, Any] = {"tables": {}}
        for t in tables:
            columns = inspector.get_columns(t)
            indexes = inspector.get_indexes(t)
            cols = {}
            for c in columns:
                cols[c["name"]] = {
                    "type": str(c["type"]).upper(),
                    "nullable": c.get("nullable", True),
                }
            result["tables"][t] = {
                "columns": cols,
                "indexes": [
                    {
                        "name": i.get("name"),
                        "columns": i.get("column_names", []),
                        "unique": i.get("unique", False),
                    }
                    for i in indexes
                ],
            }
        return result
