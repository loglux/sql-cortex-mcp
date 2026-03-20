import unittest

from app.sql.policy import is_allowed


class SqlPolicyTests(unittest.TestCase):
    def test_allows_basic_select_in_read_only(self) -> None:
        self.assertTrue(is_allowed("SELECT id FROM users", mode="read-only"))

    def test_blocks_multiple_statements_in_read_only(self) -> None:
        self.assertFalse(is_allowed("SELECT 1; SELECT 2;", mode="read-only"))

    def test_blocks_cte_with_insert_in_read_only(self) -> None:
        sql = """
        WITH moved AS (
            INSERT INTO archive_users (id)
            SELECT id FROM users
            RETURNING id
        )
        SELECT * FROM moved
        """
        self.assertFalse(is_allowed(sql, mode="read-only"))

    def test_blocks_select_into_in_read_only(self) -> None:
        self.assertFalse(is_allowed("SELECT * INTO backup_users FROM users", mode="read-only"))

    def test_blocks_for_update_in_read_only(self) -> None:
        self.assertFalse(is_allowed("SELECT * FROM users FOR UPDATE", mode="read-only"))

    def test_allows_explain_select_in_read_only(self) -> None:
        self.assertTrue(is_allowed("EXPLAIN SELECT * FROM users", mode="read-only"))

    def test_blocks_explain_insert_in_read_only(self) -> None:
        self.assertFalse(is_allowed("EXPLAIN INSERT INTO users(id) VALUES (1)", mode="read-only"))

    def test_ignores_keywords_inside_literals(self) -> None:
        self.assertTrue(
            is_allowed("SELECT '-- drop table users' AS note FROM users", mode="read-only")
        )

    def test_execute_mode_allows_single_write_statement(self) -> None:
        self.assertTrue(is_allowed("DELETE FROM users WHERE id = 1", mode="execute"))

    def test_execute_mode_still_blocks_multiple_statements(self) -> None:
        self.assertFalse(
            is_allowed("DELETE FROM users; DELETE FROM users_archive;", mode="execute")
        )


if __name__ == "__main__":
    unittest.main()
