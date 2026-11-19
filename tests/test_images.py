import subprocess

import pytest

from ddnetmirror import images

TAGS = ["4.30.7M", "4.32.1F", "4.32.3F", "4.32.10F", "4.33.0F", "4.33.2F", "4.33.2M", "latest"]


@pytest.fixture
def docker(monkeypatch):
    """Fake `docker image ls`: set stdout, returncode/stderr, or an exception to raise."""
    state = {"stdout": "".join(f"ceos:{t}\n" for t in TAGS) + "other:4.32.2F\n<none>:<none>\n",
             "returncode": 0, "stderr": "", "raise": None, "calls": []}

    def fake_run(command, **kwargs):
        state["calls"].append((command, kwargs["timeout"]))
        if state["raise"]:
            raise state["raise"]
        return subprocess.CompletedProcess(command, state["returncode"], state["stdout"], state["stderr"])

    monkeypatch.setattr(images.subprocess, "run", fake_run)
    return state


@pytest.mark.parametrize("version, image, reason", [
    ("4.32.3F", "ceos:4.32.3F", "exact match"),
    ("4.32.9F", "ceos:4.32.10F", "closest in the 4.32 train"),  # numeric, not text order
    ("4.32.2F", "ceos:4.32.3F", "closest in the 4.32 train"),  # tie 4.32.1F/4.32.3F: newer
    ("4.33.1M", "ceos:4.33.2M", "closest in the 4.33 train"),  # tie: same suffix wins
    ("4.32.1.4F", "ceos:4.32.1F", "closest in the 4.32 train"),
    ("4.31.0F", "ceos:4.30.7M", "fallback: no local ceos image in the 4.31 train"),
    ("4.34.0F", "ceos:4.30.7M", "fallback: no local ceos image in the 4.34 train"),
    (None, "ceos:4.30.7M", "fallback: EOS version unknown"),
    ("4.32.1F-INT", "ceos:4.30.7M", "fallback: EOS version '4.32.1F-INT' is not MAJOR.MINOR.PATCH"),
])
def test_choose_exact_then_same_train_then_fallback(docker, version, image, reason):
    selector = images.Selector("ceos:4.30.7M")

    chosen, why = selector.choose(version)

    assert chosen == image and why.startswith(reason)
    assert selector.ignored == ["latest"]  # other repositories and <none> are not candidates
    assert docker["calls"] == [
        (["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", "ceos"], 30),
    ]


@pytest.mark.parametrize("change, message", [
    ({"raise": FileNotFoundError("docker")}, "needs the docker command, which was not found"),
    ({"raise": subprocess.TimeoutExpired("docker", 30)}, "listing local images failed"),
    ({"returncode": 1, "stderr": "permission denied while trying to connect"},
     "'docker image ls' failed: permission denied"),
    ({"stdout": "garbage\n"}, "unexpected 'docker image ls' output: 'garbage'"),
    ({"stdout": "ceos:4.32.1F\n"}, "fallback image ceos:4.30.7M is not available locally "
                                   "(local ceos tags: 4.32.1F)"),
])
def test_docker_problems_are_clear_errors(docker, change, message):
    docker.update(change)

    with pytest.raises(images.ImageError, match=message.replace("(", r"\(").replace(")", r"\)")):
        images.Selector("ceos:4.30.7M")


@pytest.mark.parametrize("fallback, repository", [
    ("docker.io/library/ceos:4.30.7M", "ceos"),
    ("index.docker.io/library/ceos:4.30.7M", "ceos"),
    ("library/ceos:4.30.7M", "ceos"),
    ("docker.io/me/ceos:4.30.7M", "me/ceos"),
    ("registry.example:5000/ceos:4.30.7M", "registry.example:5000/ceos"),  # stays distinct
])
def test_docker_hub_names_match_how_docker_lists_them(docker, fallback, repository):
    docker["stdout"] = f"{repository}:4.30.7M\n{repository}:4.32.1F\nother/ceos:4.32.3F\n"

    selector = images.Selector(fallback)

    assert docker["calls"][0][0][-1] == repository
    assert selector.choose("4.32.2F")[0] == f"{repository}:4.32.1F"
    assert selector.choose("4.31.0F")[0] == fallback  # the fallback is used as written
