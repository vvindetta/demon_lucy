from demon_lucy.lib.args.parser import split_arg_line, parse_args
from demon_lucy.modules.linker import Linker
from demon_lucy.runtime import run_config_migrations


def test_linker_flag_migration_preserves_config_and_creates_backup(tmp_path):
    config = tmp_path / "config.txt"
    original = (
        "# --linker-auto-update-md-links\n"
        '--linker-auto-update-md-links --linker-ignore "keep this.md"\n'
        "--linker-root\n"
    )
    config.write_text(original)

    migrations = run_config_migrations(str(config))

    assert [item.get_migration_name() for item in migrations] == [
        "linker_auto_update_links_20261025"
    ]
    assert (
        tmp_path / "config.txt.bak-linker_auto_update_links_20261025"
    ).read_text() == original
    lines = config.read_text().splitlines()
    assert lines[0] == original.splitlines()[0]
    assert lines[2] == "--linker-root"
    args = parse_args(split_arg_line(lines[1]), Linker.template)
    assert args.require("linker-auto-update-links").value is True
    assert args.require("linker-ignore").value == ["keep this.md"]
    assert run_config_migrations(str(config)) == []


def test_linker_migration_leaves_current_flag_and_literal_text_alone(tmp_path):
    config = tmp_path / "config.txt"
    original = (
        "--linker-auto-update-links\n"
        '--banner "Example: --linker-auto-update-md-links"\n'
    )
    config.write_text(original)

    assert run_config_migrations(str(config)) == []
    assert config.read_text() == original
    assert list(tmp_path.glob("*.bak-*")) == []
