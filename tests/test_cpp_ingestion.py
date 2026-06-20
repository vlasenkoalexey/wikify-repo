"""C++ ingestion via scip-clang — proves the pipeline is language-agnostic.

The same SCIP parser + graph builder + callers/callees derivation that handle
scip-python also handle scip-clang output, and multiple indexes (C++ + Python)
union into one graph. This is the Phase-2 C++ path's foundation (design §Stage 1,
mixed-language). Skipped if no scip-clang binary is available.
"""

import json
import shutil
from pathlib import Path

import pytest

from wikify import scip_index

FIXTURE = Path(__file__).parent / "fixtures" / "cpp_callgraph"
PY_FIXTURE = Path(__file__).parent / "fixtures" / "callgraph"

# scip-clang binary: the pinned vendored one, or on PATH.
_VENDORED = Path(__file__).parents[1] / "vendor" / "bin" / "scip-clang-033"
SCIP_CLANG = str(_VENDORED) if _VENDORED.exists() else shutil.which("scip-clang")

pytestmark = pytest.mark.skipif(SCIP_CLANG is None, reason="scip-clang not available")


@pytest.fixture(scope="module")
def cpp_index(tmp_path_factory):
    """Run scip-clang on the C++ fixture → parsed SCIP index."""
    work = tmp_path_factory.mktemp("cpp")
    # copy sources so the compile DB paths are stable + writable
    for f in ("mathlib.h", "mathlib.cpp", "main.cpp"):
        shutil.copy(FIXTURE / f, work / f)
    compdb = [
        {"directory": str(work), "file": str(work / src),
         "command": f"clang++ -std=c++17 -I{work} -c {work / src}"}
        for src in ("mathlib.cpp", "main.cpp")
    ]
    (work / "compile_commands.json").write_text(json.dumps(compdb))
    out = work / "cpp.scip"
    scip_index.run_clang_indexer(work, work / "compile_commands.json", out,
                                 scip_clang_bin=SCIP_CLANG)
    return scip_index.parse_index(out)


def test_cpp_symbols_and_kinds(cpp_index):
    g = scip_index.build_graph(cpp_index)
    names = {s.name for s in g.symbols.values()}
    assert {"Adder", "add", "add_twice", "square", "compute"} <= names
    adder = g.symbols[g.find("Adder")[0]]
    assert adder.suffix == "Type"  # class


def test_cpp_call_structure(cpp_index):
    """The reference-scoping callers/callees derivation works on C++ too."""
    g = scip_index.build_graph(cpp_index)

    def callees(name):
        return {g.symbols[c].name for c in g.callees(g.find(name)[0])}

    assert "add" in callees("add_twice")              # add_twice() calls add()
    assert "add" in callees("square")                  # square() calls add()
    assert {"add_twice", "square"} <= callees("compute")  # compute() calls both


@pytest.mark.skipif(shutil.which("scip-python") is None, reason="scip-python not available")
def test_merge_cpp_and_python(cpp_index, tmp_path):
    """Two indexes (C++ + Python) union into ONE graph — the mixed-repo path."""
    py_out = tmp_path / "py.scip"
    scip_index.run_indexer(PY_FIXTURE, py_out, project_name="callgraph")
    py_index = scip_index.parse_index(py_out)

    g = scip_index.build_graph(py_index, cpp_index)  # union
    names = {s.name for s in g.symbols.values()}
    assert "Adder" in names          # from the C++ index
    assert "compute" in names        # present in both; merged
    # the Python fixture's `mul`/`square` and the C++ `Adder` coexist
    assert "mul" in names
