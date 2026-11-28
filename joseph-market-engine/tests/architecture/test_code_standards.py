# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Checks the Google Python Style rules that ruff, pydoclint and mypy cannot.

The same file runs in every Python project of the repository: it finds its
project through the nearest ``pyproject.toml`` and checks the ``src``,
``scripts``, ``tests`` and ``launchers`` directories that exist there. Rule
codes and the Google Python Style Guide ("pyguide") sections they enforce:

  GPY001 inline exception without codes or reason (2.1)
  GPY002 inline GPY exception that suppresses nothing (2.1)
  GPY010 licence header (3.8.2)
  GPY011 module, class or function without a docstring (3.8)
  GPY012 section-less docstring that needs Args/Returns/Yields (3.8.3)
  GPY013 Raises misses an exception the function raises (3.8.3)
  GPY014 Attributes misses a public attribute (3.8.4)
  GPY015 Attributes lists a name the class does not define (3.8.4)
  GPY016 property docstring not in attribute style (3.8.3)
  GPY017 summary mood differs within the file (3.8.3)
  GPY018 class summary names the class or the raise context (3.8.4)
  GPY019 comment without a space after "#" (3.8.5)
  GPY020 ``from x import y`` where y is not a module (2.2.4)
  GPY021 relative import (2.2.4)
  GPY022 ``import x as y`` with a non-standard alias (2.2.4)
  GPY030 ``nonlocal`` (2.5, 2.16)
  GPY031 nested function or class that closes over nothing (2.6)
  GPY032 comprehension with several ``for`` or ``if`` clauses (2.7)
  GPY033 conditional expression part spanning lines (2.11)
  GPY034 property that only gets and sets one attribute (2.13)
  GPY035 ``@staticmethod`` (2.17)
  GPY036 ``@classmethod`` that is not a named constructor (2.17)
  GPY037 custom metaclass, ``__del__`` or ``__import__`` (2.19)
  GPY040 backslash line continuation (3.2)
  GPY041 shebang form or shebang on a non-executable file (3.7)
  GPY042 string built with ``+`` from three or more parts (3.10)
  GPY043 string accumulated with ``+=`` in a loop (3.10)
  GPY044 file name not lower_with_under.py (3.16.3)
  GPY045 name with a double leading underscore (3.16.2)
  GPY046 script without ``main()`` behind the main guard (3.17)
  GPY047 module-level call that runs on import (3.17)
  GPY048 type alias not CapWords or not declared as one (3.19.6)
  GPY049 ``# type:`` comment (3.19.8)
  GPY050 type variable name (3.19.10)

An inline exception goes on the line a violation is reported on, names the
code and gives a reason after `` - ``, for example
``@staticmethod  # noqa: GPY035 - pydantic calls validators on the class``.
The same form applies to ruff and pydoclint codes. An exception without a
reason (GPY001) or one that suppresses nothing (GPY002) is a violation.

Typical usage, from the project directory:

  uv run pytest tests/architecture/test_code_standards.py
  uv run python tests/architecture/test_code_standards.py src/some/package
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import functools
import importlib.machinery
import importlib.util
import io
import pathlib
import re
import symtable
import sys
import tokenize
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Sequence
from typing import TypeAlias

import pytest


def _find_project(start: pathlib.Path) -> pathlib.Path:
    """Finds the project root that holds this file.

    Args:
        start: Path of this file.

    Returns:
        The nearest parent directory that contains a ``pyproject.toml``.

    Raises:
        RuntimeError: No parent directory has a ``pyproject.toml``.
    """
    for parent in start.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError(f"no pyproject.toml above {start}")


_PROJECT = _find_project(pathlib.Path(__file__).resolve())
_ROOT_NAMES = ("src", "scripts", "tests", "launchers")


def _extra_import_paths(project: pathlib.Path) -> tuple[str, ...]:
    """Lists the import roots to search besides ``sys.path``.

    Args:
        project: Project root.

    Returns:
        The project and its ``src`` directory; for a project without its own
        package (the system tests), also the sibling engines' ``src``
        directories, whose packages it imports.
    """
    paths = [str(project), str(project / "src")]
    if not (project / "src").is_dir():
        engines = sorted(project.parent.glob("joseph-*-engine"))
        paths.extend(str(engine / "src") for engine in engines)
    return tuple(paths)


_EXTRA_IMPORT_PATHS = _extra_import_paths(_PROJECT)

_Finding: TypeAlias = tuple[int, str, str]
_Definition: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
_Function: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
# Function scopes symtable reports for expressions, not ``def`` statements.
_IMPLICIT_SCOPES = frozenset(
    {"lambda", "genexpr", "listcomp", "setcomp", "dictcomp"}
)

