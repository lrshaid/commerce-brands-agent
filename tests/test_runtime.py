import unittest

from agent.main import TOOLS, build_system_prompt, dispatch


class RuntimeTests(unittest.TestCase):
    def test_blueprint_tool_count(self):
        self.assertEqual(len(TOOLS), 14)

    def test_dispatches_semantic_tool(self):
        result = dispatch({"tool": "shopify_entity_model", "arguments": {}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["entity_count"], 34)

    def test_unknown_tool_returns_error(self):
        result = dispatch({"tool": "write_to_shopify", "arguments": {}})
        self.assertFalse(result["ok"])

    def test_system_prompt_loads_all_knowledge_docs(self):
        prompt = build_system_prompt()
        self.assertEqual(prompt.count("(mtime "), 12)
        self.assertIn("Never fabricate a third-party metric", prompt)


if __name__ == "__main__":
    unittest.main()

