"""bazel aquery → compile_commands conversion (wikify.bazel_cc.convert).

The orchestration (running bazel) isn't unit-testable, but the conversion that
makes scip-clang work IS, and it encodes the non-obvious fixes:
  - split a combined "-isystem path" token into two argv elements;
  - absolutize include paths against the execroot;
  - `directory` = real repo root, `file` left repo-relative;
  - strip output-generating flags (-MD/-MF/-o).
"""

from pathlib import Path

from wikify import bazel_cc


def _execroot(tmp_path: Path) -> Path:
    ex = tmp_path / "execroot"
    (ex / "external/sysroot/usr/include/c++/10").mkdir(parents=True)
    (ex / "external/sysroot/usr/include/c++/10/cstddef").write_text("")
    (ex / "bazel-out/bin/gen").mkdir(parents=True)
    return ex


def test_split_combined_isystem_token_and_absolutize(tmp_path):
    ex = _execroot(tmp_path)
    aq = {"actions": [{"arguments": [
        "external/toolchain/clang", "-c", "pkg/a.cc",
        "-isystem external/sysroot/usr/include/c++/10",      # combined token
        "-Ibazel-out/bin/gen",                                # attached
        "-o", "bazel-out/bin/a.o",
    ]}]}
    [e] = bazel_cc.convert(aq, str(ex), "/repo")
    args = e["arguments"]
    # combined token split into two argv elements, path absolutized
    i = args.index("-isystem")
    assert args[i + 1] == str(ex / "external/sysroot/usr/include/c++/10")
    # attached -I absolutized
    assert f"-I{ex / 'bazel-out/bin/gen'}" in args


def test_directory_is_repo_root_and_file_relative(tmp_path):
    ex = _execroot(tmp_path)
    aq = {"actions": [{"arguments": ["clang", "-c", "pkg/a.cc"]}]}
    [e] = bazel_cc.convert(aq, str(ex), "/the/repo")
    assert e["directory"] == "/the/repo"
    assert e["file"] == "pkg/a.cc"            # repo-relative, NOT absolutized


def test_output_flags_stripped(tmp_path):
    ex = _execroot(tmp_path)
    aq = {"actions": [{"arguments": [
        "clang", "-MD", "-MF", "x.d", "-frandom-seed=foo",
        "-o", "x.o", "-c", "pkg/a.cc",
    ]}]}
    [e] = bazel_cc.convert(aq, str(ex), "/repo")
    a = e["arguments"]
    assert "-MD" not in a and "-MF" not in a and "-o" not in a
    assert not any(x.startswith("-frandom-seed=") for x in a)
    assert "pkg/a.cc" in a


def test_action_without_source_is_skipped(tmp_path):
    ex = _execroot(tmp_path)
    aq = {"actions": [{"arguments": ["clang", "--version"]},
                      {"arguments": ["clang", "-c", "pkg/a.cc"]}]}
    entries = bazel_cc.convert(aq, str(ex), "/repo")
    assert len(entries) == 1 and entries[0]["file"] == "pkg/a.cc"