_LICENCE_COPYRIGHT = re.compile(r"# Copyright \d{4} apoman123")
_LICENCE_SPDX = "# SPDX-License-Identifier: MIT"
_SHEBANGS = frozenset({"#!/usr/bin/env python3", "#!/usr/bin/python3"})
_EXEMPT_FROM_MODULES = frozenset(
    {"__future__", "typing", "collections.abc", "typing_extensions"}
)
# Abbreviations that are standard in the library's own documentation.
_STANDARD_ALIASES = frozenset(
    {
        ("numpy", "np"),
        ("pandas", "pd"),
        ("pyarrow", "pa"),
        ("pyarrow.compute", "pc"),
        ("pyarrow.parquet", "pq"),
        ("sqlalchemy", "sa"),
    }
)
# Dunder methods whose parameters are defined by the language or framework
# protocol; their docstrings need no Args or Returns sections.
_SECTIONED_DUNDERS = frozenset(
    {"__init__", "__new__", "__call__", "__init_subclass__", "__post_init__"}
)
# Exceptions that signal a bug or a protocol step, not part of an interface
# (pyguide 3.8.3: API misuse is not documented); Raises may omit them.
_UNDOCUMENTED_EXCEPTIONS = frozenset(
    {
        "AssertionError",
        "NotImplementedError",
        "StopAsyncIteration",
        "StopIteration",
    }
)
_ENUM_BASES = frozenset(
    {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"}
)
_KNOWN_BASES = frozenset(
    {
        "ABC",
        "BaseException",
        "BaseModel",
        "Exception",
        "Generic",
        "NamedTuple",
        "Protocol",
        "TypedDict",
        "object",
    }
    | _ENUM_BASES
)
_NOT_ATTRIBUTES = frozenset({"model_config"})
# joseph_script.Script documents these declarations once for every script.
_SCRIPT_DECLARATIONS = frozenset(
    {
        "ID",
        "TITLE",
        "SHORT_TITLE",
        "DESCRIPTION",
        "CATEGORY",
        "OVERLAY",
        "INPUTS",
        "REQUESTS",
    }
)
_SCRIPT_ROLES = frozenset(
    {"Indicator", "Strategy", "Library", "Filter", "Alert"}
)
_PROPERTY_DECORATORS = frozenset({"property", "cached_property"})
_ALLOWED_METACLASSES = frozenset({"ABCMeta", "EnumMeta", "EnumType"})
_TYPE_VAR_FACTORIES = frozenset({"TypeVar", "ParamSpec", "TypeVarTuple"})
_GENERIC_TYPES = frozenset(
    {
        "Annotated",
        "AsyncGenerator",
        "AsyncIterable",
        "AsyncIterator",
        "Awaitable",
        "Callable",
        "Coroutine",
        "Generator",
        "Iterable",
        "Iterator",
        "Literal",
        "Mapping",
        "MutableMapping",
        "MutableSequence",
        "Optional",
        "Sequence",
        "Union",
        "dict",
        "frozenset",
        "list",
        "set",
        "tuple",
        "type",
    }
)
_SECTION_HEADER = re.compile(
    r"^(?P<indent>\s*)(?P<name>Args|Arguments|Attributes|Example|Examples"
    r"|Keyword Args|Keyword Arguments|Note|Notes|Raises|References|Returns"
    r"|See Also|Todo|Warning|Warnings|Yields):\s*$"
)
# Sections that make pydoclint check a docstring (see pydoclint's
# ``Doc.isShortDocstring``); a docstring without them is "section-less".
_CHECKED_SECTIONS = frozenset(
    {
        "Args",
        "Arguments",
        "Attributes",
        "Example",
        "Examples",
        "Keyword Args",
        "Keyword Arguments",
        "Raises",
        "Returns",
        "Yields",
    }
)
_SECTION_ENTRY = re.compile(r"^(?P<name>\*{0,2}[A-Za-z_][\w.]*)\s*(\(.*\))?:")
_NOQA = re.compile(r"#\s*noqa\b(?P<rest>[^#]*)", re.IGNORECASE)
_NOQA_WITH_REASON = re.compile(
    r"^:\s*(?P<codes>[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)\s+-\s+"
    r"(?P<reason>.*[A-Za-z].*)$"
)
_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore\b(?P<rest>.*)$")
_TYPE_IGNORE_WITH_REASON = re.compile(r"^\[[\w\-, ]+\]\s*#\s*\S")
_TYPE_COMMENT = re.compile(r"^#\s*type:(?!\s*ignore\b)")
_FILE_NAME = re.compile(r"^[a-z_][a-z0-9_]*\.py$")
_CAP_WORDS = re.compile(r"^_?[A-Z][A-Za-z0-9]*$")
_CLASS_SUMMARY_NAMES_CLASS = re.compile(r"^(a |an |the |this )?class\b", re.I)
_RAISE_CONTEXT = re.compile(r"^(raised|raises|thrown|exception raised)\b", re.I)
_PROPERTY_VERBS = frozenset({"Return", "Returns", "Get", "Gets"})

# Verbs whose base form starts an imperative summary ("Build ...") and whose
# third-person form starts a descriptive one ("Builds ..."), for GPY017.
_VERB_TEXT = """
    accept add adjust advance aggregate allocate append apply approve arm
    assign attach authenticate authorize backfill bind block build cache
    calculate call cancel cap capture check choose clamp classify clean
    clear close collect combine compare compile complete compose compute
    configure confirm connect consume convert copy count create decide
    decode decrypt deduplicate define delete deliver derive describe
    deserialize detect determine disable disarm discard dispatch drain drop
    dump emit enable encode encrypt enforce ensure enqueue enrich estimate
    evaluate execute expand expire export expose extend extract fail fetch
    fill filter find finish flatten flush format forward freeze generate get
    grant group guard handle hold identify ignore include increment infer
    initialize insert inspect install invoke iterate join keep launch list
    load lock look make mark match measure merge migrate move normalize
    notify open pack paginate parse pause persist pick pin place poll pop
    populate prepare probe process produce provide prune publish pull push
    put query raise read rebuild receive reconcile redact reduce refresh
    register reject release reload remove render renew reopen replace replay
    require reserve reset resolve restore resume retry return reuse revoke
    rotate route run save scan schedule search select send serialize serve
    settle show shut sign simulate skip slice sort split start stop store
    strip submit subscribe summarize supply swap sync take terminate
    throttle toggle track transform translate trigger trim truncate try
    unlock unpack unregister unsubscribe update upgrade upsert validate
    verify walk warm wrap write
"""
_VERBS = frozenset(_VERB_TEXT.split())


def _third_person(verb: str) -> str:
    """Conjugates a verb in the third person singular.

    Args:
        verb: Base form, such as ``build`` or ``apply``.

    Returns:
        The third-person form, such as ``builds`` or ``applies``.
    """
    if verb.endswith(("s", "x", "z", "ch", "sh", "o")):
        return verb + "es"
    if verb.endswith("y") and verb[-2] not in "aeiou":
        return verb[:-1] + "ies"
    return verb + "s"


_IMPERATIVE = frozenset(verb.capitalize() for verb in _VERBS)
_DESCRIPTIVE = frozenset(_third_person(verb).capitalize() for verb in _VERBS)


@dataclasses.dataclass(frozen=True, order=True)
class Violation:
    """One rule violation in one file.

    Attributes:
        path: File path relative to the project root, POSIX style.
        line: Line the violation is reported on (and the exception goes on).
        code: Rule code, such as ``GPY035``.
        message: What is wrong and how to fix it.
    """

    path: str
    line: int
    code: str
    message: str

    def __str__(self) -> str:
        """Formats the violation as ``path:line: CODE message``."""
        return f"{self.path}:{self.line}: {self.code} {self.message}"


@dataclasses.dataclass(frozen=True)
class _Source:
    """A parsed Python file.

    Attributes:
        relative: Path relative to the project root, POSIX style.
        text: Source text.
        lines: Source lines without line endings.
        tree: Parsed module.
        comments: Comment tokens by line number.
        string_lines: Lines that lie inside a string literal, excluding the
            line the literal ends on.
        is_test: Whether the file is test code (under ``tests``).
    """

    relative: str
    text: str
    lines: tuple[str, ...]
    tree: ast.Module
    comments: dict[int, str]
    string_lines: frozenset[int]
    is_test: bool


def _tokens(text: str) -> tuple[dict[int, str], frozenset[int]]:
    """Collects comment tokens and the lines string literals continue over.

    Args:
        text: Source text.

    Returns:
        A tuple ``(comments, string_lines)``: the comment text by line, and
        every line a string literal starts or continues on, except the line
        the literal ends on.
    """
    comments: dict[int, str] = {}
    string_lines: set[int] = set()
    open_fstrings: list[int] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            comments[token.start[0]] = token.string
        elif token.type == tokenize.STRING:
            string_lines.update(range(token.start[0], token.end[0]))
        elif token.type == tokenize.FSTRING_START:
            open_fstrings.append(token.start[0])
        elif token.type == tokenize.FSTRING_END and open_fstrings:
            string_lines.update(range(open_fstrings.pop(), token.end[0]))
    return comments, frozenset(string_lines)


def _parse(relative: str, text: str) -> _Source:
    """Parses one file for the checks.

    Args:
        relative: Path relative to the project root, POSIX style.
        text: Source text.

    Returns:
        The parsed source.
    """
    comments, string_lines = _tokens(text)
    return _Source(
        relative=relative,
        text=text,
        lines=tuple(text.splitlines()),
        tree=ast.parse(text, filename=relative),
        comments=comments,
        string_lines=string_lines,
        is_test=relative.split("/", 1)[0] == "tests",
    )


def _definitions(
    tree: ast.Module,
) -> Iterator[tuple[_Definition, tuple[ast.AST, ...]]]:
    """Walks every class and function definition with its parents.

    Args:
        tree: Parsed module.

    Yields:
        ``(definition, parents)`` pairs; ``parents`` runs from the module to
        the direct parent of the definition.
    """
    stack: list[tuple[ast.AST, tuple[ast.AST, ...]]] = [(tree, ())]
    while stack:
        node, parents = stack.pop()
        chain = (*parents, node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (*_FUNCTIONS, ast.ClassDef)):
                yield child, chain
            stack.append((child, chain))


def _body_nodes(body: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """Walks statements without entering nested scopes.

    Args:
        body: Statements of one scope.

    Yields:
        Every node of the scope; nested functions, classes and lambdas are
        yielded but not entered.
    """
    stack = list(body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _final_name(node: ast.AST) -> str:
    """Returns the last component of a (called) dotted name.

    Args:
        node: Expression such as ``a.b.C``, ``C`` or ``a.b.C(...)``.

    Returns:
        ``C`` for those examples; an empty string for other expressions.
    """
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _decorators(node: _Definition) -> set[str]:
    """Lists the final names of a definition's decorators.

    Args:
        node: Function or class definition.

    Returns:
        Names such as ``property``, ``setter`` or ``fixture``.
    """
    return {_final_name(decorator) for decorator in node.decorator_list}


def _is_dunder(name: str) -> bool:
    """Checks whether a name has double leading and trailing underscores.

    Args:
        name: Identifier.

    Returns:
        True for names such as ``__init__``.
    """
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _is_test_function(
    source: _Source, node: _Definition, parents: tuple[ast.AST, ...]
) -> bool:
    """Checks whether a definition is a pytest test or hook.

    Args:
        source: File being checked.
        node: Definition.
        parents: Parents of the definition.

    Returns:
        True for ``test*`` functions at module level or in a ``Test*`` class
        of a test file, and for ``pytest_*`` hooks.
    """
    if not source.is_test or not isinstance(node, _FUNCTIONS):
        return False
    if node.name.startswith("pytest_"):
        return True
    parent = parents[-1]
    if isinstance(parent, ast.Module):
        return node.name.startswith("test")
    return (
        isinstance(parent, ast.ClassDef)
        and parent.name.startswith("Test")
        and node.name.startswith("test")
    )


def _sections(docstring: str) -> dict[str, list[str]]:
    """Splits a Google-style docstring into its sections.

    Args:
        docstring: Cleaned docstring (``ast.get_docstring``).

    Returns:
        Section names mapped to the entry names listed in them (the text
        before the colon at the first indentation level of the section).
    """
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    header_indent = 0
    entry_indent: int | None = None
    for line in docstring.expandtabs().splitlines():
        header = _SECTION_HEADER.match(line)
        indent = len(line) - len(line.lstrip())
        if header:
            current = sections.setdefault(header["name"], [])
            header_indent = len(header["indent"])
            entry_indent = None
            continue
        if current is None or not line.strip():
            continue
        if indent <= header_indent:
            current = None
            continue
        if entry_indent is None:
            entry_indent = indent
        if indent == entry_indent:
            entry = _SECTION_ENTRY.match(line.strip())
            if entry:
                current.append(entry["name"])
    return sections


def _summary_word(docstring: str) -> str:
    """Returns the first word of a docstring summary.

    Args:
        docstring: Cleaned docstring.

    Returns:
        The word without surrounding punctuation; empty for a blank text.
    """
    words = docstring.split()
    return words[0].strip(".,:;`'\"()") if words else ""


def _check_licence(source: _Source) -> Iterator[_Finding]:
    """Checks the licence header (GPY010, pyguide 3.8.2).

    Args:
        source: File being checked.

    Yields:
        A finding when the file does not start with the licence header.
    """
    lines = list(source.lines)
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    header_ok = (
        len(lines) >= 2
        and _LICENCE_COPYRIGHT.fullmatch(lines[0]) is not None
        and lines[1]
        in (_LICENCE_SPDX, "# SPDX-License-Identifier: AGPL-3.0-only")
        and (len(lines) == 2 or not lines[2].strip())
    )
    if not header_ok:
        yield (
            1,
            "GPY010",
            "licence header missing: start with '# Copyright <year> "
            "apoman123', an MIT or AGPL-3.0-only SPDX header and a blank line",
        )


def _check_missing_docstrings(source: _Source) -> Iterator[_Finding]:
    """Checks that every module, class and function has a docstring (GPY011).

    Args:
        source: File being checked.

    Yields:
        A finding per definition without a docstring.
    """
    if ast.get_docstring(source.tree) is None:
        yield 1, "GPY011", "module has no docstring"
    for node, _ in _definitions(source.tree):
        if "overload" in _decorators(node):
            continue
        if ast.get_docstring(node) is None:
            yield node.lineno, "GPY011", f"{node.name} has no docstring"


def _documented_args(node: _Function, is_method: bool) -> list[str]:
    """Lists the parameters a docstring's Args section must name.

    Args:
        node: Function definition.
        is_method: Whether the function is defined directly in a class.

    Returns:
        Parameter names, with ``*`` or ``**`` for variadic ones; the bound
        ``self``/``cls`` and underscore-only names are left out.
    """
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if is_method and positional and "staticmethod" not in _decorators(node):
        positional = positional[1:]
    names = [argument.arg for argument in positional]
    if arguments.vararg:
        names.append(f"*{arguments.vararg.arg}")
    names.extend(argument.arg for argument in arguments.kwonlyargs)
    if arguments.kwarg:
        names.append(f"**{arguments.kwarg.arg}")
    return [name for name in names if name.strip("*").strip("_")]


def _is_generator(node: _Function) -> bool:
    """Checks whether a function yields.

    Args:
        node: Function definition.

    Returns:
        True when the function's own body contains ``yield``.
    """
    return any(
        isinstance(child, (ast.Yield, ast.YieldFrom))
        for child in _body_nodes(node.body)
    )


def _returns_value(node: _Function) -> bool:
    """Checks whether a function returns something other than ``None``.

    Args:
        node: Function definition.

    Returns:
        True when the return annotation is not ``None``/``NoReturn``/
        ``Never``, or (without an annotation) when a ``return`` statement
        has a value.
    """
    if node.returns is not None:
        annotation = ast.unparse(node.returns).rsplit(".", 1)[-1]
        return annotation not in {"None", "NoReturn", "Never"}
    return any(
        isinstance(child, ast.Return)
        and child.value is not None
        and not (
            isinstance(child.value, ast.Constant) and child.value.value is None
        )
        for child in _body_nodes(node.body)
    )


def _needs_sections(node: _Function, parents: tuple[ast.AST, ...]) -> bool:
    """Checks whether a function's docstring must follow the section rules.

    Args:
        node: Function definition.
        parents: Parents of the definition.

    Returns:
        False for overload stubs, ``@override`` methods (pyguide 3.8.3.1)
        and protocol dunder methods.
    """
    if _decorators(node) & {"overload", "override"}:
        return False
    is_method = isinstance(parents[-1], ast.ClassDef)
    return not (
        is_method
        and _is_dunder(node.name)
        and node.name not in _SECTIONED_DUNDERS
    )


def _check_sectionless_docstrings(source: _Source) -> Iterator[_Finding]:
    """Checks section-less docstrings against the signature (GPY012).

    pydoclint checks every docstring that has sections; this rule covers the
    ones that have none (pyguide 3.8.3). They must not leave out Args for a
    function with parameters, and a function that returns or yields needs
    a Returns/Yields section unless its summary starts with "Returns" or
    "Yields". Tests and properties are exempt.

    Args:
        source: File being checked.

    Yields:
        A finding per function whose docstring lacks a required section.
    """
    for node, parents in _definitions(source.tree):
        if not isinstance(node, _FUNCTIONS) or not _needs_sections(
            node, parents
        ):
            continue
        if _is_test_function(source, node, parents):
            continue
        docstring = ast.get_docstring(node)
        decorators = _decorators(node)
        if docstring is None or decorators & _PROPERTY_DECORATORS:
            continue
        if set(_sections(docstring)) & _CHECKED_SECTIONS:
            continue
        missing: list[str] = []
        args = _documented_args(node, isinstance(parents[-1], ast.ClassDef))
        if args:
            missing.append(f"Args ({', '.join(args)})")
        word = _summary_word(docstring)
        if _is_generator(node):
            allowed = {"Yield", "Yields"}
            if decorators & {"contextmanager", "asynccontextmanager"}:
                allowed |= {"Return", "Returns"}
            if word not in allowed:
                missing.append("Yields")
        elif _returns_value(node) and word not in {"Return", "Returns"}:
            missing.append("Returns")
        if missing:
            yield (
                node.lineno,
                "GPY012",
                f"{node.name}: docstring needs {', '.join(missing)}",
            )


def _raised_names(node: _Function) -> set[str]:
    """Lists the exception classes a function raises itself.

    Args:
        node: Function definition.

    Returns:
        Final names of the raised classes (``errors.NotFoundError`` gives
        ``NotFoundError``); bare re-raises, raised variables and exceptions
        built by lowercase factories are left out.
    """
    names: set[str] = set()
    for child in _body_nodes(node.body):
        if isinstance(child, ast.Raise) and child.exc is not None:
            name = _final_name(child.exc)
            if name[:1].isupper() and name not in _UNDOCUMENTED_EXCEPTIONS:
                names.add(name)
    return names


@functools.cache
def _project_class_bases() -> dict[str, frozenset[str]]:
    """Indexes the base classes of every class defined in the project.

    Returns:
        Class names mapped to the final names of their bases (merged when
        several files define the same name).
    """
    bases: dict[str, set[str]] = {}
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names = bases.setdefault(node.name, set())
                names.update(_final_name(base) for base in node.bases)
    return {name: frozenset(names) for name, names in bases.items()}


def _exception_ancestors(name: str) -> set[str]:
    """Lists an exception class and its base classes, by final name.

    Args:
        name: Exception class name, such as ``NotFoundError``.

    Returns:
        The name and every ancestor found among the project's classes and
        the built-in exceptions.
    """
    seen: set[str] = set()
    pending = [name]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(_project_class_bases().get(current, ()))
        builtin = getattr(builtins, current, None)
        if isinstance(builtin, type) and issubclass(builtin, BaseException):
            pending.extend(base.__name__ for base in builtin.__mro__[1:])
    return seen


def _check_raises(source: _Source) -> Iterator[_Finding]:
    """Checks that Raises names every exception raised directly (GPY013).

    Names match on their last component, so ``errors.NotFoundError:`` and
    ``NotFoundError:`` both document ``raise errors.NotFoundError(...)``; a
    documented base class (``errors.JosephError:``) covers its subclasses.
    Exceptions that are only propagated may be listed as well.

    Args:
        source: File being checked.

    Yields:
        A finding per function whose Raises section misses an exception.
    """
    for node, parents in _definitions(source.tree):
        if not isinstance(node, _FUNCTIONS) or not _needs_sections(
            node, parents
        ):
            continue
        if _is_test_function(source, node, parents):
            continue
        docstring = ast.get_docstring(node)
        if docstring is None:
            continue
        documented = {
            name.rsplit(".", 1)[-1]
            for name in _sections(docstring).get("Raises", [])
        }
        missing = sorted(
            name
            for name in _raised_names(node)
            if not _exception_ancestors(name) & documented
        )
        if missing:
            yield (
                node.lineno,
                "GPY013",
                f"{node.name}: Raises section must list {', '.join(missing)}",
            )


def _assigned_names(target: ast.expr) -> Iterator[ast.expr]:
    """Flattens an assignment target.

    Args:
        target: Target of an assignment, possibly a tuple or list.

    Yields:
        Every simple target in it.
    """
    if isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _assigned_names(element)
    elif isinstance(target, ast.Starred):
        yield from _assigned_names(target.value)
    else:
        yield target


@dataclasses.dataclass
class _ClassAttributes:
    """Attributes found in one class body.

    Attributes:
        body: Names assigned or annotated directly in the class body.
        instance: Names assigned on the instance (``self.x = ...``).
        properties: Names of properties.
        methods: Names of methods.
    """

    body: set[str] = dataclasses.field(default_factory=set)
    instance: set[str] = dataclasses.field(default_factory=set)
    properties: set[str] = dataclasses.field(default_factory=set)
    methods: set[str] = dataclasses.field(default_factory=set)


def _is_enum(node: ast.ClassDef) -> bool:
    """Checks whether a class derives from an enum base.

    Args:
        node: Class definition.

    Returns:
        True when a base is named like an ``enum`` class.
    """
    return any(
        _final_name(base) in _ENUM_BASES or _final_name(base).endswith("Enum")
        for base in node.bases
    )


def _class_attributes(node: ast.ClassDef) -> _ClassAttributes:
    """Collects the attributes a class defines.

    Args:
        node: Class definition.

    Returns:
        Class-body names, instance names, properties and methods.
    """
    found = _ClassAttributes()
    for statement in node.body:
        if isinstance(statement, ast.AnnAssign):
            annotation = ast.unparse(statement.annotation)
            if isinstance(statement.target, ast.Name) and "InitVar" not in (
                annotation
            ):
                found.body.add(statement.target.id)
        elif isinstance(statement, ast.Assign):
            for target in statement.targets:
                found.body.update(
                    name.id
                    for name in _assigned_names(target)
                    if isinstance(name, ast.Name)
                )
        elif isinstance(statement, _FUNCTIONS):
            decorators = _decorators(statement)
            if decorators & _PROPERTY_DECORATORS or "setter" in decorators:
                found.properties.add(statement.name)
            else:
                found.methods.add(statement.name)
            arguments = [*statement.args.posonlyargs, *statement.args.args]
            if not arguments or decorators & {"staticmethod", "classmethod"}:
                continue
            receiver = arguments[0].arg
            for child in _body_nodes(statement.body):
                targets: list[ast.expr] = []
                if isinstance(child, ast.Assign):
                    targets = list(child.targets)
                elif isinstance(child, ast.AnnAssign):
                    targets = [child.target]
                for target in targets:
                    for name in _assigned_names(target):
                        if (
                            isinstance(name, ast.Attribute)
                            and isinstance(name.value, ast.Name)
                            and name.value.id == receiver
                        ):
                            found.instance.add(name.attr)
    return found


def _check_class_attributes(source: _Source) -> Iterator[_Finding]:
    """Checks a class's Attributes section against its code (GPY014/015).

    Every public attribute (class-body names and ``self.x`` assignments,
    without properties, enum members and ``model_config``) must be listed;
    every listed name must exist. GPY015 is skipped when a base class is
    defined in another module, since it may define the listed name.

    Args:
        source: File being checked.

    Yields:
        A finding per class with missing or unknown attribute entries.
    """
    classes = {
        node.name: node
        for node in ast.walk(source.tree)
        if isinstance(node, ast.ClassDef)
    }
    for node, _ in _definitions(source.tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if source.is_test and node.name.startswith("Test"):
            continue
        docstring = ast.get_docstring(node)
        if docstring is None:
            continue
        found = _class_attributes(node)
        documented = set(_sections(docstring).get("Attributes", []))
        # Enum members are the enum's values, not attributes to list.
        candidates = found.instance | (set() if _is_enum(node) else found.body)
        if any(
            _final_name(base.value if isinstance(base, ast.Subscript) else base)
            in _SCRIPT_ROLES
            for base in node.bases
        ):
            candidates -= _SCRIPT_DECLARATIONS
        public = {
            name
            for name in candidates - found.properties - found.methods
            if not name.startswith("_") and name not in _NOT_ATTRIBUTES
        }
        missing = sorted(public - documented)
        if missing:
            yield (
                node.lineno,
                "GPY014",
                f"{node.name}: Attributes section must list "
                f"{', '.join(missing)}",
            )
        known = found.body | found.instance | found.properties
        resolvable = True
        pending = list(node.bases)
        visited = {node.name}
        while pending:
            base = pending.pop()
            if isinstance(base, ast.Subscript):
                base = base.value
            name = _final_name(base)
            if name in visited:
                continue
            visited.add(name)
            if name in classes:
                inherited = _class_attributes(classes[name])
                known |= inherited.body | inherited.instance
                known |= inherited.properties
                pending.extend(classes[name].bases)
            elif name not in _KNOWN_BASES:
                resolvable = False
        unknown = sorted(documented - known)
        if resolvable and unknown:
            yield (
                node.lineno,
                "GPY015",
                f"{node.name}: Attributes section lists "
                f"{', '.join(unknown)}, which the class does not define",
            )


def _check_docstring_style(source: _Source) -> Iterator[_Finding]:
    """Checks summary wording (GPY016, GPY017, GPY018; pyguide 3.8.3/3.8.4).

    Property docstrings read like attributes; function summaries use one
    mood per file; class summaries do not call the class a class, and
    exception summaries say what the error is, not when it is raised.

    Args:
        source: File being checked.

    Yields:
        A finding per summary that breaks one of these rules.
    """
    moods: dict[str, list[tuple[int, str]]] = {
        "imperative": [],
        "descriptive": [],
    }
    for node, _ in _definitions(source.tree):
        docstring = ast.get_docstring(node)
        if not docstring:
            continue
        word = _summary_word(docstring)
        if isinstance(node, ast.ClassDef):
            summary = docstring.split("\n", 1)[0]
            is_error = node.name.endswith(("Error", "Exception"))
            if _CLASS_SUMMARY_NAMES_CLASS.match(summary):
                yield (
                    node.lineno,
                    "GPY018",
                    f"{node.name}: summary says what the instance represents,"
                    " not that it is a class",
                )
            elif is_error and _RAISE_CONTEXT.match(summary):
                yield (
                    node.lineno,
                    "GPY018",
                    f"{node.name}: summary says what the error is, not when "
                    "it is raised",
                )
            continue
        if _decorators(node) & _PROPERTY_DECORATORS:
            if word in _PROPERTY_VERBS:
                yield (
                    node.lineno,
                    "GPY016",
                    f"{node.name}: property docstring reads like an "
                    "attribute ('The ...'), not 'Returns ...'",
                )
            continue
        words = docstring.split()
        if len(words) > 1 and words[1] in {"of", "for"}:
            continue
        if word in _IMPERATIVE:
            moods["imperative"].append((node.lineno, word))
        elif word in _DESCRIPTIVE:
            moods["descriptive"].append((node.lineno, word))
    imperative, descriptive = moods["imperative"], moods["descriptive"]
    if imperative and descriptive:
        minority, style = (
            (imperative, "descriptive")
            if len(imperative) <= len(descriptive)
            else (descriptive, "imperative")
        )
        for line, word in minority:
            yield (
                line,
                "GPY017",
                f"summary starts with '{word}'; this file's docstrings are "
                f"mostly {style}, keep one style per file",
            )


def _check_comments(source: _Source) -> Iterator[_Finding]:
    """Checks comment format (GPY019 pyguide 3.8.5; GPY049 pyguide 3.19.8).

    Args:
        source: File being checked.

    Yields:
        A finding per comment without a space after ``#`` and per type
        comment.
    """
    for line, comment in source.comments.items():
        if line == 1 and comment.startswith("#!"):
            continue
        if comment != "#" and not comment.startswith("# "):
            yield line, "GPY019", "put one space after '#' in comments"
        if _TYPE_COMMENT.match(comment):
            yield line, "GPY049", "use an annotation, not a '# type:' comment"


def _module_exists(dotted: str) -> bool:
    """Checks whether a dotted name is a module or package.

    Resolves statically first, so no package code runs, then falls back to
    ``importlib`` for modules that only exist at runtime (``os.path``).

    Args:
        dotted: Absolute dotted module path.

    Returns:
        True when ``dotted`` is importable as a module.
    """
    parts = dotted.split(".")
    path: list[str] | None = [*sys.path, *_EXTRA_IMPORT_PATHS]
    for index in range(len(parts)):
        spec = importlib.machinery.PathFinder.find_spec(
            ".".join(parts[: index + 1]), path
        )
        if spec is None:
            break
        if index == len(parts) - 1:
            return True
        path = list(spec.submodule_search_locations or [])
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError):
        return False


def _check_imports(source: _Source) -> Iterator[_Finding]:
    """Checks import style (GPY020, GPY021, GPY022; pyguide 2.2.4).

    Args:
        source: File being checked.

    Yields:
        A finding per ``from x import y`` where ``y`` is not a module, per
        relative import and per ``import x as y`` with a non-standard alias.
    """
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname and (alias.name, alias.asname) not in (
                    _STANDARD_ALIASES
                ):
                    yield (
                        node.lineno,
                        "GPY022",
                        f"import {alias.name} as {alias.asname}: alias only "
                        "with a standard abbreviation",
                    )
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            yield node.lineno, "GPY021", "use an absolute import"
            continue
        module = node.module or ""
        if module in _EXEMPT_FROM_MODULES:
            continue
        for alias in node.names:
            if not _module_exists(f"{module}.{alias.name}"):
                yield (
                    node.lineno,
                    "GPY020",
                    f"from {module} import {alias.name}: import the module, "
                    "not a name from it",
                )


def _free_names(table: symtable.SymbolTable) -> set[str]:
    """Lists the free variables of a scope.

    A name that an inner scope reads from further out is free in every
    scope in between, so the direct symbols are enough.

    Args:
        table: Symbol table of a function or class.

    Returns:
        Names the scope reads from enclosing function scopes.
    """
    return {
        symbol.get_name() for symbol in table.get_symbols() if symbol.is_free()
    }


def _check_nested_scopes(source: _Source) -> Iterator[_Finding]:
    """Checks that nested functions and classes close over a local (GPY031).

    pyguide 2.6: nest only to close over a local value other than ``self``
    or ``cls``; otherwise define a module-level ``_name``. Decorated nested
    functions (route handlers, wrappers) are exempt.

    Args:
        source: File being checked.

    Yields:
        A finding per nested definition that reads no enclosing local.
    """
    decorated = {
        node.lineno
        for node, _ in _definitions(source.tree)
        if node.decorator_list
    }
    stack = [symtable.symtable(source.text, source.relative, "exec")]
    while stack:
        table = stack.pop()
        for child in table.get_children():
            stack.append(child)
            # ``get_type`` returns a str (a StrEnum from Python 3.13).
            if table.get_type() != "function":
                continue
            if child.get_type() not in {"function", "class"} or (
                child.get_name() in _IMPLICIT_SCOPES
            ):
                continue
            if child.get_lineno() in decorated:
                continue
            if not _free_names(child) - {"self", "cls", "__class__"}:
                yield (
                    child.get_lineno(),
                    "GPY031",
                    f"{child.get_name()} is nested but closes over no local "
                    "value; define it at module level with a leading '_'",
                )


def _check_language_rules(source: _Source) -> Iterator[_Finding]:
    """Checks the expression-level language rules.

    GPY030 ``nonlocal`` (pyguide 2.5/2.16), GPY032 comprehensions (2.7),
    GPY033 conditional expressions (2.11), GPY037 power features (2.19),
    GPY042/GPY043 string building (3.10).

    Args:
        source: File being checked.

    Yields:
        A finding per offending node.
    """
    loops: set[int] = set()
    inner_additions: set[int] = set()
    for node in ast.walk(source.tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            loops.update(id(child) for child in _body_nodes(node.body))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            inner_additions.update((id(node.left), id(node.right)))
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Nonlocal):
            yield (
                node.lineno,
                "GPY030",
                "avoid nonlocal state; return values or use an object",
            )
        elif isinstance(
            node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            filters = sum(len(generator.ifs) for generator in node.generators)
            if len(node.generators) > 1 or filters > 1:
                yield (
                    node.lineno,
                    "GPY032",
                    "comprehension has more than one 'for' or 'if'; use a loop",
                )
        elif isinstance(node, ast.IfExp):
            parts = (node.body, node.test, node.orelse)
            if any(part.lineno != part.end_lineno for part in parts):
                yield (
                    node.lineno,
                    "GPY033",
                    "each part of a conditional expression must fit on one "
                    "line; use an if statement",
                )
        elif isinstance(node, ast.ClassDef):
            for keyword in node.keywords:
                if keyword.arg == "metaclass" and (
                    _final_name(keyword.value) not in _ALLOWED_METACLASSES
                ):
                    yield node.lineno, "GPY037", "avoid custom metaclasses"
        elif isinstance(node, _FUNCTIONS) and node.name == "__del__":
            yield node.lineno, "GPY037", "avoid __del__; close resources"
        elif isinstance(node, ast.Call) and _final_name(node) == "__import__":
            yield node.lineno, "GPY037", "avoid __import__; import normally"
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Add)
            and id(node) not in inner_additions
        ):
            yield from _check_string_concatenation(node)
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.op, ast.Add)
            and id(node) in loops
            and _is_string_expression(node.value)
        ):
            yield (
                node.lineno,
                "GPY043",
                "do not accumulate a string with += in a loop; join a list",
            )


