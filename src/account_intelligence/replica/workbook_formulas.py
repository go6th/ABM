"""The values of a delivered workbook's formula cells, computed in code.

The export builder writes the Summary matrix, the People counts and the Platform Ranking ranks
as formulas (``exports.excel.common.Formula``) and stores no cached value, so a workbook read
back for the web tabs (``replica.file_tabs``) would show those cells empty. This evaluates the
small formula language the builder writes: cell and range references (``A1``, ``$B$2:$B$40``,
``'Jobs'!C2:C9``), numbers, quoted text, ``TRUE`` and ``FALSE``, ``+ - * / &``, comparisons, and
``COUNTA``, ``COUNTIF``, ``COUNTIFS``, ``SUM``, ``IF``, ``AND`` and ``OR``, with Excel's rules:

* criteria: a leading comparison sign, ``*`` and ``?`` wildcards, ``~`` escapes, case-insensitive
  text; a number criterion matches numbers (and the same number written as text), never TRUE;
* comparisons: numbers sort before text and text before TRUE and FALSE, a number never equals
  text, a blank cell is 0, empty text or FALSE beside the other side;
* ``IF`` computes only the branch it picks, so ``IF(B1=0,0,A1/B1)`` is 0 when B1 is 0;
* ``COUNTA`` counts every formula cell (its result may be empty text), ``SUM`` over a range adds
  only its numbers, a formula that is only a reference to a blank cell shows 0.

Anything else (an unknown function, a malformed formula, a reference cycle, an error such as a
division by zero, a cell that depends on a cell that cannot be computed) evaluates to None, and
the cell is shown empty rather than wrong. Pure: no I/O.

Bounded: a formula is at most ``MAX_FORMULA`` characters and ``MAX_NEST`` levels deep; a range
is read only as far as the sheet has cells (the cells past them are blank); each range's values,
and the cells meeting each criterion, are worked out once per workbook; a long chain of formulas
that refer to one another is computed in steps of ``MAX_DEPTH`` cells, so it never reaches
Python's recursion limit.

GUARANTEE(test_the_web_tabs_compute_the_builders_formulas)
GUARANTEE(test_formulas_follow_excel_on_the_edges)
GUARANTEE(test_a_long_formula_chain_and_huge_ranges_stay_bounded)
"""

from __future__ import annotations

import math
import operator
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeGuard

from openpyxl.utils import column_index_from_string

Value = str | int | float | bool | None
# (sheet, row, column) -> the raw cell value (a formula is text starting with "=")
Grid = Mapping[tuple[str, int, int], Any]
Key = tuple[str, int, int]

_TOKEN = re.compile(
    r"""\s*(?:
      (?P<str>"(?:[^"]|"")*")
    | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)?
        \$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)(?![A-Za-z0-9_(])
    | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<fn>[A-Z][A-Z0-9.]*)\s*\(
    | (?P<bool>TRUE|FALSE)(?![A-Za-z0-9_.(])
    | (?P<op><>|<=|>=|[-+*/&=<>(),])
    )""",
    re.VERBOSE,
)
_CELL = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")
# a number as Excel reads one from text (never "nan", "inf" or "1_000")
_NUMBER_TEXT = re.compile(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*")
MAX_FORMULA = 8192  # Excel's own limit on a formula's length
MAX_NEST = 40  # levels of one formula's expression (parentheses, operators, calls)
MAX_DEPTH = 8  # formula cells computed inside one another before a step restarts
MAX_CHAIN = 100_000  # formula cells on one chain of references
_STEPS = 2 * (MAX_CHAIN // MAX_DEPTH) + 2
_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
    "=": operator.eq,
    "<>": operator.ne,
}


class FormulaError(ValueError):
    """A formula outside the supported language, or one Excel shows as an error."""


class _Cycle(Exception):
    """A formula that refers back to itself (directly or through other cells)."""


class _TooDeep(Exception):
    """A chain of references deeper than one step; ``key`` is the cell to compute first."""

    def __init__(self, key: Key) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True)
