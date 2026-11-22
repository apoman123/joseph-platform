"""Enforces Google Python Style rules that ruff cannot express.

* Every module, class and function (public, private and nested) has a
  docstring.
* ``from x import y`` is used only when ``y`` is a module, except for the
  typing-support modules Google exempts.
* Relative imports are forbidden.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest

_PROJECT = pathlib.Path(__file__).resolve().parents[2]
_ROOTS = (_PROJECT / "src", _PROJECT / "tests")
_EXEMPT_FROM_MODULES = frozenset(
    {"__future__", "typing", "collections.abc", "typing_extensions"}
)


def _python_files() -> list[pathlib.Path]:
    """Lists every Python file under ``src`` and ``tests``.

    Returns:
        Sorted list of paths.
    """
    files: list[pathlib.Path] = []
    for root in _ROOTS:
        files.extend(sorted(root.rglob("*.py")))
    return files


def _is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Checks whether a function is a ``typing.overload`` stub.

    Args:
        node: Function node.

    Returns:
        True when decorated with ``overload``.
    """
    for decorator in node.decorator_list:
        name = ast.unparse(decorator)
        if name.endswith("overload"):
            return True
    return False


def _missing_docstrings(path: pathlib.Path) -> list[str]:
    """Finds definitions without docstrings in one file.

    Args:
        path: Python source file.

    Returns:
        ``file:line name`` entries for each violation.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if ast.get_docstring(tree) is None:
        problems.append(f"{path}:1 <module>")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_overload(node):
                continue
        elif not isinstance(node, ast.ClassDef):
            continue
        if ast.get_docstring(node) is None:
            problems.append(f"{path}:{node.lineno} {node.name}")
    return problems


def _is_module(parent: str, name: str) -> bool:
    """Checks whether ``parent.name`` resolves to a module or package.

    Args:
        parent: Absolute dotted module path.
        name: Imported attribute name.

    Returns:
        True when ``parent.name`` is importable as a module.
    """
    try:
        return importlib.util.find_spec(f"{parent}.{name}") is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _import_violations(path: pathlib.Path) -> list[str]:
    """Finds imports that bind non-module names or use relative paths.

    Args:
        path: Python source file.

    Returns:
        ``file:line statement`` entries for each violation.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            problems.append(f"{path}:{node.lineno} relative import")
            continue
        module = node.module or ""
        if module in _EXEMPT_FROM_MODULES:
            continue
        for alias in node.names:
            if not _is_module(module, alias.name):
                problems.append(
                    f"{path}:{node.lineno} from {module} import {alias.name}"
                )
    return problems


@pytest.mark.parametrize(
    "path", _python_files(), ids=lambda p: str(p.relative_to(_PROJECT))
)
def test_every_definition_has_a_docstring(path: pathlib.Path) -> None:
    """Every module, class and function carries a docstring."""
    assert _missing_docstrings(path) == []


@pytest.mark.parametrize(
    "path", _python_files(), ids=lambda p: str(p.relative_to(_PROJECT))
)
def test_imports_follow_google_style(path: pathlib.Path) -> None:
    """Only modules are imported by name; no relative imports."""
    assert _import_violations(path) == []
