#!/usr/bin/env python3
"""Poll an immutable KernelSU SHA; build when it changes and publish a release.

Telegram channel upload is opt-in: it runs only when ENABLE_TELEGRAM is true.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request
import uuid
import zipfile

from verify import validate_zip

ROOT = Path(__file__).resolve().parents[2]


def validate_sha(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full lowercase commit SHA")
    return sha


def gh(command):
    result = subprocess.run(["gh", *command], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("GitHub API command failed (exit " + str(result.returncode) + ")")
    return result.stdout


def lookup_release(repo, sha):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid GITHUB_REPOSITORY")
    result = subprocess.run(["gh", "api", "repos/" + repo + "/releases/tags/ksu-" + validate_sha(sha)], capture_output=True, text=True)
    if result.returncode:
        if "HTTP 404" in result.stderr:
            # gh release view also locates drafts when the published-tag REST endpoint cannot.
            draft = subprocess.run(["gh", "release", "view", "ksu-" + sha, "--repo", repo, "--json", "isDraft"], capture_output=True, text=True)
            if draft.returncode:
                if "release not found" in draft.stderr.lower():
                    return None
                raise RuntimeError("GitHub draft lookup failed")
            value = json.loads(draft.stdout)
            if not isinstance(value, dict) or type(value.get("isDraft")) is not bool:
                raise ValueError("Malformed GitHub draft state")
            return {"draft": value["isDraft"]}
        raise RuntimeError("GitHub release lookup failed; not treating it as a new commit")
    release = json.loads(result.stdout)
    if not isinstance(release, dict) or type(release.get("draft")) is not bool:
        raise ValueError("Malformed GitHub release state")
    return release


def artifact_name(sha):
    kernel_sha = validate_sha(os.environ.get("GITHUB_SHA", ""))
    return "cancro-kernel-" + kernel_sha + "-" + validate_sha(sha)


def lookup_build(repo, sha):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid GITHUB_REPOSITORY")
    name = artifact_name(sha)
    page = 1
    while True:
        data = json.loads(gh(["api", "repos/" + repo + "/actions/artifacts?name=" + name
                              + "&per_page=100&page=" + str(page)]))
        if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
            raise ValueError("Malformed GitHub artifact state")
        for artifact in data["artifacts"]:
            if not isinstance(artifact, dict):
                raise ValueError("Malformed GitHub artifact")
            if artifact.get("name") != name:
                continue
            run = artifact.get("workflow_run")
            if not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0:
                raise ValueError("Malformed GitHub workflow run")
            if run.get("head_sha") != os.environ["GITHUB_SHA"]:
                continue
            state = json.loads(gh(["api", "repos/" + repo + "/actions/runs/" + str(run["id"])]))
            if not isinstance(state, dict) or "conclusion" not in state:
                raise ValueError("Malformed GitHub run state")
            if state.get("head_sha") == os.environ["GITHUB_SHA"] and state["conclusion"] == "success":
                return True
        if len(data["artifacts"]) < 100:
            return False
        page += 1


def detect(repo, ksu_repo, branch):
    result = subprocess.run(["git", "ls-remote", ksu_repo, "refs/heads/" + branch], capture_output=True, text=True)
    if result.returncode or len(result.stdout.splitlines()) != 1:
        raise RuntimeError("Cannot resolve KernelSU branch commit")
    fields = result.stdout.split()
    if len(fields) != 2 or fields[1] != "refs/heads/" + branch:
        raise ValueError("Unexpected KernelSU remote reference")
    sha = validate_sha(fields[0])
    return {"sha": sha, "changed": not lookup_build(repo, sha)}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_assets(dist, sha):
    names = {"cancro-kernelsu-" + sha[:12] + ".zip", "source-" + sha[:12] + ".tar.gz", "build-info.json", "kernel.config"}
    for name in names | {"SHA256SUMS"}:
        path = dist / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or unsafe build artifact: " + name)
    checksums = {}
    for line in (dist / "SHA256SUMS").read_text().splitlines():
        value, separator, name = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", value) or name not in names or name in checksums:
            raise ValueError("Invalid checksum manifest")
        checksums[name] = value
    if set(checksums) != names or any(digest(dist / name) != checksums[name] for name in names):
        raise ValueError("Artifact checksum mismatch")
    archive = dist / ("cancro-kernelsu-" + sha[:12] + ".zip")
    if archive.stat().st_size > 50_000_000:
        raise ValueError("Flash ZIP exceeds Telegram Bot API upload limit")
    validate_zip(archive, sha)
    if json.loads((dist / "build-info.json").read_text()).get("ksu_sha") != sha:
        raise ValueError("Build metadata SHA mismatch")
    return archive, [dist / name for name in sorted(names)] + [dist / "SHA256SUMS"]


def telegram_request(token, method, body, content_type):
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/" + method, data=body, headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise RuntimeError("Telegram API request failed (details redacted to protect token)") from None
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise RuntimeError("Telegram returned ok=false; release remains incomplete")
    return data.get("result")


def send_telegram(archive, sha):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-100[0-9]+|@[A-Za-z0-9_]+", chat_id):
        raise RuntimeError("Configure TELEGRAM_BOT_TOKEN and a channel TELEGRAM_CHAT_ID secret")
    chat = telegram_request(token, "getChat", json.dumps({"chat_id": chat_id}).encode(), "application/json")
    if not isinstance(chat, dict) or chat.get("type") != "channel" or type(chat.get("id")) is not int:
        raise RuntimeError("Telegram destination is not a channel; document not sent")
    boundary = uuid.uuid4().hex
    caption = "Cancro / MoKee Android 10 / KernelSU\nCommit: " + sha + "\nSHA256: " + digest(archive) + "\nBuild verified; device boot not tested."
    parts = []
    for name, value in [("chat_id", str(chat["id"])), ("caption", caption)]:
        parts.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="' + name + '"\r\n\r\n' + value + "\r\n").encode())
    parts.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="document"; filename="' + archive.name + '"\r\nContent-Type: application/zip\r\n\r\n').encode() + archive.read_bytes() + b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode())
    message = telegram_request(token, "sendDocument", b"".join(parts), "multipart/form-data; boundary=" + boundary)
    destination = message.get("chat") if isinstance(message, dict) else None
    document = message.get("document") if isinstance(message, dict) else None
    if not isinstance(destination, dict) or destination.get("id") != chat["id"] or destination.get("type") != "channel" or not isinstance(document, dict) or not isinstance(document.get("file_id"), str) or not document["file_id"] or type(message.get("message_id")) is not int or message["message_id"] <= 0:
        raise RuntimeError("Telegram returned an unexpected document destination")
    return message.get("message_id")


def publish(repo, sha, dist):
    validate_sha(sha)
    archive, assets = verify_assets(dist, sha)
    release = lookup_release(repo, sha)
    if release is not None and not release["draft"]:
        return
    tag = "ksu-" + sha
    if release is None:
        command = ["release", "create", tag, "--repo", repo, "--draft", "--prerelease", "--title", "Cancro KernelSU " + sha[:12], "--notes", "MoKee Android 10. Compiler/package checks passed; device boot untested. Full source and build metadata attached. KernelSU commit: " + sha]
        if os.environ.get("GITHUB_SHA"):
            command += ["--target", validate_sha(os.environ["GITHUB_SHA"])]
        gh(command)
    gh(["release", "upload", tag, *map(str, assets), "--repo", repo, "--clobber"])
    # Telegram channel upload is opt-in and disabled by default: only send when
    # the workflow explicitly sets ENABLE_TELEGRAM to true/1.
    if os.environ.get("ENABLE_TELEGRAM", "").lower() in ("true", "1"):
        send_telegram(archive, sha)
    gh(["release", "edit", tag, "--repo", repo, "--draft=false"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("detect")
    publisher = commands.add_parser("publish")
    publisher.add_argument("sha")
    publisher.add_argument("dist", type=Path)
    args = parser.parse_args()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if args.command == "detect":
        config = dict(line.split("=", 1) for line in (ROOT / ".github/kernel-ci/build.conf").read_text().splitlines() if "=" in line and not line.startswith("#"))
        result = detect(repo, config["KSU_REPO"], config["KSU_BRANCH"])
        result["artifact_name"] = artifact_name(result["sha"])
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                stream.write("sha=" + result["sha"] + "\nchanged=" + str(result["changed"]).lower() + "\nartifact_name=" + result["artifact_name"] + "\n")
        print(json.dumps(result))
    else:
        publish(repo, args.sha, args.dist)
        print("Release complete")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error))
