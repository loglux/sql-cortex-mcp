"""Regression tests for schema introspector."""

import pytest
from app.sql.schema import SchemaIntrospector
from sqlalchemy import create_engine, text

DB_URL = "sqlite:///:memory:"


@pytest.fixture
def introspector() -> SchemaIntrospector:
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL)")
        )
        conn.execute(
            text("CREATE TABLE orders (id INTEGER PRIMARY KEY, product_id INTEGER, qty INTEGER)")
        )
    intr = SchemaIntrospector.__new__(SchemaIntrospector)
    intr.engine = engine
    return intr


def test_get_schema_returns_all_tables(introspector: SchemaIntrospector) -> None:
    schema = introspector.get_schema()
    assert "products" in schema
    assert "orders" in schema


def test_get_schema_returns_columns(introspector: SchemaIntrospector) -> None:
    schema = introspector.get_schema(table="products")
    cols = {c["name"] for c in schema["products"]["columns"]}
    assert cols == {"id", "name", "price"}


def test_get_schema_filter_by_table(introspector: SchemaIntrospector) -> None:
    schema = introspector.get_schema(table="products")
    assert "products" in schema
    assert "orders" not in schema


def test_get_schema_nonexistent_table_returns_empty(introspector: SchemaIntrospector) -> None:
    schema = introspector.get_schema(table="nonexistent")
    assert schema == {}


def test_get_schema_simple_structure(introspector: SchemaIntrospector) -> None:
    result = introspector.get_schema_simple()
    assert "tables" in result
    assert "products" in result["tables"]
    assert "columns" in result["tables"]["products"]
    assert "indexes" in result["tables"]["products"]


def test_get_schema_simple_column_types(introspector: SchemaIntrospector) -> None:
    result = introspector.get_schema_simple()
    cols = result["tables"]["products"]["columns"]
    assert "id" in cols
    assert "type" in cols["id"]
    assert "nullable" in cols["id"]