def _is_string_expression(node: ast.AST) -> bool:
    """Checks whether an expression is or contains a string literal.

    Args:
        node: Expression.

    Returns:
        True for string constants, f-strings, ``str(...)`` and ``+`` chains
        containing one of those.
    """
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.Call):
        return _final_name(node) == "str"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string_expression(node.left) or _is_string_expression(
            node.right
        )
    return False


def _check_string_concatenation(node: ast.BinOp) -> Iterator[_Finding]:
    """Checks one ``+`` chain for string formatting (GPY042, pyguide 3.10).

    Args:
        node: Outermost ``+`` node of the chain.

    Yields:
        A finding when three or more operands are joined and one is a
        string literal or f-string.
    """
    operands: list[ast.AST] = []
    pending: list[ast.AST] = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, ast.BinOp) and isinstance(current.op, ast.Add):
            pending.extend((current.left, current.right))
        else:
            operands.append(current)
    literals = [
        operand
        for operand in operands
        if isinstance(operand, ast.JoinedStr)
        or (
            isinstance(operand, ast.Constant) and isinstance(operand.value, str)
        )
    ]
    if len(operands) >= 3 and literals and len(literals) < len(operands):
        yield (
            node.lineno,
            "GPY042",
            "build strings with an f-string, % or format(), not +",
        )


