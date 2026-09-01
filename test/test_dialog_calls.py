import ast
import pathlib
import unittest


SOURCE_PATH = pathlib.Path(__file__).resolve().parents[1] / "CialloHEVC.py"


class DialogCallTests(unittest.TestCase):
    def test_show_dialog_calls_provide_title_and_message(self):
        tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
        invalid_lines = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "show_dialog":
                continue
            if len(node.args) < 2:
                invalid_lines.append(node.lineno)

        self.assertEqual(invalid_lines, [], f"show_dialog calls missing message: {invalid_lines}")


if __name__ == "__main__":
    unittest.main()
