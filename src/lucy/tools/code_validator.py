"""Pre-execution code validator for Lucy's code execution pipeline.

Catches common LLM code-generation mistakes BEFORE execution:

1. **Syntax errors** — ast.parse() catches them instantly
2. **Undefined variables** — detects references to names not defined in scope
3. **Missing imports** — verifies modules are actually available
4. **Self-containedness** — ensures code doesn't depend on external state

The validator also provides structured, actionable hints that help the LLM
self-correct without wasting execution attempts.

Architecture:
    code_executor.py → validate_python() → execute / auto-fix / reject
"""

from __future__ import annotations

import ast
import builtins
import importlib.util
import sys
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION RESULT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationIssue:
    """A single validation issue found in the code."""

    severity: str  # "error" | "warning"
    category: str  # "syntax" | "import" | "scope" | "self_contained"
    message: str
    line: int | None = None
    fix_hint: str = ""
    auto_fixable: bool = False


@dataclass
class ValidationResult:
    """Result of code validation."""

    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    fixed_code: str | None = None  # If auto-fix was applied

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def format_for_llm(self) -> str:
        """Format validation issues as a structured hint for the LLM."""
        if not self.issues:
            return "Code validation passed."

        lines = ["⚠️ Code validation found issues:"]
        for i, issue in enumerate(self.issues, 1):
            loc = f" (line {issue.line})" if issue.line else ""
            lines.append(f"  {i}. [{issue.severity.upper()}] {issue.message}{loc}")
            if issue.fix_hint:
                lines.append(f"     → Fix: {issue.fix_hint}")

        lines.append("")
        lines.append(
            "IMPORTANT: Each code execution is independent — no shared state "
            "between calls. All variables, imports, and data must be defined "
            "within the same code block."
        )
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# KNOWN MODULES — what's available in the sandbox
# ═══════════════════════════════════════════════════════════════════════════

# Modules guaranteed available in the Composio sandbox + local fallback
_STDLIB_MODULES = {
    "os", "sys", "json", "csv", "re", "math", "random", "time", "datetime",
    "collections", "itertools", "functools", "operator", "string", "textwrap",
    "io", "pathlib", "glob", "shutil", "tempfile", "copy", "pprint",
    "hashlib", "hmac", "base64", "binascii", "struct", "codecs",
    "urllib", "http", "email", "html", "xml", "socket", "ssl",
    "subprocess", "threading", "multiprocessing", "asyncio", "concurrent",
    "typing", "abc", "dataclasses", "enum", "contextlib",
    "logging", "warnings", "traceback", "inspect",
    "statistics", "decimal", "fractions",
    "argparse", "configparser", "shelve", "sqlite3",
    "unittest", "doctest",
    "uuid", "secrets",
    "gzip", "zipfile", "tarfile", "bz2", "lzma",
    "pickle", "marshal",
    "ast", "dis", "token", "tokenize",
    "calendar", "locale",
    "heapq", "bisect", "array", "queue",
    "platform", "sysconfig",
}

# Third-party packages typically available in data-science / API sandboxes
_COMMON_THIRD_PARTY = {
    "requests", "httpx", "aiohttp",
    "pandas", "numpy", "scipy",
    "matplotlib", "seaborn", "plotly",
    "beautifulsoup4", "bs4", "lxml",
    "pyyaml", "yaml", "toml",
    "pillow", "PIL",
    "openpyxl", "xlsxwriter",
    "tabulate",
    "dateutil", "pytz",
    "tqdm",
    "jinja2",
}

# Modules that might NOT be available — warn but don't block
_RISKY_MODULES = {
    "sklearn", "scikit-learn", "tensorflow", "torch", "pytorch",
    "transformers", "openai", "anthropic",
    "django", "flask", "fastapi",
    "sqlalchemy", "psycopg2", "pymongo",
    "boto3", "google.cloud",
    "celery", "redis",
}


def _is_module_available(module_name: str) -> bool:
    """Check if a module can be imported (without actually importing it)."""
    top_level = module_name.split(".")[0]
    if top_level in _STDLIB_MODULES:
        return True
    if top_level in _COMMON_THIRD_PARTY:
        return True
    # Actually probe the system
    try:
        return importlib.util.find_spec(top_level) is not None
    except (ModuleNotFoundError, ValueError):
        return False