class Range:
    """A rectangular range on one sheet."""

    sheet: str
    top: int
    left: int
    bottom: int
    right: int

    @property
    def size(self) -> int:
        """The cells the range covers."""
        return max(0, self.bottom - self.top + 1) * max(0, self.right - self.left + 1)

    def cells(self) -> Iterator[Key]:
        """Every cell address, row by row."""
        for r in range(self.top, self.bottom + 1):
            for c in range(self.left, self.right + 1):
                yield (self.sheet, r, c)


@dataclass(frozen=True)
class _Node:
    """One node of a parsed formula: a literal, a reference, an operation or a call."""

    kind: str  # lit, ref, neg, op, call
    value: Any = None  # the literal, the Range, the operator or the function name
    args: tuple[_Node, ...] = ()
    depth: int = 1


def _make(kind: str, value: Any = None, args: tuple[_Node, ...] = ()) -> _Node:
    depth = 1 + max((a.depth for a in args), default=0)
    if depth > MAX_NEST:
        raise FormulaError("formula nested too deeply")
    return _Node(kind, value, args, depth)


def _tokens(text: str) -> list[tuple[str, str]]:
    if len(text) > MAX_FORMULA:
        raise FormulaError("formula too long")
    out: list[tuple[str, str]] = []
    at = 0
    # LOOP(formula tokens): cap=the formula's characters (MAX_FORMULA); exit=end of text
    while at < len(text):
        if text[at:].strip() == "":
            break
        m = _TOKEN.match(text, at)
        if m is None or m.end() == at:
            raise FormulaError(f"cannot read {text[at : at + 20]!r}")
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
        at = m.end()
    return out


def _range(ref: str, sheet: str) -> Range:
    if "!" in ref:
        name, ref = ref.rsplit("!", 1)
        sheet = name[1:-1].replace("''", "'") if name.startswith("'") else name
    corners = [_CELL.fullmatch(p) for p in ref.split(":")]
    if not all(corners):
        raise FormulaError(f"bad reference {ref!r}")
    rows = [int(m.group(2)) for m in corners if m]
    cols = [column_index_from_string(m.group(1)) for m in corners if m]
    if min(rows) < 1 or max(rows) > 1_048_576 or max(cols) > 16_384:
        raise FormulaError(f"reference outside a sheet {ref!r}")
    return Range(sheet, min(rows), min(cols), max(rows), max(cols))


@lru_cache(maxsize=2048)
def parse(text: str, sheet: str) -> _Node:
    """A formula (without its ``=``) as a tree; ``sheet`` is the sheet its references default to.
    FormulaError for anything outside the language."""
    return _Parser(_tokens(text), sheet).expr()


def _number(value: Value) -> float | None:
    """A number cell's value (TRUE and FALSE are not numbers here)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _number_text(text: str) -> float | None:
    """Text that Excel reads as a number ("5", " 2.5 ", "1e3"), else None."""
    return float(text) if _NUMBER_TEXT.fullmatch(text) else None


def _wild(pattern: str) -> re.Pattern[str]:
    """An Excel text criterion as a whole-text, case-insensitive pattern."""
    out = []
    i = 0
    # LOOP(criterion characters): cap=the pattern's characters; exit=end of pattern
    while i < len(pattern):
        ch = pattern[i]
        if ch == "~" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
        i += 1
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def matcher(criterion: Value) -> Callable[[Value], bool]:
    """Excel's COUNTIF test for one criterion (FormulaError for an empty criterion cell)."""
    if criterion is None:
        raise FormulaError("an empty criterion")
    if isinstance(criterion, bool):
        return lambda v: isinstance(v, bool) and v == criterion
    if not isinstance(criterion, str):
        criterion = _text(criterion)
    op = next((o for o in ("<>", "<=", ">=", "=", "<", ">") if criterion.startswith(o)), "")
    operand = criterion[len(op) :]
    target = _number_text(operand)
    compare = _COMPARE[op or "="]
    if target is not None:
        # a number criterion ("5", ">=5"): numbers compare as numbers; "5" also matches the text 5
        def numeric(v: Value) -> bool:
            n = _number(v)
            if n is None and op in ("", "=") and isinstance(v, str):
                return v.strip() == operand.strip()
            return compare(n, target) if n is not None else op == "<>"

        return numeric
    if operand == "":
        # "" counts blank cells and empty text, "=" only blank cells, "<>" every other cell
        if op == "<>":
            return lambda v: v is not None
        if op == "=":
            return lambda v: v is None
        if op:
            return lambda v: False
        return lambda v: v is None or v == ""
    if op in ("<", ">", "<=", ">="):
        # text compares with text, case-insensitively, without wildcards
        folded = operand.lower()
        return lambda v: isinstance(v, str) and compare(v.lower(), folded)
    pattern = _wild(operand)
    if op == "<>":
        return lambda v: not (isinstance(v, str) and pattern.fullmatch(v) is not None)
    return lambda v: isinstance(v, str) and pattern.fullmatch(v) is not None


