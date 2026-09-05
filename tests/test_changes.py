"""History is routing, not content (wikify.changes): git log parsing, attribution of
commits to the pages whose cited files they touched, the packet block, the change page
with its regeneration-safe auto block, the log line, and forge links."""

from wikify import changes


def _log_output():
    R, F = "\x1e", "\x1f"
    return (f"{R}aaaa1111{F}aaaa111{F}2026-09-05{F}[rng] fix seed{F}Body line one.\n\nBody line two.{F}\n"
            f"src/rng.cc\nsrc/rng.h\n"
            f"{R}bbbb2222{F}bbbb222{F}2026-09-04{F}docs typo{F}{F}\nREADME.md\n")


def test_parse_git_log_records_files_and_body():
    cs = changes.parse_git_log(_log_output())
    assert [c.short for c in cs] == ["aaaa111", "bbbb222"]
    assert cs[0].subject == "[rng] fix seed" and cs[0].files == ["src/rng.cc", "src/rng.h"]
    assert "Body line two." in cs[0].body and cs[1].body == "" and cs[1].files == ["README.md"]


def test_attribute_pages_and_since_block():
    cs = changes.parse_git_log(_log_output())
    changes.attribute_pages(cs, {"rng-page": {"src/rng.cc"}, "other": {"src/x.cc"}})
    assert cs[0].pages == ["rng-page"] and cs[1].pages == []
    block = changes.render_since_block("rng-page", cs, "0123456789abcdef")
    assert block.startswith("## Since last ingest") and "`aaaa111` (2026-09-05) [rng] fix seed" in block
    assert "Body line one." in block and "files: `src/rng.cc`, `src/rng.h`" in block
    assert "bbbb222" not in block
    assert changes.render_since_block("other", cs, "0123456789") == ""


def test_commit_url_from_blob_or_tree_base():
    assert changes.commit_url("https://github.com/org/repo/blob/abc123", "deadbeef") == \
        "https://github.com/org/repo/commit/deadbeef"
    assert changes.commit_url("https://github.com/org/repo/tree/abc123/", "d") == "https://github.com/org/repo/commit/d"
    assert changes.commit_url("../raw/code/repo", "d") is None and changes.commit_url(None, "d") is None


def _rec():
    cs = changes.parse_git_log(_log_output())
    changes.attribute_pages(cs, {"rng-page": {"src/rng.cc"}})
    return changes.Reconcile(old_ref="0123456789abcdef", new_ref="fedcba9876543210", build=["new-page"],
                             rebuild=["rng-page"], relink=["moved-page"], leave=["same"], changed=3,
                             removed=1, moved=2, removed_files=["src/gone.cc"], commits=cs)


def test_change_page_content_links_and_narrative_survives(tmp_path):
    rec = _rec()
    text = changes.render_change_page(rec, "demo", "https://github.com/o/r/blob/fedcba9876543210",
                                      "2026-09-05T00:00:00Z", "wikify/0.2.0")
    assert text.startswith("---\ntype: changelog\n")
    assert "| from | `0123456789` |" in text and "| to | `fedcba9876` |" in text
    assert "| [rng-page](../concepts/rng-page.md) | rebuilt: cited code changed (1 commit(s)) |" in text
    assert "| [moved-page](../concepts/moved-page.md) | relinked" in text
    assert "- `src/gone.cc`" in text
    assert "[`aaaa111`](https://github.com/o/r/commit/aaaa1111)" in text
    assert "### Other commits (1)" in text and "`(root)` (1)" in text
    # narrative above the auto marker survives regeneration; the block is rewritten
    narrative = text.split(changes.AUTO_BEGIN)[0] + "This bump fixes the RNG seed race.\n\n"
    edited = narrative + text.split(changes.AUTO_BEGIN, 1)[1].replace("fedcba9876", "STALE")
    edited = changes.AUTO_BEGIN.join([narrative, edited.split(changes.AUTO_BEGIN, 1)[1]]) if changes.AUTO_BEGIN in edited else edited
    regen = changes.render_change_page(rec, "demo", None, "2026-09-06T00:00:00Z", "wikify/0.2.0", existing=narrative + changes.AUTO_BEGIN + "\nSTALE\n" + changes.AUTO_END + "\n")
    assert "This bump fixes the RNG seed race." in regen and "STALE" not in regen
    assert "git -C raw/code/demo show <sha>" in regen          # no forge base → git hint
    assert "`aaaa111` 2026-09-05" in regen


def test_reconcile_roundtrip_and_log_line(tmp_path):
    rec = _rec()
    back = changes.Reconcile.from_json(rec.to_json())
    assert back == rec
    log = changes.append_log(tmp_path, rec, "demo", "2026-09-05")
    text = log.read_text()
    assert "## [2026-09-05] ingest | demo @ fedcba9876 (from 0123456789)" in text
    assert "2 commit(s); symbols 3 changed, 1 removed, 2 moved; pages 1 built, 1 rebuilt, 1 relinked, 1 unchanged" in text
    assert changes.append_log(tmp_path, rec, "demo", "2026-09-05").read_text() == text   # idempotent
