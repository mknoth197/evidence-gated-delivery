#!/usr/bin/env python3
"""Preflight a Plan issue body before publishing it to GitHub."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from visual_applicability import validate_disposition
from plan_protocol import PLAN_PROTOCOL_V2, lint_plan

HEADINGS = (
    "Problem Statement",
    "Personas",
    "Value Assessment",
    "User Stories",
    "Design",
    "Acceptance Criteria",
    "Tasks",
    "Out of Scope",
    "Mockup Accounting Matrix",
    "Cross-Reference",
)


def section(body: str, heading: str) -> str:
    match = re.search(
        rf"(?ims)^##[ \t]+{re.escape(heading)}[ \t]*$\n(.*?)(?=^##[ \t]+|\Z)",
        body,
    )
    return match.group(1).strip() if match else ""


def approved_url(url: str, extra_hosts: list[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path
    if parsed.scheme != "https":
        return False
    if host == "github.com" and path.startswith("/user-attachments/"):
        return True
    if host == "user-images.githubusercontent.com":
        return True
    if host == "openai.site" or host.endswith(".openai.site"):
        return True
    if host == "sites.openai.com" or host.endswith(".sites.openai.com"):
        return True
    return any(host == value.lower() or host.endswith(f".{value.lower()}") for value in extra_hosts)


def private_github_attachment_url(url: str, issue_url: str) -> tuple[str | None, str | None]:
    issue_match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", issue_url.rstrip("/")
    )
    asset_id = url.rstrip("/").split("/")[-1]
    if issue_match is None or not asset_id:
        return None, "private GitHub attachment resolution requires an exact issue URL"
    owner, repo, number = issue_match.groups()
    command = [
        "gh",
        "api",
        f"repos/{owner}/{repo}/issues/{number}/comments?per_page=100",
        "-H",
        "Accept: application/vnd.github.html+json",
        "--paginate",
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        pages = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return None, f"authenticated GitHub attachment resolution failed: {exc}"
    comments = pages if isinstance(pages, list) else []
    rendered = "\n".join(
        str(comment.get("body_html", "")) for comment in comments if isinstance(comment, dict)
    )
    candidates = re.findall(r'https://private-user-images\.githubusercontent\.com/[^"\s<>]+', rendered)
    for candidate in candidates:
        resolved = html.unescape(candidate)
        if asset_id in resolved:
            return resolved, None
    return None, "authenticated GitHub issue read-back lacks the referenced attachment"


def remote_image_sha256(
    url: str, github_issue_url: str | None = None
) -> tuple[str | None, str | None]:
    request = Request(url, headers={"User-Agent": "evidence-gated-delivery/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                return None, f"remote artifact content type is {content_type}, not image/*"
            payload = response.read(20 * 1024 * 1024 + 1)
    except HTTPError as exc:
        if (
            exc.code == 404
            and github_issue_url
            and urlparse(url).hostname == "github.com"
            and urlparse(url).path.startswith("/user-attachments/")
        ):
            resolved, resolution_error = private_github_attachment_url(url, github_issue_url)
            if resolution_error:
                return None, resolution_error
            return remote_image_sha256(resolved or "")
        return None, f"remote artifact fetch failed: {exc}"
    except (URLError, TimeoutError) as exc:
        return None, f"remote artifact fetch failed: {exc}"
    if len(payload) > 20 * 1024 * 1024:
        return None, "remote artifact exceeds 20 MiB validation limit"
    return hashlib.sha256(payload).hexdigest(), None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("body", type=Path)
    parser.add_argument(
        "--plan-protocol-version",
        default=PLAN_PROTOCOL_V2,
        choices=("plan-protocol/v1", PLAN_PROTOCOL_V2),
    )
    parser.add_argument("--final-image", type=Path)
    parser.add_argument(
        "--visual-disposition",
        type=Path,
        help="JSON visual_artifact_disposition receipt bound to the Plan body",
    )
    parser.add_argument(
        "--user-direction",
        action="append",
        help="Persisted effective user direction; repeat in source order",
    )
    parser.add_argument(
        "--runtime-evidence",
        type=Path,
        help="JSON array of current, scope-bound runtime visual evidence",
    )
    parser.add_argument(
        "--runtime-evidence-not-before",
        help="ISO-8601 lower bound for current runtime evidence",
    )
    parser.add_argument(
        "--runtime-evidence-not-after",
        help="ISO-8601 validation-time upper bound for runtime evidence",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Additional explicitly approved durable artifact hostname",
    )
    parser.add_argument(
        "--github-issue-url",
        help="Exact private GitHub issue URL used to resolve an authenticated attachment read-back",
    )
    args = parser.parse_args()

    errors: list[str] = []
    try:
        body = args.body.read_text()
    except OSError as exc:
        print(json.dumps({"status": "INVALID", "errors": [str(exc)]}, indent=2))
        return 2

    disposition: dict[str, object]
    runtime_evidence = None
    if args.runtime_evidence:
        try:
            runtime_evidence = json.loads(args.runtime_evidence.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"runtime evidence is unreadable: {exc}")
    if args.visual_disposition:
        try:
            disposition = json.loads(args.visual_disposition.read_text())
            if isinstance(disposition.get("visual_artifact_disposition"), dict):
                disposition = disposition["visual_artifact_disposition"]
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "INVALID", "errors": [str(exc)]}, indent=2))
            return 2
    elif args.final_image:
        disposition = {
            "policy_version": "legacy",
            "decision": "VISUAL_REQUIRED",
            "evidence_mode": "generative_mockup",
        }
        errors.append("visual disposition receipt is required")
    else:
        disposition = {}
        errors.append("visual disposition receipt is required")
    visual_mode, _inventory, disposition_errors = validate_disposition(
        disposition,
        body,
        phase="plan",
        require_embedded_inventory=args.plan_protocol_version == PLAN_PROTOCOL_V2,
        authoritative_user_directions=args.user_direction,
        authoritative_runtime_evidence=runtime_evidence,
        runtime_evidence_not_before=args.runtime_evidence_not_before,
        runtime_evidence_not_after=args.runtime_evidence_not_after,
    )
    errors.extend(disposition_errors)
    lint_receipt = None
    if args.plan_protocol_version == PLAN_PROTOCOL_V2:
        lint_receipt = lint_plan(body)
        if lint_receipt["status"] != "PASS":
            errors.extend(
                f"{finding['finding_id']}: {finding['evidence']}"
                for finding in lint_receipt["findings"]
            )

    for heading in HEADINGS:
        content = section(body, heading)
        if not content:
            errors.append(f"missing or empty top-level section: {heading}")

    acceptance = section(body, "Acceptance Criteria")
    tasks = section(body, "Tasks")
    matrix = section(body, "Mockup Accounting Matrix")
    design = section(body, "Design")
    cross_reference = section(body, "Cross-Reference")

    ears = re.findall(
        r"(?im)^(?:[-*][ \t]+)?(?:WHEN|WHILE|WHERE|IF)\b.+\bSHALL\b.+$",
        acceptance,
    )
    checkboxes = re.findall(r"(?im)^[ \t]*[-*][ \t]+\[[ xX]\][ \t]+.+$", tasks)
    matrix_rows = [
        line
        for line in matrix.splitlines()
        if line.strip().startswith("|")
        and "---" not in line
        and "visual requirement" not in line.lower()
    ]
    if not ears:
        errors.append("Acceptance Criteria must contain at least one EARS statement")
    if not checkboxes:
        errors.append("Tasks must contain at least one checklist item")
    if not matrix_rows:
        errors.append("Mockup Accounting Matrix must contain at least one normative row")
    if "```mermaid" not in design.lower():
        errors.append("Design must contain a Mermaid diagram")
    if not re.search(r"https://github\.com/[^/]+/[^/]+/issues/\d+", cross_reference):
        errors.append("Cross-Reference must contain the exact Research issue URL")

    image_sha: str | None = None
    durable_urls: list[str] = []
    if visual_mode == "generative_mockup":
        if args.final_image is None:
            errors.append("generative_mockup requires --final-image")
        else:
            try:
                image_bytes = args.final_image.read_bytes()
            except OSError as exc:
                errors.append(str(exc))
            else:
                image_sha = hashlib.sha256(image_bytes).hexdigest()
                if image_sha not in body:
                    errors.append("issue body does not contain the final image SHA-256")
                urls = re.findall(r"https?://[^\s)>]+", body)
                durable_urls = [
                    url.rstrip(".,")
                    for url in urls
                    if approved_url(url.rstrip(".,"), args.allow_host)
                ]
                if not durable_urls:
                    errors.append("issue body lacks an approved durable final mockup URL")
                elif len(durable_urls) != 1:
                    errors.append("issue body must identify exactly one durable final mockup URL")
                else:
                    remote_sha, remote_error = remote_image_sha256(
                        durable_urls[0], args.github_issue_url
                    )
                    if remote_error:
                        errors.append(remote_error)
                    elif remote_sha != image_sha:
                        errors.append(
                            "hosted final mockup bytes do not match the local image SHA-256"
                        )
    elif args.final_image is not None:
        errors.append(f"visual mode {visual_mode} must not receive --final-image")

    result = {
        "status": "VALID" if not errors else "INVALID",
        "body": str(args.body),
        "visual_evidence_mode": visual_mode,
        "plan_protocol_version": args.plan_protocol_version,
        "plan_lint": lint_receipt,
        "final_image": str(args.final_image) if args.final_image else None,
        "final_image_sha256": image_sha,
        "acceptance_criteria_count": len(ears),
        "implementation_task_count": len(checkboxes),
        "mockup_accounting_rows": len(matrix_rows),
        "durable_image_urls": durable_urls,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
