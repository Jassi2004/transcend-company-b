"""Record success only after Render serves this exact revision and correct routing."""

import json
import os
import re
import time
import urllib.error
import urllib.request

ORIGIN = "https://maya-company-b-jassi.onrender.com"


def main():
    sha = os.environ["RELEASE_SHA"]
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("A full commit SHA is required")

    def api(path, body=None):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    # A newer push supersedes this release; never label it as deployed.
    if api("/commits/main")["sha"] != sha:
        print("Release superseded before verification; no deployment recorded.")
        return
    deployment = api(
        "/deployments",
        {
            "ref": sha,
            "environment": "staging",
            "auto_merge": False,
            "required_contexts": [],
            "production_environment": False,
            "transient_environment": True,
            "description": "Verify existing Render release; this record does not trigger a deploy",
        },
    )

    def status(state, description):
        api(
            f"/deployments/{deployment['id']}/statuses",
            {
                "state": state,
                "environment_url": ORIGIN,
                "log_url": f"https://github.com/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
                "description": description,
                "auto_inactive": True,
            },
        )

    status("in_progress", "Waiting for exact live SHA and critical-priority behavior")
    deadline = time.monotonic() + 16 * 60
    try:
        while time.monotonic() < deadline:
            if api("/commits/main")["sha"] != sha:
                status("inactive", "Superseded by a newer default-branch revision")
                return
            try:
                # Never forward the GitHub credential to the live application.
                with urllib.request.urlopen(f"{ORIGIN}/api/health", timeout=30) as response:
                    health = json.load(response)
                with urllib.request.urlopen(
                    f"{ORIGIN}/api/classify?severity=critical", timeout=30
                ) as response:
                    routing = json.load(response)
                if (
                    health.get("status") == "healthy"
                    and health.get("buildCommit") == sha
                    and routing.get("priority") == "critical"
                ):
                    status(
                        "success",
                        "Exact release SHA live; critical requests retain critical priority",
                    )
                    print(json.dumps({"sha": sha, "health": health, "behavior": routing}))
                    return
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                print(f"Staging not verified yet: {type(error).__name__}")
            time.sleep(15)
        raise TimeoutError("Staging did not pass exact-revision and behavior verification")
    except BaseException:
        status("failure", "Exact live revision or critical-priority behavior was not verified")
        raise


if __name__ == "__main__":
    main()