def _plain_text(criterion: Value) -> str | None:
    """The text of a criterion that only matches equal text (no sign, number or wildcard)."""
    if not isinstance(criterion, str) or criterion == "" or criterion[:1] in "<>=":
        return None
    if any(ch in criterion for ch in "*?~") or _number_text(criterion) is not None:
        return None
    return criterion.lower()


class Evaluator:
    """Computes formula cells over one workbook's raw cells (memoised; cycles give None)."""

    def __init__(self, grid: Grid, sheets: Iterable[str] = ()) -> None:
        self.grid = grid
        self.memo: dict[Key, Value] = {}
        self.busy: dict[Key, None] = {}
        # each sheet's last row and column with a cell (a range is read no further); ``sheets``
        # names the workbook's sheets, the empty ones included
        self.extent: dict[str, tuple[int, int]] = dict.fromkeys(sheets, (0, 0))
        for sheet, r, c in grid:
            rows, cols = self.extent.get(sheet, (0, 0))
            self.extent[sheet] = (max(rows, r), max(cols, c))
        self._ranges: dict[Range, tuple[Value, ...]] = {}
        self._texts: dict[Range, Counter[str]] = {}
        self._masks: dict[tuple[Range, str, Value], int] = {}

    # ---- cells ---------------------------------------------------------------------------

    def value(self, sheet: str, row: int, col: int) -> Value:
        """The cell's value: its literal, or its formula's result (None when unsupported)."""
        key = (sheet, row, col)
        raw = self.grid.get(key)
        if not _is_formula(raw):
            return raw
        if key in self.memo:
            return self.memo[key]
        if self.busy:
            return self._compute(key, raw)
        # a chain deeper than one step is computed from its far end, a step at a time
        pending, waiting = [key], {key}
        for _ in range(_STEPS):
            k = pending[-1]
            try:
                self._compute(k, self.grid[k])
            except _TooDeep as deep:
                if deep.key in waiting:
                    break  # a cycle longer than one step
                pending.append(deep.key)
                waiting.add(deep.key)
                continue
            except _Cycle:
                pass  # every cell on it shows empty (memoised by _compute)
            pending.pop()
            if not pending:
                return self.memo.get(key)
        for k in pending:
            self.memo[k] = None
        return None

    def _compute(self, key: Key, raw: str) -> Value:
        if key in self.memo:
            return self.memo[key]
        if key in self.busy:
            raise _Cycle(key)
        if len(self.busy) >= MAX_DEPTH:
            raise _TooDeep(key)
        self.busy[key] = None
        out: Value
        try:
            node = parse(raw[1:], key[0])
            result = self._eval(node)
            if isinstance(result, Range):
                result = self._single(result)
            # a formula that is a reference to a blank cell shows 0, as in Excel
            out = 0 if result is None else result
        except _Cycle:
            # every cell on a reference cycle shows empty, not a number computed from a blank
            self.memo[key] = None
            raise
        except _TooDeep:
            raise  # computed again once the far end of the chain is known
        except (ArithmeticError, ValueError, TypeError, IndexError, KeyError, RecursionError):
            out = None
        finally:
            self.busy.pop(key, None)
        self.memo[key] = out
        return out

    def _cell(self, key: Key) -> Value:
        """A cell's value inside a formula; FormulaError when it is a formula that cannot be
        computed (the dependent cell is then unknown too, never computed from a blank)."""
        raw = self.grid.get(key)
        if not _is_formula(raw):
            return raw
        out = self._compute(key, raw)
        if out is None:
            raise FormulaError(f"depends on {key!r}, which cannot be computed")
        return out

    def _clip(self, rng: Range) -> Range:
        """The part of a range the sheet has cells in (the rest is blank)."""
        if rng.sheet not in self.extent:
            raise FormulaError(f"no sheet {rng.sheet!r}")
        rows, cols = self.extent[rng.sheet]
        return Range(rng.sheet, rng.top, rng.left, min(rng.bottom, rows), min(rng.right, cols))

    def range_values(self, rng: Range) -> tuple[Value, ...]:
        """The values of every cell of a range, row by row, read once per workbook."""
        if rng not in self._ranges:
            self._ranges[rng] = tuple(self._cell(k) for k in rng.cells())
        return self._ranges[rng]

    def values(self, rng: Range) -> Iterator[Value]:
        """The values of the cells of a range the sheet has cells in."""
        yield from self.range_values(self._clip(rng))

    def _single(self, rng: Range) -> Value:
        if rng.size != 1:
            raise FormulaError("a range where one value was expected")
        return self._cell((rng.sheet, rng.top, rng.left))

    # ---- expressions ---------------------------------------------------------------------

    def _eval(self, node: _Node) -> Value | Range:
        kind = node.kind
        if kind == "lit":
            return node.value  # type: ignore[no-any-return]
        if kind == "ref":
            rng: Range = node.value
            if rng.sheet not in self.extent:
                raise FormulaError(f"no sheet {rng.sheet!r}")
            return rng
        if kind == "neg":
            return _tidy(-_num(self._scalar(node.args[0])))
        if kind == "call":
            return self._call(node.value, node.args)
        op = node.value
        a, b = self._scalar(node.args[0]), self._scalar(node.args[1])
        if op == "&":
            return f"{_text(a)}{_text(b)}"
        if op in _COMPARE:
            return _compare(op, a, b)
        x, y = _num(a), _num(b)
        if op == "+":
            return _tidy(x + y)
        if op == "-":
            return _tidy(x - y)
        if op == "*":
            return _tidy(x * y)
        return _tidy(x / y)  # ZeroDivisionError: Excel's #DIV/0!

    def _scalar(self, node: _Node) -> Value:
        out = self._eval(node)
        return self._single(out) if isinstance(out, Range) else out

    def _call(self, name: str, args: tuple[_Node, ...]) -> Value:
        if name == "IF":
            if len(args) not in (2, 3):
                raise FormulaError("IF takes two or three arguments")
            # only the branch picked is computed
            if _truth(self._scalar(args[0])):
                return self._scalar(args[1])
            return self._scalar(args[2]) if len(args) == 3 else False
        if name == "COUNTA":
            return sum(self._counta(a) for a in args)
        if name == "SUM":
            total = 0.0
            for a in args:
                got = self._eval(a)
                if isinstance(got, Range):
                    total += sum(n for v in self.values(got) if (n := _number(v)) is not None)
                else:
                    total += _num(got) if got is not None else 0.0
            return _tidy(total)
        if name == "COUNTIF":
            if len(args) != 2:
                raise FormulaError("COUNTIF takes two arguments")
            return self._countifs([(self._range_arg(args[0]), self._scalar(args[1]))])
        if name == "COUNTIFS":
            if len(args) < 2 or len(args) % 2:
                raise FormulaError("COUNTIFS takes pairs")
            pairs = [
                (self._range_arg(args[k]), self._scalar(args[k + 1]))
                for k in range(0, len(args), 2)
            ]
            return self._countifs(pairs)
        if name in ("AND", "OR"):
            flags: list[bool] = []
            for a in args:
                got = self._eval(a)
                if isinstance(got, Range):
                    # a range's text and blank cells are passed over
                    flags += [bool(v) for v in self.values(got) if isinstance(v, int | float)]
                elif got is not None:
                    flags.append(_truth(got))
            if not flags:
                raise FormulaError(f"{name} without a logical value")
            return all(flags) if name == "AND" else any(flags)
        raise FormulaError(f"unsupported function {name}")

    def _counta(self, arg: _Node) -> int:
        got = self._eval(arg)
        if not isinstance(got, Range):
            return 1  # a value, empty text included, counts
        clipped = self._clip(got)
        # every formula cell counts, whatever its result; a literal counts unless it is empty
        return sum(1 for k in clipped.cells() if self.grid.get(k) not in (None, ""))

    def _range_arg(self, arg: _Node) -> Range:
        got = self._eval(arg)
        if not isinstance(got, Range):
            raise FormulaError("a value where a range was expected")
        return got

    def _countifs(self, pairs: list[tuple[Range, Value]]) -> int:
        shapes = {(r.bottom - r.top, r.right - r.left) for r, _ in pairs}
        if len(shapes) != 1:
            raise FormulaError("COUNTIFS ranges differ in size")
        if len(pairs) == 1 and (plain := _plain_text(pairs[0][1])) is not None:
            # an exact text criterion: counted from the range's text tally (read once)
            return self._tally(self._clip(pairs[0][0]))[plain]
        # the cells every range has, aligned; the cells past them are blank in every range
        clipped = [self._clip(r) for r, _ in pairs]
        height = max(c.bottom - c.top + 1 for c in clipped)
        width = max(c.right - c.left + 1 for c in clipped)
        height = max(0, min(height, pairs[0][0].bottom - pairs[0][0].top + 1))
        width = max(0, min(width, pairs[0][0].right - pairs[0][0].left + 1))
        hits = -1  # every cell
        for r, c in pairs:
            aligned = Range(r.sheet, r.top, r.left, r.top + height - 1, r.left + width - 1)
            hits &= self._hits(aligned, c)
        count = (hits & ((1 << (height * width)) - 1)).bit_count()
        blank = pairs[0][0].size - height * width
        if blank and all(matcher(c)(None) for _, c in pairs):
            count += blank
        return count

    def _hits(self, rng: Range, criterion: Value) -> int:
        """The cells of a range that meet a criterion, as the bits of a number (cell i is bit
        i), worked out once per range and criterion."""
        key = (rng, type(criterion).__name__, criterion)
        if key not in self._masks:
            test = matcher(criterion)
            bits = "".join("1" if test(v) else "0" for v in reversed(self.range_values(rng)))
            self._masks[key] = int(bits or "0", 2)
        return self._masks[key]

    def _tally(self, rng: Range) -> Counter[str]:
        if rng not in self._texts:
            self._texts[rng] = Counter(
                v.lower() for v in self.range_values(rng) if isinstance(v, str)
            )
        return self._texts[rng]


