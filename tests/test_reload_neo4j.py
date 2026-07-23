from __future__ import annotations

import unittest

from src.reload_neo4j import split_cypher_statements


class ReloadNeo4jTests(unittest.TestCase):
    def test_split_cypher_ignores_comments_and_empty_parts(self) -> None:
        text = """
        // package comment
        CREATE CONSTRAINT x IF NOT EXISTS FOR (n:X) REQUIRE n.id IS UNIQUE;

        // import comment
        LOAD CSV WITH HEADERS FROM 'file:///x.csv' AS row
        MERGE (:X {id: row.id});
        """

        statements = split_cypher_statements(text)

        self.assertEqual(len(statements), 2)
        self.assertTrue(statements[0].startswith("CREATE CONSTRAINT"))
        self.assertTrue(statements[1].startswith("LOAD CSV"))


if __name__ == "__main__":
    unittest.main()
