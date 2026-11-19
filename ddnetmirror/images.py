"""Pick a local cEOS image per device by EOS version (the default without --image).

Plain version-string matching against the tags of the local images in the
repository `ceos`. It says nothing about whether a config runs on an image: no
compatibility analysis.
"""

import re
import subprocess

REPOSITORY = "ceos"
DOCKER_TIMEOUT = 30  # seconds
# MAJOR.MINOR.PATCH[.BUILD][LETTERS], e.g. 4.35.7.2M, 4.32.1F
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?([A-Za-z]*)")
# EOS vs EOS64 is not a version difference: EOS64-4.34.2F, 4.34.2F-EOS64 -> 4.34.2F
EOS_WRAPPER_RE = re.compile(r"(?:EOS(?:64)?-)?(?P<version>.*?)(?:-EOS(?:64)?)?", re.IGNORECASE)


class ImageError(Exception):
    pass


def parse_version(text):
    """Return ((major, minor, patch, build), SUFFIX) or None if not a plain EOS version."""
    m = VERSION_RE.fullmatch(text or "")
    if not m:
        return None
    major, minor, patch, build, suffix = m.groups()
    return (int(major), int(minor), int(patch), int(build or 0)), suffix.upper()


def local_tags(repository):
    """Return the tags of local Docker images in repository (no pulls, no registry)."""
    command = ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", repository]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
    except FileNotFoundError:
        raise ImageError("picking images needs the docker command, which was not found "
                         "(or pass --image IMAGE)") from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImageError(f"listing local images failed: {exc}") from None
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise ImageError(
            f"'docker image ls' failed: {detail} "
            "(it needs access to the local Docker images, e.g. the docker group; "
            "or pass --image IMAGE)"
        )
    tags = []
    for line in result.stdout.splitlines():
        repo, sep, tag = line.strip().rpartition(":")
        if not sep or not repo:
            raise ImageError(f"unexpected 'docker image ls' output: {line!r}")
        if repo == repository and tag != "<none>":
            tags.append(tag)
    return sorted(set(tags))


def newest(candidates):
    """Highest version; ties (e.g. 4.34.8F/4.34.8M) by suffix text, then tag text."""
    return max(candidates, key=lambda c: (c[1][0], c[1][1], c[0]))


class Selector:
    """Local version-tagged ceos images, listed once before collection."""

    def __init__(self, repository=REPOSITORY):
        self.repository = repository
        tags = local_tags(repository)
        parsed = [(tag, parse_version(tag)) for tag in tags]
        self.ignored = [tag for tag, version in parsed if version is None]
        self.candidates = [(tag, v) for tag, v in parsed if v is not None]
        if not self.candidates:
            raise ImageError(
                f"no local {repository} image with a version tag such as {repository}:4.34.2F "
                f"(local {repository} tags: {', '.join(tags) or 'none'}); "
                "import one, or pass --image IMAGE"
            )

    def image(self, tag):
        return f"{self.repository}:{tag}"

    def choose(self, eos_version):
        """Return (image, reason, cross_train) for a device's EOS version string.

        1. a tag equal to the version; 2. else the newest in the same
        MAJOR.MINOR train; 3. else the newest in the nearest train, the newer
        train on equal distance; 4. unknown or unusable version: the newest.
        """
        if not eos_version:
            tag, _ = newest(self.candidates)
            return self.image(tag), "EOS version unknown; newest local image", False
        text = EOS_WRAPPER_RE.fullmatch(eos_version)["version"]
        version = parse_version(text)
        if version is None:
            tag, _ = newest(self.candidates)
            return (self.image(tag), f"EOS version {eos_version!r} not recognized; newest local image",
                    False)
        if any(tag == text for tag, _ in self.candidates):
            return self.image(text), "exact match", False
        train = version[0][:2]
        same = [c for c in self.candidates if c[1][0][:2] == train]
        if same:
            tag, _ = newest(same)
            return self.image(tag), f"newest in the {train[0]}.{train[1]} train", False

        def distance(candidate):
            other = candidate[1][0][:2]
            # nearest train first; on equal distance the newer train
            return (abs(other[0] - train[0]), abs(other[1] - train[1]), other < train)

        nearest = min(distance(c) for c in self.candidates)
        tag, (nums, _) = newest([c for c in self.candidates if distance(c) == nearest])
        return (self.image(tag),
                f"no local image in the {train[0]}.{train[1]} train; newest in the nearest "
                f"train {nums[0]}.{nums[1]}", True)
