import subprocess

import pytest

from ddnetmirror import images


@pytest.fixture
def docker(monkeypatch):
    """Fake `docker image ls`: set tags, returncode/stderr, or an exception to raise."""
    state = {"tags": [], "returncode": 0, "stderr": "", "raise": None, "calls": []}

    def fake_run(command, **kwargs):
        state["calls"].append((command, kwargs["timeout"]))
        if state["raise"]:
            raise state["raise"]
        stdout = "".join(f"ceos:{t}\n" for t in state["tags"]) + "other:4.34.2F\n<none>:<none>\n"
        return subprocess.CompletedProcess(command, state["returncode"], stdout, state["stderr"])

    monkeypatch.setattr(images.subprocess, "run", fake_run)
    return state


@pytest.mark.parametrize("tags, image, reason, cross_train", [
    # the four examples from issue #40, target 4.34.2F
    (["4.34.1F", "4.34.8M"], "ceos:4.34.8M", "newest in the 4.34 train", False),
    (["4.33.12M", "4.35.1F", "4.35.12M"], "ceos:4.35.12M",
     "no local image in the 4.34 train; newest in the nearest train 4.35", True),  # tie: newer train
    (["4.32.1M", "4.32.12M", "4.33.6M", "4.33.8M"], "ceos:4.33.8M",
     "no local image in the 4.34 train; newest in the nearest train 4.33", True),
    (["4.30.7M"], "ceos:4.30.7M", "no local image in the 4.34 train; newest in the nearest train 4.30",
     True),
    # exact wins over newer; numeric, not text order; F/M tie by suffix text
    (["4.34.2F", "4.34.10M"], "ceos:4.34.2F", "exact match", False),
    (["4.34.9M", "4.34.10F", "latest"], "ceos:4.34.10F", "newest in the 4.34 train", False),
    (["4.34.8F", "4.34.8M"], "ceos:4.34.8M", "newest in the 4.34 train", False),
])
def test_choose_exact_then_newest_in_train_then_nearest_train(docker, tags, image, reason, cross_train):
    docker["tags"] = tags

    assert images.Selector().choose("4.34.2F") == (image, reason, cross_train)
    assert docker["calls"] == [
        (["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", "ceos"], 30),
    ]


@pytest.mark.parametrize("version, image, reason", [
    ("4.34.2F-EOS64", "ceos:4.34.2F", "exact match"),  # EOS vs EOS64 ignored
    ("EOS64-4.34.2F", "ceos:4.34.2F", "exact match"),
    (None, "ceos:4.35.1F", "EOS version unknown; newest local image"),
    ("4.34.2F-INT", "ceos:4.35.1F", "EOS version '4.34.2F-INT' not recognized; newest local image"),
    ("4.34.2F\n", "ceos:4.35.1F", "EOS version '4.34.2F\\n' not recognized; newest local image"),
])
def test_eos64_is_ignored_and_unknown_versions_get_the_newest(docker, version, image, reason):
    docker["tags"] = ["4.33.1F", "4.34.2F", "4.35.1F"]

    assert images.Selector().choose(version) == (image, reason, False)


@pytest.mark.parametrize("change, message", [
    ({"raise": FileNotFoundError("docker")}, "needs the docker command, which was not found"),
    ({"raise": subprocess.TimeoutExpired("docker", 30)}, "listing local images failed"),
    ({"returncode": 1, "stderr": "permission denied while trying to connect"},
     "'docker image ls' failed: permission denied"),
    ({"tags": ["latest"]}, "no local ceos image with a version tag"),  # other repositories don't count
])
def test_docker_problems_and_no_usable_image_are_clear_errors(docker, change, message):
    docker.update(change)

    with pytest.raises(images.ImageError, match=message):
        images.Selector()


def test_docker_does_not_inherit_the_cloudvision_token(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "ceos:4.34.2F\n", "")

    monkeypatch.setattr(images.subprocess, "run", fake_run)
    monkeypatch.setenv("CLOUDVISION_TOKEN", "secret")
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/docker.sock")

    assert images.local_tags("ceos") == ["4.34.2F"]
    assert "CLOUDVISION_TOKEN" not in seen and seen["DOCKER_HOST"] == "unix:///run/docker.sock"