class _Parser:
    """Recursive descent over one formula's tokens, to a tree of ``_Node``."""

    def __init__(self, tokens: list[tuple[str, str]], sheet: str) -> None:
        self.t = tokens
        self.i = 0
        self.sheet = sheet
        self.nest = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def _take(self, text: str | None = None) -> tuple[str, str]:
        tok = self._peek()
        if tok is None or (text is not None and tok[1] != text):
            raise FormulaError(f"expected {text!r}")
        self.i += 1
        return tok

    def _is_op(self, *ops: str) -> str | None:
        tok = self._peek()
        return tok[1] if tok is not None and tok[0] == "op" and tok[1] in ops else None

    def expr(self) -> _Node:
        out = self._comparison()
        if self._peek() is not None:
            raise FormulaError("trailing tokens")
        return out

    def _comparison(self) -> _Node:
        self.nest += 1
        if self.nest > MAX_NEST:
            raise FormulaError("formula nested too deeply")
        try:
            left = self._concat()
            # LOOP(comparisons): cap=the formula's tokens; exit=no comparison sign
            while op := self._is_op("=", "<>", "<", ">", "<=", ">="):
                self._take()
                left = _make("op", op, (left, self._concat()))
            return left
        finally:
            self.nest -= 1

    def _concat(self) -> _Node:
        left = self._additive()
        # LOOP(text joins): cap=the formula's tokens; exit=no ampersand
        while self._is_op("&"):
            self._take()
            left = _make("op", "&", (left, self._additive()))
        return left

    def _additive(self) -> _Node:
        left = self._term()
        # LOOP(sums): cap=the formula's tokens; exit=no plus or minus
        while op := self._is_op("+", "-"):
            self._take()
            left = _make("op", op, (left, self._term()))
        return left

    def _term(self) -> _Node:
        left = self._unary()
        # LOOP(products): cap=the formula's tokens; exit=no times or divide
        while op := self._is_op("*", "/"):
            self._take()
            left = _make("op", op, (left, self._unary()))
        return left

    def _unary(self) -> _Node:
        signs = 0
        # LOOP(leading signs): cap=the formula's tokens; exit=no plus or minus
        while op := self._is_op("-", "+"):
            self._take()
            signs += op == "-"
        out = self._primary()
        return _make("neg", None, (out,)) if signs % 2 else out

    def _primary(self) -> _Node:
        kind, text = self._take()
        if kind == "num":
            return _make("lit", _tidy(float(text)))
        if kind == "str":
            return _make("lit", text[1:-1].replace('""', '"'))
        if kind == "bool":
            return _make("lit", text == "TRUE")
        if kind == "ref":
            return _make("ref", _range(text, self.sheet))
        if kind == "fn":
            return _make("call", text, self._args())
        if text == "(":
            out = self._comparison()
            self._take(")")
            return out
        raise FormulaError(f"unexpected {text!r}")

    def _args(self) -> tuple[_Node, ...]:
        args: list[_Node] = []
        if self._is_op(")"):
            self._take()
            return ()
        # LOOP(call arguments): cap=the formula's tokens (_take raises at the end); exit=")"
        while True:
            args.append(self._comparison())
            sep = self._take()[1]
            if sep == ")":
                return tuple(args)
            if sep != ",":
                raise FormulaError(f"unexpected {sep!r}")


