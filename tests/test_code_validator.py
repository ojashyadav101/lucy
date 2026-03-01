"""Tests for the code_validator module.

Tests cover:
- Syntax error detection
- Scope analysis (undefined variables)
- Import availability checking
- Auto-fix for missing imports
- Error analysis for runtime failures
- Edge cases (star imports, comprehensions, walrus operator, etc.)

NOTE: Imports the validator module directly (not via lucy.tools) to avoid
pulling in the full Lucy dependency chain during isolated testing.
"""

import sys
import os

# Add the src directory to the path so we can import code_validator directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Import the module directly, not via the package __init__ (avoids dependency chain)
import importlib.util
# Mock structlog since it's the only non-stdlib dep in code_validator
import types
mock_structlog = types.ModuleType("structlog")
class _MockLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass
mock_structlog.get_logger = lambda: _MockLogger()
sys.modules["structlog"] = mock_structlog

# Register the module name first so dataclass resolution works
_validator_path = os.path.join(os.path.dirname(__file__), "..", "src", "lucy", "tools", "code_validator.py")
_spec = importlib.util.spec_from_file_location(
    "lucy.tools.code_validator",
    _validator_path,
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["lucy.tools.code_validator"] = _mod
_spec.loader.exec_module(_mod)

validate_python = _mod.validate_python
analyze_execution_error = _mod.analyze_execution_error
ValidationResult = _mod.ValidationResult


# ═══════════════════════════════════════════════════════════════════════════
# SYNTAX VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class TestSyntaxValidation:
    """Test that syntax errors are caught before execution."""

    def test_valid_syntax(self):
        result = validate_python("x = 1\nprint(x)")
        assert result.valid

    def test_syntax_error_missing_colon(self):
        result = validate_python("if True\n    print('yes')")
        assert not result.valid
        assert len(result.errors) == 1
        assert result.errors[0].category == "syntax"

    def test_syntax_error_unmatched_paren(self):
        result = validate_python("print('hello'")
        assert not result.valid
        assert result.errors[0].category == "syntax"

    def test_syntax_error_invalid_indent(self):
        result = validate_python("  x = 1\ny = 2")
        assert not result.valid
        assert result.errors[0].category == "syntax"

    def test_syntax_error_includes_line_number(self):
        result = validate_python("x = 1\nif True\n    pass")
        assert not result.valid
        assert result.errors[0].line is not None
        assert result.errors[0].line == 2

    def test_empty_code_is_valid(self):
        result = validate_python("")
        assert result.valid

    def test_comment_only_is_valid(self):
        result = validate_python("# just a comment")
        assert result.valid

    def test_multiline_string_is_valid(self):
        result = validate_python('x = """hello\nworld"""')
        assert result.valid


# ═══════════════════════════════════════════════════════════════════════════
# SCOPE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

class TestScopeAnalysis:
    """Test detection of undefined variables."""

    def test_defined_variable_ok(self):
        result = validate_python("x = 42\nprint(x)")
        assert result.valid

    def test_undefined_variable_detected(self):
        result = validate_python("print(users)")
        assert not result.valid
        errors = [i for i in result.issues if i.category == "scope"]
        assert any("users" in i.message for i in errors)

    def test_imported_name_ok(self):
        result = validate_python("import json\ndata = json.loads('{}')")
        assert result.valid

    def test_from_import_ok(self):
        result = validate_python("from pathlib import Path\np = Path('.')")
        assert result.valid

    def test_function_def_ok(self):
        result = validate_python("def hello():\n    return 42\nhello()")
        assert result.valid

    def test_class_def_ok(self):
        result = validate_python("class Foo:\n    pass\nf = Foo()")
        assert result.valid

    def test_for_loop_target_ok(self):
        result = validate_python("for i in range(10):\n    print(i)")
        assert result.valid

    def test_with_statement_ok(self):
        result = validate_python(
            "with open('test.txt') as f:\n    data = f.read()\nprint(data)"
        )
        assert result.valid

    def test_comprehension_scope_ok(self):
        result = validate_python("items = [1, 2, 3]\nresult = [x * 2 for x in items]")
        assert result.valid

    def test_dict_comprehension_ok(self):
        result = validate_python(
            "data = {'a': 1, 'b': 2}\nresult = {k: v * 2 for k, v in data.items()}"
        )
        assert result.valid

    def test_walrus_operator_ok(self):
        result = validate_python(
            "items = [1, 2, 3, 4, 5]\n"
            "filtered = [y for x in items if (y := x * 2) > 4]"
        )
        assert result.valid

    def test_augmented_assign_defines_name(self):
        result = validate_python("count = 0\ncount += 1\nprint(count)")
        assert result.valid

    def test_except_handler_defines_name(self):
        result = validate_python(
            "try:\n    x = 1/0\nexcept ZeroDivisionError as e:\n    print(e)"
        )
        assert result.valid

    def test_star_import_skips_scope_check(self):
        result = validate_python("from os.path import *\nresult = join('a', 'b')")
        assert result.valid  # Can't know what * imports

    def test_tuple_unpack_ok(self):
        result = validate_python("a, b = 1, 2\nprint(a + b)")
        assert result.valid

    def test_builtin_functions_ok(self):
        result = validate_python("x = len([1, 2, 3])\nprint(x)")
        assert result.valid

    def test_nested_function_ok(self):
        """Function bodies are separate scope — shouldn't flag their internals."""
        result = validate_python(
            "def process(data):\n"
            "    result = [x for x in data]\n"
            "    return result\n"
            "output = process([1, 2, 3])\n"
            "print(output)"
        )
        assert result.valid


# ═══════════════════════════════════════════════════════════════════════════
# IMPORT CHECKING
# ═══════════════════════════════════════════════════════════════════════════

class TestImportChecking:
    """Test that import availability is verified."""

    def test_stdlib_import_ok(self):
        result = validate_python("import json\nimport os\nimport sys")
        assert result.valid

    def test_unknown_module_flagged(self):
        result = validate_python("import nonexistent_module_xyz")
        issues = [i for i in result.issues if i.category == "import"]
        assert len(issues) > 0

    def test_common_third_party_ok(self):
        result = validate_python("import requests")
        assert result.valid

    def test_from_import_checked(self):
        result = validate_python("from nonexistent_module_xyz import foo")
        issues = [i for i in result.issues if i.category == "import"]
        assert len(issues) > 0


# ═══════════════════════════════════════════════════════════════════════════
# AUTO-FIX
# ═══════════════════════════════════════════════════════════════════════════

class TestAutoFix:
    """Test automatic fixing of common issues."""

    def test_auto_fix_missing_json(self):
        result = validate_python("data = json.loads('{}')", auto_fix=True)
        assert result.fixed_code is not None
        assert "import json" in result.fixed_code

    def test_auto_fix_missing_pd(self):
        result = validate_python(
            "df = pd.DataFrame({'a': [1, 2]})\nprint(df)",
            auto_fix=True,
        )
        assert result.fixed_code is not None
        assert "import pandas as pd" in result.fixed_code

    def test_auto_fix_missing_np(self):
        result = validate_python(
            "arr = np.array([1, 2, 3])\nprint(arr)",
            auto_fix=True,
        )
        assert result.fixed_code is not None
        assert "import numpy as np" in result.fixed_code

    def test_auto_fix_missing_Path(self):
        result = validate_python(
            "p = Path('.')\nprint(p.resolve())",
            auto_fix=True,
        )
        assert result.fixed_code is not None
        assert "from pathlib import Path" in result.fixed_code

    def test_no_auto_fix_for_syntax_errors(self):
        result = validate_python("if True\n    pass", auto_fix=True)
        assert not result.valid
        assert result.fixed_code is None

    def test_no_auto_fix_for_unknown_vars(self):
        result = validate_python("print(my_custom_variable)", auto_fix=True)
        # Unknown variables are errors — no auto-fix
        assert not result.valid
        assert result.fixed_code is None

    def test_auto_fix_disabled(self):
        result = validate_python("data = json.loads('{}')", auto_fix=False)
        assert result.fixed_code is None


# ═══════════════════════════════════════════════════════════════════════════
# ERROR ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

class TestErrorAnalysis:
    """Test runtime error analysis produces useful hints."""

    def test_nameerror_known_import(self):
        hint = analyze_execution_error(
            "NameError: name 'json' is not defined",
            "data = json.loads('{}')",
        )
        assert "import json" in hint

    def test_nameerror_unknown_variable(self):
        hint = analyze_execution_error(
            "NameError: name 'users' is not defined",
            "print(users)",
        )
        assert "independent" in hint.lower()
        assert "users" in hint

    def test_modulenotfounderror(self):
        hint = analyze_execution_error(
            "ModuleNotFoundError: No module named 'sklearn'",
            "import sklearn",
        )
        assert "sklearn" in hint
        assert "not available" in hint.lower()

    def test_keyerror(self):
        hint = analyze_execution_error(
            "KeyError: 'email'",
            "data = {}\nprint(data['email'])",
        )
        assert "email" in hint
        assert ".get(" in hint

    def test_indexerror(self):
        hint = analyze_execution_error(
            "IndexError: list index out of range",
            "data = []\nprint(data[0])",
        )
        assert "length" in hint.lower() or "range" in hint.lower()

    def test_attributeerror(self):
        hint = analyze_execution_error(
            "AttributeError: 'str' object has no attribute 'append'",
            "x = 'hello'\nx.append('world')",
        )
        assert "str" in hint
        assert "append" in hint

    def test_typeerror_none(self):
        hint = analyze_execution_error(
            "TypeError: 'NoneType' object is not subscriptable",
            "result = None\nprint(result[0])",
        )
        assert "None" in hint

    def test_filenotfounderror(self):
        hint = analyze_execution_error(
            "FileNotFoundError: [Errno 2] No such file or directory: 'data.csv'",
            "open('data.csv')",
        )
        assert "file" in hint.lower()

    def test_timeout(self):
        hint = analyze_execution_error(
            "Execution timed out after 60s",
            "while True: pass",
        )
        assert "timeout" in hint.lower() or "timed out" in hint.lower()

    def test_generic_error(self):
        hint = analyze_execution_error(
            "SomeWeirdError: unexpected thing happened",
            "x = weird_stuff()",
        )
        assert "independent" in hint.lower()


# ═══════════════════════════════════════════════════════════════════════════
# FORMAT FOR LLM
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatForLLM:
    """Test that validation results format nicely for LLM consumption."""

    def test_valid_result_message(self):
        result = validate_python("x = 1\nprint(x)")
        msg = result.format_for_llm()
        assert "passed" in msg.lower()

    def test_error_result_has_hints(self):
        result = validate_python("print(undefined_var)")
        msg = result.format_for_llm()
        assert "undefined_var" in msg
        assert "IMPORTANT" in msg
        assert "independent" in msg.lower()

    def test_syntax_error_has_fix_hint(self):
        result = validate_python("if True\n    pass")
        msg = result.format_for_llm()
        assert "syntax" in msg.lower()
        assert "Fix" in msg


# ═══════════════════════════════════════════════════════════════════════════
# REAL-WORLD LLM CODE PATTERNS
# ═══════════════════════════════════════════════════════════════════════════

class TestRealWorldPatterns:
    """Test patterns actually seen in LLM-generated code failures."""

    def test_llm_references_previous_variable(self):
        """Common: LLM reuses a variable from a previous execution."""
        result = validate_python(
            "# Continuing from the previous analysis\n"
            "filtered = [u for u in users if u['active']]\n"
            "print(len(filtered))"
        )
        assert not result.valid
        errors = [i for i in result.issues if i.category == "scope"]
        assert any("users" in i.message for i in errors)

    def test_llm_forgets_import(self):
        """Common: LLM uses a library without importing it."""
        result = validate_python(
            "response = requests.get('https://api.example.com/data')\n"
            "data = response.json()\n"
            "print(data)"
        )
        # 'requests' should be flagged as undefined but auto-fixable
        issues = [i for i in result.issues if "requests" in i.message]
        assert len(issues) > 0

    def test_llm_complete_self_contained(self):
        """Test a well-formed, self-contained code block."""
        code = """\
import json
import urllib.request

url = "https://api.example.com/data"
req = urllib.request.Request(url)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    print(json.dumps(data, indent=2))
except Exception as e:
    print(f"Error: {e}")
"""
        result = validate_python(code)
        assert result.valid

    def test_llm_pandas_without_import(self):
        """Common: LLM uses pd.DataFrame without importing pandas."""
        result = validate_python(
            "df = pd.DataFrame({'name': ['Alice', 'Bob'], 'score': [90, 85]})\n"
            "print(df.describe())",
            auto_fix=True,
        )
        assert result.fixed_code is not None
        assert "import pandas as pd" in result.fixed_code

    def test_llm_multiple_missing_imports(self):
        """Test auto-fix with multiple missing common imports."""
        result = validate_python(
            "data = json.loads('{}')\n"
            "p = Path('.')\n"
            "print(os.getcwd())",
            auto_fix=True,
        )
        assert result.fixed_code is not None
        assert "import json" in result.fixed_code
        assert "from pathlib import Path" in result.fixed_code
        assert "import os" in result.fixed_code

    def test_fstring_with_braces(self):
        """f-strings with complex expressions should not break validation."""
        result = validate_python(
            "data = {'key': 'value'}\n"
            "print(f\"Result: {data['key']}\")"
        )
        assert result.valid