def _check_methods(source: _Source) -> Iterator[_Finding]:
    """Checks decorators and properties (pyguide 2.13, 2.17, 3.16.2).

    GPY034 a property pair that only reads and writes one attribute,
    GPY035 ``@staticmethod``, GPY036 ``@classmethod`` that is not a named
    constructor, GPY045 names with a double leading underscore.

    Args:
        source: File being checked.

    Yields:
        A finding per offending method or name.
    """
    for node, _ in _definitions(source.tree):
        if not isinstance(node, ast.ClassDef):
            continue
        setters = {
            statement.name: statement
            for statement in node.body
            if isinstance(statement, _FUNCTIONS)
            and "setter" in _decorators(statement)
        }
        for statement in node.body:
            if not isinstance(statement, _FUNCTIONS):
                continue
            yield from _check_method(node, statement, setters)
        found = _class_attributes(node)
        for name in sorted(found.instance | found.body):
            if name.startswith("__") and not name.endswith("__"):
                yield (
                    node.lineno,
                    "GPY045",
                    f"{node.name}.{name}: use one leading underscore",
                )


def _check_method(
    owner: ast.ClassDef, method: _Function, setters: dict[str, _Function]
) -> Iterator[_Finding]:
    """Checks one method's decorators (GPY034, GPY035, GPY036, GPY045).

    Args:
        owner: Class the method belongs to.
        method: Method definition.
        setters: Property setters of the class by name.

    Yields:
        A finding per rule the method breaks.
    """
    decorators = _decorators(method)
    for decorator in method.decorator_list:
        if _final_name(decorator) == "staticmethod":
            yield (
                decorator.lineno,
                "GPY035",
                f"{method.name}: use a module-level function, not "
                "@staticmethod",
            )
    if "classmethod" in decorators and len(method.decorator_list) == 1:
        returns = ast.unparse(method.returns) if method.returns else ""
        if not re.search(rf"\b(Self|{re.escape(owner.name)})\b", returns):
            yield (
                method.lineno,
                "GPY036",
                f"{method.name}: use @classmethod only for named "
                f"constructors returning {owner.name} or Self",
            )
    if method.name.startswith("__") and not method.name.endswith("__"):
        yield (
            method.lineno,
            "GPY045",
            f"{method.name}: use one leading underscore",
        )
    if decorators & {"property"} and method.name in setters:
        getter_attr = _trivial_getter(method)
        setter_attr = _trivial_setter(setters[method.name])
        if getter_attr is not None and getter_attr == setter_attr:
            yield (
                method.lineno,
                "GPY034",
                f"{method.name}: a property that only gets and sets "
                f"self.{getter_attr} should be a public attribute",
            )


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Drops a leading docstring from a function body.

    Args:
        body: Function body.

    Returns:
        The statements after the docstring.
    """
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _trivial_getter(method: _Function) -> str | None:
    """Finds the attribute a property getter does nothing but return.

    Args:
        method: Getter definition.

    Returns:
        The attribute name for a body of ``return self.<name>``, else None.
    """
    body = _without_docstring(method.body)
    if (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and isinstance(body[0].value, ast.Attribute)
        and isinstance(body[0].value.value, ast.Name)
        and body[0].value.value.id == "self"
    ):
        return body[0].value.attr
    return None


def _trivial_setter(method: _Function) -> str | None:
    """Finds the attribute a property setter does nothing but assign.

    Args:
        method: Setter definition.

    Returns:
        The attribute name for a body of ``self.<name> = <argument>``, else
        None.
    """
    body = _without_docstring(method.body)
    if (
        len(body) == 1
        and isinstance(body[0], ast.Assign)
        and len(body[0].targets) == 1
        and isinstance(body[0].targets[0], ast.Attribute)
        and isinstance(body[0].targets[0].value, ast.Name)
        and body[0].targets[0].value.id == "self"
        and isinstance(body[0].value, ast.Name)
    ):
        return body[0].targets[0].attr
    return None


def _is_main_guard(node: ast.If) -> bool:
    """Checks whether an ``if`` statement is ``if __name__ == "__main__":``.

    Args:
        node: Module-level ``if`` statement.

    Returns:
        True for the main guard, in either operand order.
    """
    if not isinstance(node.test, ast.Compare):
        return False
    names = [node.test.left, *node.test.comparators]
    return {ast.unparse(name) for name in names} in (
        {"__name__", "'__main__'"},
        {"__name__", '"__main__"'},
    )


def _calls_main_only(guard: ast.If) -> bool:
    """Checks that a main guard only hands control to ``main``.

    Args:
        guard: The ``if __name__ == "__main__":`` statement.

    Returns:
        True for ``main()``, ``sys.exit(main())``, ``raise
        SystemExit(main())`` or ``asyncio.run(main())``.
    """
    if len(guard.body) != 1 or guard.orelse:
        return False
    statement = guard.body[0]
    if isinstance(statement, ast.Expr):
        call = statement.value
    elif isinstance(statement, ast.Raise) and statement.exc is not None:
        call = statement.exc
    else:
        return False
    if not isinstance(call, ast.Call):
        return False
    if _final_name(call) == "main":
        return True
    return (
        _final_name(call) in {"exit", "SystemExit", "run"}
        and len(call.args) == 1
        and _final_name(call.args[0]) == "main"
    )


def _check_module_layout(source: _Source) -> Iterator[_Finding]:
    """Checks file-level rules (pyguide 3.2, 3.7, 3.16.3, 3.17).

    GPY040 backslash continuation, GPY041 shebang, GPY044 file name, GPY046
    scripts run ``main()`` behind the main guard, GPY047 module-level calls
    that run on import.

    Args:
        source: File being checked.

    Yields:
        A finding per offending line or statement.
    """
    for number, line in enumerate(source.lines, start=1):
        if (
            line.endswith("\\")
            and number not in source.string_lines
            and number not in source.comments
        ):
            yield (
                number,
                "GPY040",
                "no backslash continuation; use implicit joining in ()",
            )
    guards = [
        node
        for node in source.tree.body
        if isinstance(node, ast.If) and _is_main_guard(node)
    ]
    if source.lines and source.lines[0].startswith("#!"):
        if source.lines[0] not in _SHEBANGS:
            yield 1, "GPY041", "shebang must be '#!/usr/bin/env python3'"
        elif not guards:
            yield 1, "GPY041", "only executable files have a shebang"
    file_name = source.relative.rsplit("/", 1)[-1]
    if not _FILE_NAME.match(file_name):
        yield 1, "GPY044", f"{file_name}: name files lower_with_under.py"
    has_main = any(
        isinstance(node, _FUNCTIONS) and node.name == "main"
        for node in source.tree.body
    )
    for guard in guards:
        if not has_main or not _calls_main_only(guard):
            yield (
                guard.lineno,
                "GPY046",
                "the main guard only calls main(), defined at module level",
            )
    if source.relative.startswith("scripts/") and not guards:
        yield (
            1,
            "GPY046",
            "scripts put their work in main() behind "
            "'if __name__ == \"__main__\":'",
        )
    docstring = ast.get_docstring(source.tree, clean=False)
    for index, node in enumerate(source.tree.body):
        if index == 0 and docstring is not None:
            continue
        if isinstance(node, ast.Expr) and isinstance(
            node.value, (ast.Call, ast.Await)
        ):
            yield (
                node.lineno,
                "GPY047",
                "module-level call runs on import; move it into a function",
            )


def _is_type_expression(node: ast.AST) -> bool:
    """Checks whether an expression is clearly a type, for alias detection.

    Args:
        node: Assigned value.

    Returns:
        True for subscripted generics (``dict[str, int]``,
        ``Callable[...]``) and ``|`` unions containing one or ``None``.
    """
    if isinstance(node, ast.Subscript):
        return _final_name(node.value) in _GENERIC_TYPES
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        sides = (node.left, node.right)
        return any(
            _is_type_expression(side)
            or (isinstance(side, ast.Constant) and side.value is None)
            for side in sides
        )
    return False


def _check_typing(source: _Source) -> Iterator[_Finding]:
    """Checks type alias and type variable names (pyguide 3.19.6, 3.19.10).

    GPY048: aliases are CapWords (``_Private`` when module-only) and are
    declared with ``TypeAlias`` or a ``type`` statement. GPY050: type
    variables are descriptive unless private and unconstrained.

    Args:
        source: File being checked.

    Yields:
        A finding per offending alias or type variable.
    """
    for node in ast.walk(source.tree):
        if isinstance(node, ast.TypeAlias):
            name = ast.unparse(node.name)
            if not _CAP_WORDS.match(name):
                yield node.lineno, "GPY048", f"type alias {name}: use CapWords"
        elif isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name
        ):
            if _final_name(node.annotation) == "TypeAlias" and not (
                _CAP_WORDS.match(node.target.id)
            ):
                yield (
                    node.lineno,
                    "GPY048",
                    f"type alias {node.target.id}: use CapWords",
                )
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if isinstance(node.value, ast.Call) and (
                _final_name(node.value) in _TYPE_VAR_FACTORIES
            ):
                yield from _check_type_var(target.id, node.value)
            elif node in source.tree.body and _is_type_expression(node.value):
                yield (
                    node.lineno,
                    "GPY048",
                    f"{target.id} looks like a type alias; declare it as "
                    f"'{target.id}: TypeAlias = ...'",
                )


def _check_type_var(name: str, node: ast.Call) -> Iterator[_Finding]:
    """Checks one type variable name (GPY050, pyguide 3.19.10).

    Args:
        name: Variable the type variable is assigned to.
        node: The ``TypeVar(...)`` (or ``ParamSpec``) call.

    Yields:
        A finding when the name is a public single letter, or a short name
        for a constrained or bound type variable.
    """
    constrained = len(node.args) > 1 or any(
        keyword.arg == "bound" for keyword in node.keywords
    )
    short = len(name.lstrip("_")) == 1
    if short and (constrained or not name.startswith("_")):
        yield (
            node.lineno,
            "GPY050",
            f"type variable {name}: use a descriptive name, or _{name[-1]} "
            "when private and unconstrained",
        )


_CHECKS = (
    _check_licence,
    _check_missing_docstrings,
    _check_sectionless_docstrings,
    _check_raises,
    _check_class_attributes,
    _check_docstring_style,
    _check_comments,
    _check_imports,
    _check_nested_scopes,
    _check_language_rules,
    _check_methods,
    _check_module_layout,
    _check_typing,
)


def _exceptions(source: _Source) -> tuple[dict[int, set[str]], list[_Finding]]:
    """Reads the inline exceptions of a file and checks their form (GPY001).

    Args:
        source: File being checked.

    Returns:
        A tuple ``(codes_by_line, findings)``: the rule codes each line
        exempts, and a GPY001 finding per exception without codes or reason.
    """
    codes_by_line: dict[int, set[str]] = {}
    findings: list[_Finding] = []
    for line, comment in source.comments.items():
        for match in _NOQA.finditer(comment):
            rest = match["rest"].rstrip()
            valid = _NOQA_WITH_REASON.match(rest)
            if valid is None:
                if rest.startswith(":"):
                    findings.append(
                        (
                            line,
                            "GPY001",
                            "inline exception needs a reason: "
                            "'# noqa: CODE - <reason>'",
                        )
                    )
                continue
            codes = {code.strip() for code in valid["codes"].split(",")}
            codes_by_line.setdefault(line, set()).update(codes)
        ignore = _TYPE_IGNORE.search(comment)
        if ignore and not _TYPE_IGNORE_WITH_REASON.match(
            ignore["rest"].strip()
        ):
            findings.append(
                (
                    line,
                    "GPY001",
                    "type: ignore needs a code and a reason: "
                    "'# type: ignore[code]  # <reason>'",
                )
            )
    return codes_by_line, findings


def check_source(relative: str, text: str) -> list[Violation]:
    """Runs every check on one file's text.

    Args:
        relative: Path relative to the project root, POSIX style; decides
            whether the file is test code or a script.
        text: Source text.

    Returns:
        The violations left after inline exceptions, sorted by line.
    """
    source = _parse(relative, text)
    codes_by_line, findings = _exceptions(source)
    used: set[tuple[int, str]] = set()
    violations = [Violation(relative, *finding) for finding in findings]
    for check in _CHECKS:
        for line, code, message in check(source):
            if code in codes_by_line.get(line, set()):
                used.add((line, code))
            else:
                violations.append(Violation(relative, line, code, message))
    for line, codes in codes_by_line.items():
        for code in sorted(codes):
            if code.startswith("GPY") and (line, code) not in used:
                violations.append(
                    Violation(
                        relative,
                        line,
                        "GPY002",
                        f"inline exception for {code} suppresses nothing",
                    )
                )
    return sorted(set(violations))


@functools.cache
def check_file(path: pathlib.Path) -> tuple[Violation, ...]:
    """Runs every check on one file.

    Args:
        path: Absolute path of a Python file inside the project.

    Returns:
        The file's violations.
    """
    relative = path.relative_to(_PROJECT).as_posix()
    return tuple(check_source(relative, path.read_text(encoding="utf-8")))


def python_files(paths: Sequence[pathlib.Path] = ()) -> list[pathlib.Path]:
    """Lists the Python files to check.

    Args:
        paths: Files or directories to limit the search to; every checked
            root of the project when empty.

    Returns:
        Sorted absolute paths, without virtual environments and caches.
    """
    roots = list(paths) or [_PROJECT / name for name in _ROOT_NAMES]
    files: set[pathlib.Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved.is_file():
            files.add(resolved)
        elif resolved.is_dir():
            files.update(
                path
                for path in resolved.rglob("*.py")
                if not {".venv", "__pycache__"} & set(path.parts)
            )
    return sorted(files)


@pytest.mark.parametrize(
    "path",
    python_files(),
    ids=lambda path: path.relative_to(_PROJECT).as_posix(),
)
def test_file_follows_google_style(path: pathlib.Path) -> None:
    """The file passes every GPY check (codes in the module docstring)."""
    violations = check_file(path)
    assert not violations, "\n" + "\n".join(map(str, violations))


_GOOD_MODULE = '''\
# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""A module that follows every rule."""

