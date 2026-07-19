"""Turnkey sender for Hermes cron jobs and webhooks -> bridge /notify.

This is the integration glue for Open Engine task TAY-9 / MOT-82 task 5
("Wire Hermes cron/webhook to send notifications to the bridge"). Hermes
automation (a `hermes cron` job, an n8n workflow, a Linear webhook, a
shell one-liner) calls this to push a notification to the connected Tab5.

Two entry points:

  * CLI:   `python -m hermes_bridge.notify_cli --title "..." --priority 2`
  * API:   `send(bridge_url, title=..., body=..., priority=...)`

It also supports rendering a named cron template (see cron_templates.py)
and sending the result:

  * CLI:   `python -m hermes_bridge.notify_cli --template deploy_failed \\
            --var service=hermes-desk --var branch=main ...`
  * API:   `send_template(bridge_url, "deploy_failed", service=..., branch=...)`

The bridge replies with {"status": "queued"|"delivered"|"suppressed",
"notification_id": "..."} and HTTP 202. This module raises on any non-2xx
so a cron job can surface the failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

import httpx

from .cron_templates import get_template, list_templates


def send(
    bridge_url: str,
    *,
    title: str,
    body: str = "",
    level: str = "info",
    priority: int = 1,
    requires_ack: bool = False,
    category: str = "general",
    display_type: str = "card",
    source: str = "hermes-cron",
    notification_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    timeout: float = 5.0,
) -> dict:
    """POST a notification payload to the bridge's /notify endpoint.

    Returns the parsed bridge response. Raises httpx.HTTPStatusError on a
    non-2xx response (so callers/cron jobs can detect delivery failure).
    """
    url = bridge_url.rstrip("/") + "/notify"
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "level": level,
        "priority": priority,
        "requires_ack": requires_ack,
        "category": category,
        "display_type": display_type,
        "source": source,
    }
    if notification_id:
        payload["notification_id"] = notification_id
    if metadata:
        payload["metadata"] = metadata

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


def send_template(
    bridge_url: str, template_name: str, *, timeout: float = 5.0, **fields
) -> dict:
    """Render a named cron template and send it to the bridge.

    Raises KeyError if the template name is unknown.
    """
    tmpl = get_template(template_name)
    if tmpl is None:
        raise KeyError(f"unknown notification template: {template_name!r}")
    rendered = tmpl.render(**fields)
    return send(bridge_url, **rendered, timeout=timeout)


def _parse_kv(items: list[str]) -> dict:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            print(f"--var expects KEY=VALUE, got {item!r}", file=sys.stderr)
            sys.exit(2)
        k, v = item.split("=", 1)
        out[k] = v
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="hermes_bridge.notify_cli",
        description="Send a proactive notification to the Hermes Desk bridge.",
    )
    p.add_argument("--bridge", default="http://localhost:8765",
                   help="Bridge base URL (default: http://localhost:8765)")
    p.add_argument("--title", help="Notification title (required without --template)")
    p.add_argument("--body", default="", help="Notification body")
    p.add_argument("--level", default="info",
                   choices=["info", "warning", "error", "success", "urgent"])
    p.add_argument("--priority", type=int, default=1,
                   choices=[0, 1, 2, 3],
                   help="0=LOW 1=NORMAL 2=HIGH 3=URGENT (default 1)")
    p.add_argument("--category", default="general")
    p.add_argument("--display-type", dest="display_type", default="card")
    p.add_argument("--source", default="hermes-cron")
    p.add_argument("--requires-ack", dest="requires_ack", action="store_true")
    p.add_argument("--template", help="Render and send a named cron template")
    p.add_argument("--var", action="append", default=[],
                   help="Template field as KEY=VALUE (repeatable)")
    p.add_argument("--timeout", type=float, default=5.0)
    args = p.parse_args(argv)

    try:
        if args.template:
            fields = _parse_kv(args.var)
            # Cast numeric-looking fields so templates with {meeting_count} work.
            casted: dict[str, Any] = {}
            for k, v in fields.items():
                try:
                    casted[k] = int(v)
                except ValueError:
                    casted[k] = v
            result = send_template(args.bridge, args.template,
                                   timeout=args.timeout, **casted)
        else:
            if not args.title:
                p.error("--title is required unless --template is given")
            result = send(
                args.bridge,
                title=args.title, body=args.body, level=args.level,
                priority=args.priority, requires_ack=args.requires_ack,
                category=args.category, display_type=args.display_type,
                source=args.source, timeout=args.timeout,
            )
    except httpx.HTTPError as exc:
        print(f"notify: delivery failed: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"notify: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
