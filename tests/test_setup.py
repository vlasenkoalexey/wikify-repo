"""`wikify setup` / `wikify doctor` (wikify.setup_cmd): the skill ships inside the package and
installs at user level or into a project idempotently; doctor reports; wrapper scripts exist."""

import os
from pathlib import Path

from typer.testing import CliRunner

from wikify import setup_cmd
from wikify.cli import app

runner = CliRunner()


def test_skill_is_package_data_and_agents_dir_symlinks_to_it():
    src = setup_cmd.skill_source("wikify-ingest-repo")
    assert (src / "SKILL.md").is_file() and (src / "prompts" / "synthesis.md").is_file()
    link = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "wikify-ingest-repo"
    assert link.is_symlink() and link.resolve() == src.resolve()


def test_install_skill_user_is_idempotent(tmp_path):
    dest, status = setup_cmd.install_skill_user(tmp_path / ".claude")
    assert status == "created" and (dest / "SKILL.md").is_file() and (dest / "prompts").is_dir()
    assert setup_cmd.install_skill_user(tmp_path / ".claude")[1] == "unchanged"
    (dest / "SKILL.md").write_text("tampered")
    assert setup_cmd.install_skill_user(tmp_path / ".claude")[1] == "updated"
    assert (dest / "SKILL.md").read_text() != "tampered"


def test_install_skill_project_copies_symlinks_and_ignores(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    dest, status = setup_cmd.install_skill_project(proj)
    assert status == "created" and (dest / "SKILL.md").is_file()
    link = proj / ".claude" / "skills" / "wikify-ingest-repo"
    assert link.is_symlink() and os.readlink(link) == "../../.agents/skills/wikify-ingest-repo"
    assert (link / "SKILL.md").is_file()
    assert "/.claude/skills/" in (proj / ".gitignore").read_text().splitlines()
    before = (proj / ".gitignore").read_text()
    assert setup_cmd.install_skill_project(proj)[1] == "unchanged"
    assert (proj / ".gitignore").read_text() == before


def test_setup_and_doctor_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKIFY_HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    proj.mkdir()
    res = runner.invoke(app, ["setup", "--claude-dir", str(tmp_path / ".claude"), "--project", str(proj),
                              "--indexers", "none"])
    assert res.exit_code == 0, res.output
    assert "skill (Claude Code, user): created" in res.output and "skill (project): created" in res.output
    assert (tmp_path / ".claude" / "skills" / "wikify-ingest-repo" / "SKILL.md").is_file()
    res = runner.invoke(app, ["doctor", "--claude-dir", str(tmp_path / ".claude"), "--project", str(proj)])
    assert res.exit_code == 0, res.output
    assert "OK   wikify" in res.output and "OK   skill (Claude Code, user)" in res.output
    assert "skill (project .agents/skills)" in res.output
    res = runner.invoke(app, ["doctor", "--claude-dir", str(tmp_path / "nowhere")])
    assert "MISS skill (Claude Code, user)" in res.output and "-> wikify setup" in res.output


def test_init_with_skill(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    res = runner.invoke(app, ["init", "--root", str(repo), "--with-skill"])
    assert res.exit_code == 0, res.output
    assert (repo / ".agents" / "skills" / "wikify-ingest-repo" / "SKILL.md").is_file()
    assert (repo / ".claude" / "skills" / "wikify-ingest-repo").is_symlink()


def test_vendor_bin_honors_wikify_home(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKIFY_HOME", str(tmp_path / "wh"))
    assert setup_cmd.vendor_bin() == tmp_path / "wh" / "vendor" / "bin"
    assert setup_cmd.find_tool("definitely-not-a-tool-xyz") is None


def test_wrapper_scripts_delegate_to_setup():
    root = Path(__file__).resolve().parents[1]
    assert "wikify setup" in (root / "scripts" / "setup-vendor.sh").read_text()
    assert "wikify setup" in (root / "scripts" / "install-skill.sh").read_text()


def test_setup_project_injects_retrieval_block(tmp_path):
    from wikify.cli import WIKIFY_BEGIN, WIKIFY_END
    # a SCHEMA.md-routed project: the block goes into SCHEMA.md only
    proj = tmp_path / "host"
    proj.mkdir()
    (proj / "SCHEMA.md").write_text("# schema\n\nrules\n")
    (proj / "CLAUDE.md").write_text("@SCHEMA.md\n")
    res = runner.invoke(app, ["setup", "--no-user", "--indexers", "none", "--project", str(proj)])
    assert res.exit_code == 0, res.output
    assert "SCHEMA.md: updated wikify block" in res.output
    schema = (proj / "SCHEMA.md").read_text()
    assert schema.startswith("# schema") and WIKIFY_BEGIN in schema and "`wiki/code/<slug>/`" in schema
    assert WIKIFY_BEGIN not in (proj / "CLAUDE.md").read_text()
    # idempotent
    before = schema
    runner.invoke(app, ["setup", "--no-user", "--indexers", "none", "--project", str(proj)])
    assert (proj / "SCHEMA.md").read_text() == before
    # no SCHEMA.md, no agent files: CLAUDE.md + AGENTS.md are created; wiki_dir honored
    bare = tmp_path / "bare"
    bare.mkdir()
    res = runner.invoke(app, ["setup", "--no-user", "--indexers", "none", "--project", str(bare),
                              "--wiki-dir", "wiki/codebases"])
    assert res.exit_code == 0, res.output
    for f in ("CLAUDE.md", "AGENTS.md"):
        text = (bare / f).read_text()
        assert text.count(WIKIFY_END) == 1 and "`wiki/codebases/<slug>/`" in text
    # explicit target list
    res = runner.invoke(app, ["setup", "--no-user", "--indexers", "none", "--project", str(bare),
                              "--instructions", "GEMINI.md"])
    assert "GEMINI.md: created wikify block" in res.output


def test_setup_project_no_skill_injects_block_only(tmp_path):
    """A project that must not carry the skill (it ships in a distribution): block only."""
    from wikify.cli import WIKIFY_BEGIN
    proj = tmp_path / "kb"
    proj.mkdir()
    (proj / "SCHEMA.md").write_text("# schema\n")
    res = runner.invoke(app, ["setup", "--no-user", "--indexers", "none", "--project", str(proj),
                              "--no-skill", "--wiki-dir", "wiki/codebases"])
    assert res.exit_code == 0, res.output
    assert "skill (project): skipped" in res.output and "SCHEMA.md: updated wikify block" in res.output
    assert not (proj / ".agents").exists() and not (proj / ".claude").exists()
    assert not (proj / ".gitignore").exists()
    assert WIKIFY_BEGIN in (proj / "SCHEMA.md").read_text() and "`wiki/codebases/<slug>/`" in (proj / "SCHEMA.md").read_text()