def _is_formula(raw: Any) -> TypeGuard[str]:
    return isinstance(raw, str) and raw.startswith("=")


def _rank(value: Value) -> int:
    """Excel's order of kinds in a comparison: numbers, then text, then TRUE and FALSE."""
    return 2 if isinstance(value, bool) else 1 if isinstance(value, str) else 0


def _compare(op: str, a: Value, b: Value) -> bool:
    """A comparison as Excel makes it (a blank is 0, empty text or FALSE beside the other)."""
    blank_as: dict[int, Value] = {0: 0, 1: "", 2: False}
    if a is None and b is None:
        a = b = 0
    elif a is None:
        a = blank_as[_rank(b)]
    elif b is None:
        b = blank_as[_rank(a)]
    ra, rb = _rank(a), _rank(b)
    if ra != rb:
        return _COMPARE[op](ra, rb)
    if ra == 1:
        return _COMPARE[op](str(a).lower(), str(b).lower())
    return _COMPARE[op](a, b)


def _num(value: Value) -> float:
    """A value in arithmetic: a blank is 0, TRUE is 1, number text is its number."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    n = _number_text(value)
    if n is None:
        raise FormulaError("text where a number was expected")  # Excel's #VALUE!
    return n


def _truth(value: Value) -> bool:
    if isinstance(value, str):
        word = value.strip().upper()
        if word not in ("TRUE", "FALSE"):
            raise FormulaError("text where TRUE or FALSE was expected")  # Excel's #VALUE!
        return word == "TRUE"
    return bool(value)


def _text(value: Value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:.15g}"  # Excel shows 15 significant digits: 0.1+0.2 is 0.3
    return str(value)


def _tidy(value: float) -> int | float:
    """A whole number as an int (Excel shows 3, not 3.0); an overflow is Excel's #NUM!."""
    if not math.isfinite(value):
        raise FormulaError("number out of range")
    return int(value) if float(value).is_integer() else value
