"""구조 규칙 검사기 (spec §3의 R1~R11). test_architecture.py가 이것을 부른다.

파일을 실행하지 않고 AST로 읽는다. 검사 범위는 넘겨받은 research_desk/ 폴더 안뿐이다.
그 안에서도 `__pycache__`와 점(.)으로 시작하는 폴더는 건너뛴다.

**칸 판정** (research_desk/ 기준 경로)
- `core/`, `domain/`, `collector/`, `tagger/`, `web/` 아래 → 그 칸
- `features/<기능>/` 폴더 아래 → `features/<기능>`
- `cli.py`, `__main__.py` → 입구
- 그 밖(`__init__.py`, `features/__init__.py`, 칸 밖에 놓인 파일) → 패키지 뿌리
- 경로에 `tests/` 폴더가 있거나 이름이 `conftest.py`인 파일은 테스트다. R10만 적용한다.

**import 해석**
- `import a.b` → `a.b`
- `from a.b import c` → `a.b.c`가 실제 모듈(파일·폴더)이면 `a.b.c`, 아니면 `a.b`(c는 a.b 안의 이름).
  `research_desk`·`research_desk.features` 바로 아래 이름은 아직 없는 칸이어도 그 칸으로 본다.
- 상대 import는 파이썬과 같은 방법으로 절대 이름으로 푼다.
- 함수 안·`TYPE_CHECKING` 안의 import도 import다. `importlib` 같은 동적 import는 보지 않는다.

**규칙** — 위반마다 "파일:줄 — 규칙 이름: 설명"
- R1~R5·R7: 칸마다 import해도 되는 research_desk 모듈. 입구 모듈(cli·__main__) import는 R8로만 보고한다.
- 공개 창구(R5·R7·R8): 자기 기능 밖에서는 기능을 `research_desk.features.<기능>` 자체나 거기서 가져온
  이름으로만 쓴다. `from research_desk.features.<기능> import x`에서 x가 하위 모듈 이름이면, 그 기능의
  `__init__.py` 최상위가 x를 직접 묶을 때만(예: `from .router import router`, `from . import store`)
  공개 이름으로 본다. `import *`, `__all__` 목록, 하위 모듈 import의 부수효과는 묶은 것으로 치지 않는다.
- R6: 기능 → 다른 기능 import(테스트 제외)로 그래프를 만들고 순환마다 한 줄 보고한다.
- R8: 입구만 모든 칸(하위 모듈 포함)을 import한다. 단 기능은 입구도 공개 창구로만 쓴다.
  다른 파일은 cli·__main__을 import하지 않는다.
  패키지 뿌리 파일은 research_desk 모듈을 import하지 않는다(여러 칸을 묶는 곳은 입구뿐).
- R9: 외부 도구는 정해진 칸에서만. R10: 옛 코드 금지(테스트 포함).
- R11: `.table('<표>')` 호출(supabase-py의 별칭 `.from_('<표>')` 포함)과, docstring이 아닌 문자열의 FROM/UPDATE/INTO/JOIN <표>
  (대소문자 무시, 단어 경계, `public.`·큰따옴표 허용)는 주인 칸에만 있어야 한다.
  docstring = 모듈·클래스·함수 본문의 첫 문장인 문자열.
"""
from __future__ import annotations

import ast
import os
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

PACKAGE = "research_desk"
FEATURES = "features"

RULE_NAMES = {
    0: "구문 오류",
    1: "R1 core",
    2: "R2 domain",
    3: "R3 collector",
    4: "R4 tagger",
    5: "R5 기능",
    6: "R6 순환 금지",
    7: "R7 web",
    8: "R8 입구",
    9: "R9 외부 도구",
    10: "R10 옛 코드",
    11: "R11 표 주인",
}

ROOT = "패키지 뿌리"
ENTRY = "입구"
SIMPLE_AREAS = ("core", "domain", "collector", "tagger", "web")
ENTRY_MODULES = ("cli", "__main__")

# R1~R4: 칸 → (규칙 번호, import해도 되는 칸, 설명)
ALLOWED_AREAS = {
    "core": (1, ("core",), "core는 research_desk.core 밖을 import하지 않는다"),
    "domain": (2, ("core", "domain"), "domain은 core·domain만 import한다"),
    "collector": (3, ("core", "domain", "collector"), "collector는 core·domain·collector만 import한다"),
    "tagger": (4, ("core", "domain", "tagger"), "tagger는 core·domain·tagger만 import한다"),
}

