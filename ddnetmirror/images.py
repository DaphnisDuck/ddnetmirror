"""Pick a local cEOS image per device by EOS version (--image auto).

Plain version-string matching against the tags of locally available images in
the fallback image's repository. It says nothing about whether a config runs on
an image: no compatibility analysis.
"""

import re
import subprocess

DOCKER_TIMEOUT = 30  # seconds
# MAJOR.MINOR.PATCH[.BUILD][LETTERS], e.g. 4.35.7.2M, 4.32.1F
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?([A-Za-z]*)")


class ImageError(Exception):
    pass


def parse_version(text):
    """Return ((major, minor, patch, build), SUFFIX) or None if not a plain EOS version."""
    m = VERSION_RE.fullmatch(text or "")
    if not m:
        return None
    major, minor, patch, build, suffix = m.groups()
    return (int(major), int(minor), int(patch), int(build or 0)), suffix.upper()


def split_image(ref):
    """'repo[:port]/name:tag' -> (repository, tag); a tag is required."""
    repository, sep, tag = ref.rpartition(":")
    if not sep or not repository or not tag or "/" in tag or "@" in ref:
        raise ValueError(f"expected REPOSITORY:TAG, got {ref!r}")
    return repository, tag


DOCKER_HUB = ("docker.io/", "index.docker.io/", "registry-1.docker.io/")


def short_repository(repository):
    """Docker Hub names as `docker image ls` shows them: docker.io/library/ceos -> ceos,
    docker.io/me/ceos -> me/ceos. Other registries (incl. host:port) stay as written."""
    for prefix in DOCKER_HUB:
        if repository.startswith(prefix):
            repository = repository[len(prefix):]
            break
    return repository.removeprefix("library/") if repository.count("/") == 1 else repository


def local_tags(repository):
    """Return the tags of local Docker images in repository (no pulls, no registry)."""
    command = ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", repository]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
    except FileNotFoundError:
        raise ImageError("--image auto needs the docker command, which was not found") from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImageError(f"--image auto: listing local images failed: {exc}") from None
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise ImageError(
            f"--image auto: 'docker image ls' failed: {detail} "
            "(it needs access to the local Docker images, e.g. the docker group)"
        )
    tags = []
    for line in result.stdout.splitlines():
        repo, sep, tag = line.strip().rpartition(":")
        if not sep or not repo:
            raise ImageError(f"--image auto: unexpected 'docker image ls' output: {line!r}")
        if repo == repository and tag != "<none>":
            tags.append(tag)
    return sorted(set(tags))


class Selector:
    """Local candidates for --image auto, checked once before collection."""

    def __init__(self, fallback):
        self.fallback = fallback
        repository, fallback_tag = split_image(fallback)
        self.repository = short_repository(repository)
        tags = local_tags(self.repository)
        if fallback_tag not in tags:
            raise ImageError(f"fallback image {fallback} is not available locally "
                             f"(local {self.repository} tags: {', '.join(tags) or 'none'})")
        self.candidates = [(tag, parse_version(tag)) for tag in tags]
        self.ignored = [tag for tag, version in self.candidates if version is None]
        self.candidates = [(tag, v) for tag, v in self.candidates if v is not None]

    def choose(self, eos_version):
        """Return (image, reason) for a device's EOS version string.

        1. a tag equal to the version; 2. else the closest tag in the same
        MAJOR.MINOR train: smallest PATCH difference, then smallest BUILD
        difference, then the same letter suffix (F/M), then the newer version,
        then the tag text; 3. else the fallback. Never crosses trains.
        """
        version = parse_version(eos_version)
        if not eos_version:
            return self.fallback, "fallback: EOS version unknown"
        if version is None:
            return self.fallback, f"fallback: EOS version {eos_version!r} is not MAJOR.MINOR.PATCH[.BUILD][F/M]"
        (major, minor, patch, build), suffix = version
        if any(tag == eos_version for tag, _ in self.candidates):
            return f"{self.repository}:{eos_version}", "exact match"
        same_train = [(tag, v) for tag, v in self.candidates if v[0][:2] == (major, minor)]
        if not same_train:
            return self.fallback, f"fallback: no local {self.repository} image in the {major}.{minor} train"

        def key(candidate):
            tag, (nums, tag_suffix) = candidate
            return (abs(nums[2] - patch), abs(nums[3] - build), tag_suffix != suffix,
                    tuple(-n for n in nums), tag)

        tag, _ = min(same_train, key=key)
        return (f"{self.repository}:{tag}",
                f"closest in the {major}.{minor} train (version match only, not a compatibility check)")