# ═══════════════════════════════════════════════════════════════════════════
# BUILTIN NAMES
# ═══════════════════════════════════════════════════════════════════════════

_BUILTIN_NAMES = set(dir(builtins))
# Add common implicit names the LLM might use
_BUILTIN_NAMES |= {
    "__name__", "__file__", "__doc__", "__builtins__",
    "__all__", "__spec__", "__loader__", "__package__",
    # Common iteration targets that are typically assigned in-scope
    "i", "j", "k", "x", "y", "item", "row", "col", "key", "val", "value",
    "line", "char", "elem", "entry", "record", "idx", "index",
    # Common exception names
    "e", "ex", "err", "exc",
    # Comprehension variables (ast visitor will handle these)
    "_",
}


# ═══════════════════════════════════════════════════════════════════════════
# AST-BASED SCOPE ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class _ScopeAnalyzer(ast.NodeVisitor):
    """Walk the AST to collect defined names and referenced names.

    This is deliberately conservative — it flags only clear cases of
    undefined variables, not complex dynamic patterns.
    """

    def __init__(self) -> None:
        self.defined: set[str] = set()
        self.referenced: set[str] = set()
        self.imported_modules: list[str] = []
        self.imported_names: set[str] = set()
        # Track comprehension/generator scope variables
        self._comp_vars: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            self.defined.add(name.split(".")[0])
            self.imported_modules.append(alias.name)
            self.imported_names.add(name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imported_modules.append(node.module)
        for alias in node.names:
            name = alias.asname or alias.name
            if name == "*":
                # Star imports — can't track what they define
                self.defined.add("*")
            else:
                self.defined.add(name)
                self.imported_names.add(name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defined.add(node.name)
        # Don't recurse into function bodies for module-level scope analysis
        # (function parameters and locals are their own scope)
        for decorator in node.decorator_list:
            self.visit(decorator)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.defined.add(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defined.add(node.name)
        for base in node.bases:
            self.visit(base)
        for decorator in node.decorator_list:
            self.visit(decorator)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._collect_targets(target)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.target:
            self._collect_targets(node.target)
        if node.value:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._collect_targets(node.target)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        self._collect_targets(node.target)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars:
                self._collect_targets(item.optional_vars)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.defined.add(node.name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.referenced.add(node.id)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self.defined.add(node.id)

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.defined.add(name)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self.defined.add(name)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        # Walrus operator (:=)
        self._collect_targets(node.target)
        self.visit(node.value)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, node.key, node.value)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def _visit_comprehension(
        self, generators: list[ast.comprehension], *elts: ast.expr
    ) -> None:
        """Handle comprehension scope — variables defined in generators are local."""
        saved = self._comp_vars.copy()
        for gen in generators:
            self._collect_comp_targets(gen.target)
            self.visit(gen.iter)
            for if_clause in gen.ifs:
                self.visit(if_clause)
        for elt in elts:
            self.visit(elt)
        self._comp_vars = saved

    def _collect_comp_targets(self, node: ast.expr) -> None:
        """Collect comprehension target names into the comp_vars set."""
        if isinstance(node, ast.Name):
            self._comp_vars.add(node.id)
            self.defined.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                self._collect_comp_targets(elt)
        elif isinstance(node, ast.Starred):
            self._collect_comp_targets(node.value)

    def _collect_targets(self, node: ast.expr) -> None:
        """Extract assigned names from assignment targets."""
        if isinstance(node, ast.Name):
            self.defined.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                self._collect_targets(elt)
        elif isinstance(node, ast.Starred):
            self._collect_targets(node.value)
        # ast.Attribute and ast.Subscript assignments don't define new names
        # at module scope — the base object must already exist

    def get_undefined(self) -> set[str]:
        """Return names that are referenced but not defined or built-in."""
        if "*" in self.defined:
            # Star import means we can't be sure what's defined
            return set()

        all_defined = self.defined | _BUILTIN_NAMES | self._comp_vars
        return self.referenced - all_defined


# ═══════════════════════════════════════════════════════════════════════════
# COMMON AUTO-FIXES
# ═══════════════════════════════════════════════════════════════════════════

# Map of commonly referenced names → the import that provides them
_COMMON_MISSING_IMPORTS: dict[str, str] = {
    "pd": "import pandas as pd",
    "np": "import numpy as np",
    "plt": "import matplotlib.pyplot as plt",
    "sns": "import seaborn as sns",
    "re": "import re",
    "json": "import json",
    "csv": "import csv",
    "os": "import os",
    "sys": "import sys",
    "Path": "from pathlib import Path",
    "datetime": "from datetime import datetime",
    "timedelta": "from datetime import timedelta",
    "date": "from datetime import date",
    "defaultdict": "from collections import defaultdict",
    "Counter": "from collections import Counter",
    "OrderedDict": "from collections import OrderedDict",
    "deque": "from collections import deque",
    "namedtuple": "from collections import namedtuple",
    "dataclass": "from dataclasses import dataclass",
    "field": "from dataclasses import field",
    "Enum": "from enum import Enum",
    "BeautifulSoup": "from bs4 import BeautifulSoup",
    "requests": "import requests",
    "httpx": "import httpx",
    "yaml": "import yaml",
    "StringIO": "from io import StringIO",
    "BytesIO": "from io import BytesIO",
    "sleep": "from time import sleep",
    "pprint": "from pprint import pprint",
    "reduce": "from functools import reduce",
    "partial": "from functools import partial",
    "lru_cache": "from functools import lru_cache",
    "wraps": "from functools import wraps",
    "chain": "from itertools import chain",
    "product": "from itertools import product",
    "combinations": "from itertools import combinations",
    "permutations": "from itertools import permutations",
    "tabulate": "from tabulate import tabulate",
    "uuid4": "from uuid import uuid4",
    "uuid": "import uuid",
    "math": "import math",
    "random": "import random",
    "time": "import time",
    "hashlib": "import hashlib",
    "base64": "import base64",
    "copy": "import copy",
    "deepcopy": "from copy import deepcopy",
    "textwrap": "import textwrap",
    "shutil": "import shutil",
    "glob": "import glob",
    "tempfile": "import tempfile",
    "subprocess": "import subprocess",
    "asyncio": "import asyncio",
    "typing": "import typing",
    "Any": "from typing import Any",
    "Dict": "from typing import Dict",
    "List": "from typing import List",
    "Optional": "from typing import Optional",
    "Tuple": "from typing import Tuple",
    "Union": "from typing import Union",
}


def _try_auto_fix(code: str, issues: list[ValidationIssue]) -> str | None:
    """Attempt to auto-fix the code based on validation issues.

    Returns fixed code if successful, None if auto-fix isn't possible.
    Only fixes safe, unambiguous issues:
    - Missing common imports (pd, np, json, etc.)
    """
    missing_imports: list[str] = []
    unfixable = False

    for issue in issues:
        if issue.category == "syntax":
            # Can't auto-fix syntax errors
            unfixable = True
            break

        if issue.category == "scope" and issue.auto_fixable:
            # Check if it's a known missing import
            var_name = issue.message.split("'")[1] if "'" in issue.message else ""
            if var_name in _COMMON_MISSING_IMPORTS:
                import_line = _COMMON_MISSING_IMPORTS[var_name]
                if import_line not in missing_imports:
                    missing_imports.append(import_line)
            else:
                unfixable = True
                break

        if issue.category == "import" and not issue.auto_fixable:
            unfixable = True
            break

    if unfixable or not missing_imports:
        return None

    # Prepend missing imports
    import_block = "\n".join(missing_imports)
    fixed = f"{import_block}\n\n{code}"

    # Re-validate the fixed code to ensure we didn't break anything
    try:
        ast.parse(fixed)
    except SyntaxError:
        return None

    logger.info(
        "code_auto_fixed",
        added_imports=missing_imports,
    )
    return fixed


# ═══════════════════════════════════════════════════════════════════════════
# ERROR ANALYSIS — for post-execution failures
# ═══════════════════════════════════════════════════════════════════════════

def analyze_execution_error(error_text: str, code: str) -> str:
    """Analyze a runtime error and produce an actionable hint for the LLM.

    This is called AFTER execution fails, to help the LLM understand
    what went wrong and how to fix it.

    Returns a structured hint string.
    """
    error_lower = error_text.lower()
    hints: list[str] = []

    # ── NameError: undefined variable ──
    if "nameerror" in error_lower:
        # Extract the variable name
        import re
        match = re.search(r"name '(\w+)' is not defined", error_text)
        var_name = match.group(1) if match else "unknown"

        if var_name in _COMMON_MISSING_IMPORTS:
            hints.append(
                f"Variable '{var_name}' is not defined. Add: {_COMMON_MISSING_IMPORTS[var_name]}"
            )
        else:
            hints.append(
                f"Variable '{var_name}' is not defined. "
                f"Each execution is independent — no shared state between calls. "
                f"Define all variables within this code block, or re-fetch/re-compute the data."
            )

    # ── ModuleNotFoundError ──
    elif "modulenotfounderror" in error_lower:
        import re
        match = re.search(r"no module named '([\w.]+)'", error_text, re.IGNORECASE)
        module_name = match.group(1) if match else "unknown"
        alternatives = _suggest_module_alternative(module_name)
        hints.append(
            f"Module '{module_name}' is not available in this sandbox. "
            f"{alternatives}"
        )

    # ── ImportError ──
    elif "importerror" in error_lower:
        import re
        match = re.search(r"cannot import name '(\w+)' from '([\w.]+)'", error_text)
        if match:
            name, module = match.group(1), match.group(2)
            hints.append(
                f"Cannot import '{name}' from '{module}'. "
                f"Check the correct import path, or the module version may differ."
            )
        else:
            hints.append(
                "Import failed. Verify the import path and that the module is available."
            )

    # ── TypeError ──
    elif "typeerror" in error_lower:
        if "argument" in error_lower and ("missing" in error_lower or "unexpected" in error_lower):
            hints.append(
                "Function called with wrong arguments. Check the function signature."
            )
        elif "'nonetype'" in error_lower:
            hints.append(
                "Operating on None. A previous operation likely returned None "
                "instead of the expected value. Add a None check."
            )
        else:
            hints.append("Type mismatch. Check variable types before operations.")

    # ── KeyError ──
    elif "keyerror" in error_lower:
        import re
        match = re.search(r"keyerror: ['\"]?(\w+)['\"]?", error_text, re.IGNORECASE)
        key = match.group(1) if match else "unknown"
        hints.append(
            f"Key '{key}' not found in dictionary. "
            f"Use .get('{key}', default) for safe access, or print the available keys first."
        )

    # ── IndexError ──
    elif "indexerror" in error_lower:
        hints.append(
            "List index out of range. Check the list length before accessing by index. "
            "Use `if len(my_list) > idx:` or try/except."
        )

    # ── AttributeError ──
    elif "attributeerror" in error_lower:
        import re
        match = re.search(
            r"'(\w+)' object has no attribute '(\w+)'", error_text
        )
        if match:
            obj_type, attr = match.group(1), match.group(2)
            hints.append(
                f"'{obj_type}' has no attribute '{attr}'. "
                f"Check the object type — it may not be what you expect. "
                f"Print type(variable) to debug."
            )
        else:
            hints.append("Attribute not found. Check the object type.")

    # ── FileNotFoundError ──
    elif "filenotfounderror" in error_lower:
        hints.append(
            "File not found. Each execution starts with a clean working directory. "
            "Files from previous executions are not persisted. "
            "Create the file within this code block, or use workspace tools to read existing files."
        )

    # ── JSONDecodeError ──
    elif "jsondecode" in error_lower or "json.decoder" in error_lower:
        hints.append(
            "JSON parsing failed. The response may not be valid JSON. "
            "Print the raw response first to inspect it, then parse."
        )

    # ── Timeout ──
    elif "timeout" in error_lower or "timed out" in error_lower:
        hints.append(
            "Execution timed out. Simplify the operation, process less data, "
            "or break into smaller steps."
        )

    # ── SyntaxError (at runtime, e.g. from eval/exec) ──
    elif "syntaxerror" in error_lower:
        hints.append(
            "Syntax error in the code. Check for missing colons, parentheses, "
            "or incorrect indentation."
        )

    # ── Generic fallback ──
    if not hints:
        hints.append(
            f"Execution error: {error_text[:200]}. "
            f"Each execution is independent with no shared state. "
            f"Ensure all variables and imports are defined within the code block."
        )

    return "\n".join([
        "🔍 Error Analysis:",
        *[f"  • {h}" for h in hints],
        "",
        "Remember: Each code execution is a fresh, independent environment.",
    ])


def _suggest_module_alternative(module_name: str) -> str:
    """Suggest alternatives for unavailable modules."""
    alternatives: dict[str, str] = {
        "pandas": "Use json + built-in modules for data processing, or csv module for tabular data.",
        "numpy": "Use the math module and list comprehensions for numerical operations.",
        "sklearn": "Not available. Use basic statistics from the statistics module.",
        "tensorflow": "Not available. This sandbox is for data processing, not ML training.",
        "torch": "Not available. This sandbox is for data processing, not ML training.",
        "pytorch": "Not available. This sandbox is for data processing, not ML training.",
        "scipy": "Use math module for basic scientific computing.",
        "matplotlib": "Use text-based output or generate CSV data for external plotting.",
        "seaborn": "Use text-based output or generate CSV data for external plotting.",
        "flask": "Not needed — this is a code execution sandbox, not a web server.",
        "django": "Not needed — this is a code execution sandbox, not a web server.",
        "sqlalchemy": "Use sqlite3 from the standard library for database operations.",
        "psycopg2": "Use httpx to call your API instead of connecting to the DB directly.",
        "pymongo": "Use httpx to call your API instead of connecting to MongoDB directly.",
        "boto3": "Use httpx to call AWS APIs directly with signed requests.",
        "redis": "Not available in this sandbox.",
    }
    return alternatives.get(module_name, "Try using standard library alternatives.")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN VALIDATION ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def validate_python(code: str, *, auto_fix: bool = True) -> ValidationResult:
    """Validate Python code before execution.

    Performs:
    1. Syntax check (ast.parse)
    2. Scope analysis (undefined variables)
    3. Import availability check

    Args:
        code: Python source code to validate.
        auto_fix: If True, attempt to fix common issues (missing imports).

    Returns:
        ValidationResult with issues, validity status, and optionally fixed code.
    """
    issues: list[ValidationIssue] = []

    # ── Step 1: Syntax check ──
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        issues.append(ValidationIssue(
            severity="error",
            category="syntax",
            message=f"Syntax error: {e.msg}",
            line=e.lineno,
            fix_hint=(
                f"Fix the syntax error at line {e.lineno}: {e.msg}. "
                f"Check for missing colons, unmatched brackets, or indentation."
            ),
        ))
        return ValidationResult(valid=False, issues=issues)

    # ── Step 2: Scope analysis ──
    analyzer = _ScopeAnalyzer()
    analyzer.visit(tree)

    undefined = analyzer.get_undefined()
    for name in sorted(undefined):
        # Skip common false positives
        if name.startswith("_") and len(name) > 1:
            continue

        is_known_import = name in _COMMON_MISSING_IMPORTS
        issues.append(ValidationIssue(
            severity="warning" if is_known_import else "error",
            category="scope",
            message=f"Name '{name}' is used but not defined in this code block.",
            fix_hint=(
                f"Add: {_COMMON_MISSING_IMPORTS[name]}"
                if is_known_import
                else (
                    f"Define '{name}' within this code block. "
                    f"Each execution is independent — variables from previous "
                    f"calls don't persist."
                )
            ),
            auto_fixable=is_known_import,
        ))

    # ── Step 3: Import availability ──
    for module_name in analyzer.imported_modules:
        if not _is_module_available(module_name):
            top_level = module_name.split(".")[0]
            is_risky = top_level in _RISKY_MODULES
            alt = _suggest_module_alternative(top_level)
            issues.append(ValidationIssue(
                severity="warning" if is_risky else "error",
                category="import",
                message=f"Module '{module_name}' may not be available.",
                fix_hint=alt,
            ))

    # ── Step 4: Determine validity ──
    has_errors = any(i.severity == "error" for i in issues)
    has_fixable = any(i.auto_fixable for i in issues)

    # ── Step 5: Attempt auto-fix if enabled ──
    fixed_code = None
    if auto_fix and issues and has_fixable and not has_errors:
        fixed_code = _try_auto_fix(code, issues)
        if fixed_code:
            # Re-validate the fixed code
            recheck = validate_python(fixed_code, auto_fix=False)
            if recheck.valid:
                return ValidationResult(
                    valid=True,
                    issues=issues,  # Keep original issues for logging
                    fixed_code=fixed_code,
                )
            # If re-validation fails, fall through to return original issues

    return ValidationResult(
        valid=not has_errors,
        issues=issues,
        fixed_code=fixed_code,
    )