# R9: 외부 도구(최상위 모듈 이름) → 쓸 수 있는 칸
EXTERNAL_TOOL_AREAS = {
    "supabase": "core",
    "asyncpg": "core",
    "openai": "core",
    "anthropic": "core",
    "dotenv": "core",
    "fitz": "core",
    "pymupdf": "core",
    "pymongo": "core",  # core.mongo
    "bson": "core",  # pymongo의 BSON 패키지
    "httpx": "core",  # core.kis (KIS Open API)
    "telethon": "collector",
    "langgraph": "tagger",
}

# R10: 옛 코드(최상위 모듈 이름)
OLD_MODULES = frozenset({"langgraph_tagger", "collector", "config", "storage", "telegram_client", "main"})

# R11: 표 → 주인 칸 (spec §4)
TABLE_OWNERS = {
    "reports": ("collector", "tagger", "features/review", "features/reports"),
    "failed_attempts": ("collector",),
    "report_summaries": ("features/analysis",),
    # 주가 스냅숏과 갱신 실행 기록
    "stock_price_snapshot": ("features/prices",),
    "price_update_runs": ("features/prices",),
    # 회사 프로필·사업 부문·임베딩과 유사 기업 계산 기록
    "company_profiles": ("features/peers",),
    "company_segments": ("features/peers",),
    "company_embeddings": ("features/peers",),
    "segment_embeddings": ("features/peers",),
    "peer_builds": ("features/peers",),
}
# supabase-py opens a table with client.table('<표>') or its alias client.from_('<표>').
_TABLE_METHODS = ("table", "from_")
_SQL_TABLE = re.compile(
    r'\b(?:FROM|UPDATE|INTO|JOIN)\s+(?:"?public"?\s*\.\s*)?"?'
    r"(" + "|".join(TABLE_OWNERS) + r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class Violation:
    path: str  # research_desk/... (슬래시 경로)
    line: int
    rule_no: int
    message: str

    @property
    def rule(self) -> str:
        return RULE_NAMES[self.rule_no]

    def __str__(self) -> str:
        return f"{self.path}:{self.line} — {self.rule}: {self.message}"


def format_violations(violations: list[Violation]) -> str:
    header = (f"구조 규칙 위반 {len(violations)}건 (규칙 표: research_desk/tests/architecture_rules.py). "
              "형식: 파일:줄 — 규칙 이름: 설명")
    return "\n".join([header, *(str(v) for v in violations)])


def resolve_relative(module: str, is_package: bool, level: int, name: Optional[str]) -> Optional[str]:
    """`from <점 level개><name> import …`를 절대 모듈 이름으로 푼다(importlib과 같은 방법).

    module은 import하는 파일의 모듈 이름, is_package는 그 파일이 __init__.py인지다.
    최상위 패키지 위로 올라가면 None (실행하면 ImportError가 나는 import).
    """
    package = module if is_package else module.rpartition(".")[0]
    bits = package.rsplit(".", level - 1)
    if not package or len(bits) < level:
        return None
    return f"{bits[0]}.{name}" if name else bits[0]


def check_tree(package_dir: Path) -> list[Violation]:
    """package_dir(research_desk/ 폴더) 아래 모든 .py 파일을 검사해 위반 목록을 돌려준다."""
    package_dir = Path(package_dir).resolve()
    if not package_dir.is_dir():
        raise FileNotFoundError(f"검사할 패키지 폴더가 없습니다: {package_dir}")
    files, violations = _scan(package_dir)
    checker = _Checker(files)
    for source in files:
        if source.tree is not None:
            violations.extend(checker.check_imports(source))
            if not source.is_test:
                violations.extend(_check_tables(source))
    violations.extend(checker.check_cycles())
    return sorted(set(violations))


# ── 파일 읽기 ─────────────────────────────────────────────────────────────────


@dataclass
class _Source:
    rel: str  # research_desk/core/db.py
    parts: tuple[str, ...]  # 모듈 이름에서 research_desk 다음 부분 (__init__.py는 폴더까지)
    is_package: bool
    is_test: bool
    tree: Optional[ast.Module] = None

    @property
    def module(self) -> str:
        return ".".join((PACKAGE, *self.parts))

    @property
    def area(self) -> str:
        return _area(self.parts, self.is_package)


def _scan(package_dir: Path) -> tuple[list[_Source], list[Violation]]:
    files: list[_Source] = []
    violations: list[Violation] = []
    for dirpath, dirnames, filenames in os.walk(package_dir):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath, filename)
            folders = path.relative_to(package_dir).parts[:-1]
            stem = filename[: -len(".py")]
            is_package = stem == "__init__"
            source = _Source(
                rel=path.relative_to(package_dir.parent).as_posix(),
                parts=folders if is_package else (*folders, stem),
                is_package=is_package,
                is_test="tests" in folders or filename == "conftest.py",
            )
            try:
                source.tree = ast.parse(path.read_bytes(), filename=str(path))
            except (SyntaxError, ValueError) as exc:
                line = getattr(exc, "lineno", None) or 1
                reason = getattr(exc, "msg", None) or str(exc)
                violations.append(Violation(source.rel, line, 0, f"파이썬 구문 오류라 구조 검사를 할 수 없다: {reason}"))
            files.append(source)
    return files, violations