from typing import TypeAlias

Pair: TypeAlias = tuple[int, int]


class Point:
    """A point on a grid.

    Attributes:
        x: Column.
        y: Row.
    """

    def __init__(self, x: int, y: int) -> None:
        """Initializes the point.

        Args:
            x: Column.
            y: Row.
        """
        self.x = x
        self.y = y

    @property
    def norm(self) -> int:
        """The Manhattan distance from the origin."""
        return abs(self.x) + abs(self.y)

    def shifted(self, offset: int) -> "Point":
        """Returns a copy moved right.

        Args:
            offset: Columns to move.

        Raises:
            ValueError: The offset is negative.
        """
        if offset < 0:
            raise ValueError(f"negative offset: {offset=}")
        return Point(self.x + offset, self.y)

    @classmethod
    def origin(cls) -> "Point":
        """Returns the origin."""
        return cls(0, 0)
'''

_BAD_SNIPPETS = (
    ("GPY001", "x = 1  # noqa: E501\n"),
    ("GPY002", "x = 1  # noqa: GPY035 - nothing to suppress here\n"),
    (
        "GPY012",
        'def f(a: int) -> int:\n    """Adds one."""\n    return a + 1\n',
    ),
    (
        "GPY013",
        'def f() -> None:\n    """Fails.\n\n    Returns:\n        Nothing.\n'
        '    """\n    raise errors.BadError()\n',
    ),
    (
        "GPY014",
        'class C:\n    """A thing."""\n\n    size: int\n',
    ),
    (
        "GPY015",
        'class C:\n    """A thing.\n\n    Attributes:\n        size: Size.\n'
        '    """\n',
    ),
    (
        "GPY016",
        'class C:\n    """A thing."""\n\n    @property\n    def size(self) '
        '-> int:\n        """Returns the size."""\n        return 1\n',
    ),
    (
        "GPY017",
        'def f() -> None:\n    """Builds a."""\n\n\ndef g() -> None:\n    '
        '"""Builds b."""\n\n\ndef h() -> None:\n    """Build c."""\n',
    ),
    ("GPY018", 'class C:\n    """Class that holds data."""\n'),
    ("GPY019", "x = 1  #comment\n"),
    ("GPY020", "from os.path import join\n"),
    ("GPY021", "from . import sibling\n"),
    ("GPY022", "import json as j\n"),
    (
        "GPY030",
        'def f() -> None:\n    """Outer."""\n    n = 0\n\n    def g() -> '
        'None:\n        """Inner."""\n        nonlocal n\n        n += 1\n'
        "\n    g()\n",
    ),
    (
        "GPY031",
        'def f() -> None:\n    """Outer."""\n\n    def g() -> None:\n        '
        '"""Inner."""\n\n    g()\n',
    ),
    ("GPY032", "x = [a for a in range(3) for b in range(3)]\n"),
    ("GPY033", "x = (1 if f(\n    a) else 2)\n"),
    (
        "GPY034",
        'class C:\n    """A thing."""\n\n    @property\n    def size(self) -> '
        'int:\n        """The size."""\n        return self._size\n\n    '
        "@size.setter\n    def size(self, value: int) -> None:\n        "
        '"""Sets the size."""\n        self._size = value\n',
    ),
    (
        "GPY035",
        'class C:\n    """A thing."""\n\n    @staticmethod\n    def f() -> '
        'None:\n        """Does nothing."""\n',
    ),
    (
        "GPY036",
        'class C:\n    """A thing."""\n\n    @classmethod\n    def f(cls) -> '
        'int:\n        """Returns one."""\n        return 1\n',
    ),
    ("GPY037", 'class C(metaclass=Meta):\n    """A thing."""\n'),
    ("GPY040", "x = 1 + \\\n    2\n"),
    ("GPY041", "#!/usr/bin/python\n"),
    ("GPY042", "x = 'a' + name + 'b'\n"),
    (
        "GPY043",
        "text = ''\nfor item in items:\n    text += 'x'\n",
    ),
    ("GPY045", 'class C:\n    """A thing."""\n\n    __hidden = 1\n'),
    ("GPY046", 'if __name__ == "__main__":\n    print(1)\n'),
    ("GPY047", "print(1)\n"),
    ("GPY048", "Pairs = list[tuple[int, int]]\n"),
    ("GPY049", "x = []  # type: list[int]\n"),
    ("GPY050", "T = TypeVar('T')\n"),
)


def test_rules_accept_a_clean_module() -> None:
    """A module that follows every rule has no violations."""
    assert check_source("src/pkg/clean.py", _GOOD_MODULE) == []


@pytest.mark.parametrize(
    ("code", "snippet"), _BAD_SNIPPETS, ids=[code for code, _ in _BAD_SNIPPETS]
)
def test_rule_reports_its_violation(code: str, snippet: str) -> None:
    """Each rule reports a snippet that breaks it."""
    shebang, _, body = snippet.rpartition("#!/usr/bin/python\n")
    text = (
        f"{shebang}{_}# Copyright 2026 apoman123\n"
        '# SPDX-License-Identifier: MIT\n\n"""Snippet."""\n\n'
        f"{body}"
    )
    codes = {violation.code for violation in check_source("src/m.py", text)}
    assert code in codes


def test_inline_exception_with_reason_suppresses() -> None:
    """A '# noqa: CODE - reason' comment exempts its line."""
    text = (
        "# Copyright 2026 apoman123\n# SPDX-License-Identifier: MIT\n\n"
        '"""Snippet."""\n\nimport json as j  # noqa: GPY022 - test alias.\n'
    )
    assert check_source("src/m.py", text) == []


def main(argv: Sequence[str] = ()) -> int:
    """Prints the violations of some paths, one per line.

    Args:
        argv: Files or directories, relative to the working directory;
            the whole project when empty.

    Returns:
        Process exit code: 1 when there are violations, else 0.
    """
    violations: list[Violation] = []
    for path in python_files([pathlib.Path(arg) for arg in argv]):
        violations.extend(check_file(path))
    for violation in violations:
        sys.stdout.write(f"{violation}\n")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
