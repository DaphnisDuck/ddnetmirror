import os

import pytest

from ddnetmirror import fetch_config
from ddnetmirror.fetch_config import read_token
from ddnetmirror.output import FetchError

SECRET = "file-secret-token"


@pytest.fixture
def home(monkeypatch, tmp_path):
    """Temporary HOME and XDG_CONFIG_HOME, no CLOUDVISION_TOKEN; returns the token path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("CLOUDVISION_TOKEN", raising=False)
    return tmp_path / "xdg" / "ddnetmirror" / "token"


def write_token(path, text=SECRET + "\n", mode=0o600, dir_mode=0o700):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(dir_mode)
    path.write_text(text)
    path.chmod(mode)


def test_environment_wins_over_the_file(home, monkeypatch):
    write_token(home)
    monkeypatch.setenv("CLOUDVISION_TOKEN", " env-token\n")

    assert read_token() == "env-token"


@pytest.mark.parametrize("text", [SECRET, SECRET + "\n", f"  {SECRET}\r\n"])
def test_file_token_is_stripped(home, monkeypatch, text):
    monkeypatch.setenv("CLOUDVISION_TOKEN", "")  # empty counts as unset
    write_token(home, text)

    assert read_token() == SECRET


def test_default_path_without_xdg_config_home(home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME")
    write_token(tmp_path / ".config" / "ddnetmirror" / "token")

    assert read_token() == SECRET


def make_symlink(path):
    write_token(path.with_name("real"))
    path.symlink_to("real")


def make_fifo(path):
    path.parent.mkdir(parents=True, mode=0o700)
    os.mkfifo(path, 0o600)


@pytest.mark.parametrize("setup, message", [
    (lambda p: None, "no CloudVision token: set CLOUDVISION_TOKEN or create {path}"),
    (lambda p: write_token(p, "\n"), "token file {path} is empty"),
    (make_symlink, "token file {path} is a symbolic link"),
    (make_fifo, "token file {path} is not a regular file"),
    (lambda p: write_token(p, mode=0o640), "token file {path} has mode 0640; group/other access"),
    (lambda p: write_token(p, mode=0o604), "token file {path} has mode 0604"),
    (lambda p: write_token(p, dir_mode=0o770), "directory {path.parent} has mode 0770"),
    (lambda p: write_token(p, dir_mode=0o757), "directory {path.parent} has mode 0757"),
])
def test_unusable_token_file_is_refused_without_revealing_content(home, setup, message):
    setup(home)

    with pytest.raises(FetchError) as exc:
        read_token()

    assert message.format(path=home) in str(exc.value)
    assert SECRET not in str(exc.value)


def test_readable_but_not_writable_directory_is_accepted(home):
    write_token(home, dir_mode=0o755)  # 0700 is recommended, not required

    assert read_token() == SECRET


def test_file_owned_by_someone_else_is_refused(home, monkeypatch):
    write_token(home)
    monkeypatch.setattr(fetch_config.os, "geteuid", lambda: os.getuid() + 1)

    with pytest.raises(FetchError, match="is not owned by you"):
        read_token()


def test_refusal_reaches_stderr_without_the_secret(home, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_token(home, mode=0o644)

    assert fetch_config.main(["--server", "cv.example", "--hostname", "leaf1"]) == 1

    out = capsys.readouterr()
    assert "has mode 0644" in out.err
    assert SECRET not in out.out + out.err



@pytest.mark.parametrize("source", ["file", "env"])
@pytest.mark.parametrize("separator", ["\n", " ", "\x07", "\u00e9"])
def test_token_with_inner_whitespace_or_control_characters_is_refused_silently(
    home, capsys, monkeypatch, tmp_path, source, separator
):
    monkeypatch.chdir(tmp_path)
    bad = f"Zq7Secret{separator}Kx9Secret"
    if source == "file":
        write_token(home, bad + "\n")
    else:
        monkeypatch.setenv("CLOUDVISION_TOKEN", bad)

    assert fetch_config.main(["--server", "cv.example", "--hostname", "leaf1"]) == 1

    out = capsys.readouterr()
    assert "the token contains whitespace, control or non-ASCII characters" in out.err
    assert "Secret" not in out.out + out.err


# --token-file PATH: an explicit file wins over everything and never falls back

@pytest.fixture
def fallbacks(home, monkeypatch):
    """A usable default token file and CLOUDVISION_TOKEN, neither of which may be used."""
    write_token(home, "default-token\n")
    monkeypatch.setenv("CLOUDVISION_TOKEN", "env-token")


def test_explicit_token_file_wins_over_environment_and_default(fallbacks, tmp_path, monkeypatch):
    write_token(tmp_path / "tokens" / "staging")
    monkeypatch.chdir(tmp_path)

    assert read_token("tokens/staging") == SECRET  # relative to the working directory
    assert read_token("~/tokens/staging") == SECRET  # ~ expanded, as a shell may not have


@pytest.mark.parametrize("setup, message", [
    (lambda p: None, "token file {path} (--token-file) does not exist"),
    (make_symlink, "token file {path} is a symbolic link"),
    (lambda p: write_token(p, mode=0o644), "token file {path} has mode 0644"),
])
def test_unusable_explicit_token_file_is_an_error_without_fallback(fallbacks, tmp_path, setup,
                                                                   message):
    path = tmp_path / "tokens" / "staging"
    path.parent.mkdir(mode=0o700)
    setup(path)

    with pytest.raises(FetchError) as exc:
        read_token(str(path))

    assert message.format(path=path) in str(exc.value)
    assert SECRET not in str(exc.value)


def test_empty_token_file_option_is_a_usage_error(home, capsys):
    with pytest.raises(SystemExit) as exc:
        fetch_config.main(["--server", "cv.example", "--hostname", "leaf1", "--token-file", ""])

    assert exc.value.code == 2
    assert "--token-file must not be empty" in capsys.readouterr().err


def test_parent_directory_is_checked_as_the_kernel_resolves_it(home, tmp_path):
    # safe/link/../token opens open/token: the open directory must be checked, not safe/
    (tmp_path / "open" / "sub").mkdir(parents=True)
    write_token(tmp_path / "open" / "token", dir_mode=0o777)
    (tmp_path / "safe").mkdir(mode=0o700)
    (tmp_path / "safe" / "link").symlink_to(tmp_path / "open" / "sub")
    path = f"{tmp_path}/safe/link/../token"

    with pytest.raises(FetchError, match=r"directory .*/safe/link/\.\. has mode 0777"):
        read_token(path)