def _area(parts: tuple[str, ...], is_package: bool) -> str:
    """모듈 이름 조각(research_desk 다음) → 칸 이름."""
    if not parts:
        return ROOT
    head = parts[0]
    if head in SIMPLE_AREAS:
        return head
    if head in ENTRY_MODULES:
        return ENTRY
    if head == FEATURES and (len(parts) > 2 or (len(parts) == 2 and is_package)):
        return f"{FEATURES}/{parts[1]}"
    return ROOT


def _is_internal(module: str) -> bool:
    return module == PACKAGE or module.startswith(PACKAGE + ".")


# ── import 규칙 (R1~R10) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Import:
    line: int
    target: str  # 가장 구체적인 절대 모듈 이름
    base: Optional[str] = None  # from-import의 from 쪽 (절대 이름)
    name: Optional[str] = None  # from-import로 가져온 이름


class _Checker:
    def __init__(self, files: list[_Source]) -> None:
        # 실제 있는 모듈 → 패키지 여부. 파일의 상위 폴더도 모두 패키지다.
        self._modules: dict[str, bool] = {}
        self._windows: dict[str, ast.Module] = {}
        for source in files:
            self._modules[source.module] = source.is_package or self._modules.get(source.module, False)
            for i in range(len(source.parts)):
                self._modules[".".join((PACKAGE, *source.parts[:i]))] = True
            if (source.is_package and source.tree is not None
                    and len(source.parts) == 2 and source.parts[0] == FEATURES):
                self._windows[source.parts[1]] = source.tree
        self._exports: dict[str, frozenset[str]] = {}
        # R6: 기능 → 기능 → [(파일, 줄)]
        self._edges: dict[str, dict[str, list[tuple[str, int]]]] = defaultdict(lambda: defaultdict(list))

    # 모듈 이름 풀기

    def _imports(self, source: _Source) -> Iterator[_Import]:
        for node in ast.walk(source.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield _Import(node.lineno, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = resolve_relative(source.module, source.is_package, node.level, node.module)
                    if base is None:
                        continue  # 최상위 패키지 위로 올라가는 상대 import는 실행조차 안 된다
                else:
                    base = node.module or ""
                for alias in node.names:
                    yield _Import(node.lineno, self._from_target(base, alias.name), base, alias.name)

    def _from_target(self, base: str, name: str) -> str:
        if name == "*" or not _is_internal(base):
            return base
        candidate = f"{base}.{name}"
        if candidate in self._modules or base in (PACKAGE, f"{PACKAGE}.{FEATURES}"):
            return candidate
        return base

    def _target_area(self, module: str) -> str:
        # 트리에 없는 모듈은 폴더로 본다(아직 만들지 않은 칸·기능).
        return _area(tuple(module.split(".")[1:]), self._modules.get(module, True))

    def _exports_name(self, feature: str, name: str) -> bool:
        """기능의 공개 창구(__init__.py) 최상위가 이 이름을 직접 묶는가(`_bound_names`)."""
        if feature not in self._exports:
            tree = self._windows.get(feature)
            self._exports[feature] = _bound_names(tree) if tree is not None else frozenset()
        return name in self._exports[feature]

    def _through_window(self, imp: _Import, feature: str) -> bool:
        window = f"{PACKAGE}.{FEATURES}.{feature}"
        if imp.target == window:
            return True
        return (imp.base == window and imp.name is not None
                and imp.target == f"{window}.{imp.name}" and self._exports_name(feature, imp.name))

    # 검사

    def check_imports(self, source: _Source) -> Iterator[Violation]:
        area = source.area
        for imp in self._imports(source):
            top = imp.target.split(".")[0]
            if top in OLD_MODULES:
                yield _violation(source, imp.line, 10,
                                 "langgraph_tagger와 옛 루트 모듈(collector·config·storage·telegram_client·main)은 "
                                 f"import하지 않는다. 금지: `{imp.target}`")
            if source.is_test:
                continue
            tool_area = EXTERNAL_TOOL_AREAS.get(top)
            if tool_area is not None and area != tool_area:
                yield _violation(source, imp.line, 9,
                                 f"외부 도구 `{top}`의 import는 research_desk/{tool_area}/ 안에서만 한다")
            if _is_internal(imp.target):
                target_area = self._target_area(imp.target)
                problem = self._internal_rule(area, imp, target_area)
                if problem is not None:
                    yield _violation(source, imp.line, *problem)
                if (area.startswith(FEATURES + "/") and target_area.startswith(FEATURES + "/")
                        and target_area != area):
                    self._edges[_feature(area)][_feature(target_area)].append((source.rel, imp.line))

    def _internal_rule(self, area: str, imp: _Import, target_area: str) -> Optional[tuple[int, str]]:
        target = f"`{imp.target}`"
        if area == ENTRY:
            # 입구는 모든 칸(하위 모듈 포함)을 import하지만, 기능은 공개 창구로만 쓴다(spec §2).
            if target_area.startswith(FEATURES + "/"):
                feature = _feature(target_area)
                if not self._through_window(imp, feature):
                    return 8, (f"입구(cli.py·__main__.py)도 기능은 공개 창구 `{PACKAGE}.{FEATURES}.{feature}`로만 "
                               f"쓴다. 하위 모듈 직접 import 금지: {target}")
            return None
        if target_area == ENTRY:
            return 8, f"다른 칸은 입구(cli·__main__)를 import하지 않는다. 금지: {target}"
        if area == ROOT:
            return 8, ("패키지 뿌리·칸 밖 파일은 research_desk 모듈을 import하지 않는다"
                       f"(여러 칸을 묶는 곳은 cli.py·__main__.py뿐). 금지: {target}")
        if area in ALLOWED_AREAS:
            rule, allowed, description = ALLOWED_AREAS[area]
            if target_area in allowed:
                return None
            return rule, f"{description}. 금지: {target}"
        if area.startswith(FEATURES + "/"):
            if target_area in ("core", "domain") or target_area == area:
                return None
            if target_area.startswith(FEATURES + "/"):
                other = _feature(target_area)
                if self._through_window(imp, other):
                    return None
                return 5, (f"다른 기능은 공개 창구 `{PACKAGE}.{FEATURES}.{other}`로만 쓴다. "
                           f"하위 모듈 직접 import 금지: {target}")
            if target_area in ("collector", "tagger", "web"):
                return 5, f"기능은 collector·tagger·web·cli를 import하지 않는다. 금지: {target}"
            return 5, f"기능은 core·domain·자기 기능·다른 기능의 공개 창구만 import한다. 금지: {target}"
        if area == "web":
            if target_area in ("core", "domain", "web"):
                return None
            if target_area.startswith(FEATURES + "/"):
                feature = _feature(target_area)
                if self._through_window(imp, feature):
                    return None
                return 7, (f"web은 기능의 공개 창구 `{PACKAGE}.{FEATURES}.{feature}`만 import한다. "
                           f"하위 모듈 직접 import 금지: {target}")
            if target_area in ("collector", "tagger"):
                return 7, f"web은 collector·tagger를 import하지 않는다. 금지: {target}"
            return 7, f"web은 core·domain·기능의 공개 창구만 import한다. 금지: {target}"
        return None

    def check_cycles(self) -> list[Violation]:
        """R6: 기능 의존 그래프의 순환(강하게 연결된 묶음)마다 한 줄."""
        edges = self._edges
        nodes = sorted(set(edges) | {b for targets in edges.values() for b in targets})
        reach = {n: _reachable(n, edges) for n in nodes}
        done: set[str] = set()
        out = []
        for node in nodes:
            if node in done:
                continue
            group = {node} | {other for other in reach[node] if node in reach[other]}
            done |= group
            if len(group) < 2:
                continue
            cycle = _shortest_cycle(min(group), group, edges)
            steps = list(zip(cycle, cycle[1:]))
            sites = [min(edges[a][b]) for a, b in steps]
            detail = ", ".join(f"{a}→{b}: {path}:{line}" for (a, b), (path, line) in zip(steps, sites))
            path, line = sites[0]
            out.append(Violation(path, line, 6, f"기능 사이 의존이 순환한다: {' → '.join(cycle)} ({detail})"))
        return out


def _violation(source: _Source, line: int, rule_no: int, message: str) -> Violation:
    return Violation(source.rel, line, rule_no, message)


def _feature(area: str) -> str:
    return area.split("/", 1)[1]


def _bound_names(tree: ast.Module) -> frozenset[str]:
    """모듈 최상위 문장이 직접 묶는 이름. 함수·클래스 본문 안은 보지 않는다.

    import(별칭 포함)·`from … import 이름`·값이 있는 대입·def·class·for/with 대상만 센다.
    `from … import *`, `__all__` 목록, 하위 모듈 import의 부수효과로 생기는 이름
    (`from .store import f` 뒤의 `store`), 값 없는 주석 선언(`x: int`)은 세지 않는다.
    """
    names: set[str] = set()

    def visit(statements: list[ast.stmt]) -> None:
        for st in statements:
            if isinstance(st, ast.Import):
                names.update(a.asname or a.name.split(".")[0] for a in st.names)
            elif isinstance(st, ast.ImportFrom):
                names.update(a.asname or a.name for a in st.names if a.name != "*")
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(st.name)
            elif isinstance(st, ast.Assign):
                for target in st.targets:
                    names.update(_target_names(target))
            elif isinstance(st, ast.AugAssign) or (isinstance(st, ast.AnnAssign) and st.value is not None):
                names.update(_target_names(st.target))
            else:  # if / try / with / for 등은 안쪽 문장도 본다
                if isinstance(st, (ast.For, ast.AsyncFor)):
                    names.update(_target_names(st.target))
                if isinstance(st, (ast.With, ast.AsyncWith)):
                    for item in st.items:
                        if item.optional_vars is not None:
                            names.update(_target_names(item.optional_vars))
                for field in ("body", "orelse", "finalbody"):
                    inner = getattr(st, field, None)
                    if isinstance(inner, list):
                        visit(inner)
                for block in [*(getattr(st, "handlers", None) or []), *(getattr(st, "cases", None) or [])]:
                    visit(block.body)

    visit(tree.body)
    return frozenset(names)


def _target_names(target: ast.expr) -> set[str]:
    """대입 대상이 묶는 이름. `a.b = …`·`a[0] = …`는 새 이름을 묶지 않는다."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(element) for element in target.elts))
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return set()


def _reachable(start: str, edges) -> set[str]:
    seen: set[str] = set()
    stack = list(edges.get(start, ()))
    while stack:
        node = stack.pop()
        if node not in seen:
            seen.add(node)
            stack.extend(edges.get(node, ()))
    return seen


def _shortest_cycle(start: str, group: set[str], edges) -> list[str]:
    """start에서 출발해 start로 돌아오는 가장 짧은 길 (start, …, start)."""
    parent: dict[str, Optional[str]] = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in sorted(edges.get(current, ())):
            if nxt == start:
                path = [current]
                while parent[path[-1]] is not None:
                    path.append(parent[path[-1]])
                return [*reversed(path), start]
            if nxt in group and nxt not in parent:
                parent[nxt] = current
                queue.append(nxt)
    raise AssertionError(f"순환 묶음 안에서 {start}로 돌아오는 길이 없습니다")


# ── 표 주인 (R11) ─────────────────────────────────────────────────────────────


def _check_tables(source: _Source) -> Iterator[Violation]:
    area = source.area
    docstrings = _docstring_ids(source.tree)
    fstring_lines = {id(part): node.lineno for node in ast.walk(source.tree)
                     if isinstance(node, ast.JoinedStr) for part in node.values}
    for node in ast.walk(source.tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _TABLE_METHODS):
            for arg in [*node.args[:1], *(kw.value for kw in node.keywords)]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    table = arg.value.strip().lower()
                    if table in TABLE_OWNERS and area not in TABLE_OWNERS[table]:
                        call = f".{node.func.attr}('{table}')"
                        yield _violation(source, arg.lineno, 11, _owner_message(table, call))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings):
            first: dict[str, re.Match] = {}
            for match in _SQL_TABLE.finditer(node.value):
                first.setdefault(match.group(1).lower(), match)
            for table, match in first.items():
                if area not in TABLE_OWNERS[table]:
                    line = fstring_lines.get(id(node)) or _match_line(node, match.start())
                    snippet = " ".join(match.group(0).split())
                    yield _violation(source, line, 11, _owner_message(table, f"SQL 문자열 `{snippet}`"))


def _owner_message(table: str, what: str) -> str:
    owners = "·".join(TABLE_OWNERS[table])
    return f"`{table}` 표는 주인 칸({owners})만 직접 다룬다. 금지: {what}"


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


def _match_line(node: ast.Constant, offset: int) -> int:
    """여러 줄 문자열이면 찾은 글자가 있는 실제 줄. 줄 수가 안 맞으면(이스케이프 등) 시작 줄."""
    text = node.value
    end = getattr(node, "end_lineno", None) or node.lineno
    if end - node.lineno == text.count("\n"):
        return node.lineno + text.count("\n", 0, offset)
    return node.lineno
