"""Cron job templates for Hermes Desk proactive notifications.

These are reference implementations. Actual scheduling happens in Hermes
via `hermes cron` or the cronjob tool. Each template is a self-contained
prompt that produces a notification payload for the bridge's /notify endpoint.

Usage from Hermes cron:
    curl -X POST http://moto-agent-host:8765/notify \
        -H "Content-Type: application/json" \
        -d '{"title": "Morning Briefing", "body": "...", "priority": 1}'

Templates can also be triggered via Hermes gateway API calls from
automation workflows (n8n, Linear webhooks, etc.).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CronTemplate:
    """A reusable notification template."""
    name: str
    category: str
    priority: int = 1  # 0=LOW, 1=NORMAL, 2=HIGH, 3=URGENT
    requires_ack: bool = False
    display_type: str = "card"
    title_template: str = ""
    body_template: str = ""

    def render(self, **kwargs) -> dict:
        """Render the template into a notification payload."""
        return {
            "title": self.title_template.format(**kwargs),
            "body": self.body_template.format(**kwargs),
            "priority": self.priority,
            "requires_ack": self.requires_ack,
            "category": self.category,
            "display_type": self.display_type,
            "source": "hermes-cron",
        }


# ---------------------------------------------------------------------------
# Morning Briefing
# ---------------------------------------------------------------------------

MORNING_BRIEFING = CronTemplate(
    name="morning_briefing",
    category="calendar",
    priority=1,
    display_type="dashboard",
    title_template="Good morning, {name}",
    body_template=(
        "Today: {date}\n"
        "Weather: {weather}\n"
        "Calendar: {meeting_count} meetings\n"
        "{next_meeting}\n"
        "Tasks: {task_count} open ({urgent_count} urgent)"
    ),
)

# ---------------------------------------------------------------------------
# Linear Task Update
# ---------------------------------------------------------------------------

LINEAR_TASK_UPDATE = CronTemplate(
    name="linear_task_update",
    category="linear",
    priority=1,
    display_type="card",
    title_template="Task Update: {issue_id}",
    body_template="{issue_title} — moved to {new_status}",
)

LINEAR_TASK_ASSIGNED = CronTemplate(
    name="linear_task_assigned",
    category="linear",
    priority=1,
    requires_ack=True,
    display_type="card",
    title_template="New task assigned: {issue_id}",
    body_template="{issue_title}\nPriority: {priority}\nProject: {project}",
)

# ---------------------------------------------------------------------------
# Deploy / CI Notification
# ---------------------------------------------------------------------------

DEPLOY_STARTED = CronTemplate(
    name="deploy_started",
    category="deploy",
    priority=0,
    display_type="status",
    title_template="Deploy started: {service}",
    body_template="Branch: {branch}\nCommit: {commit_sha}",
)

DEPLOY_FINISHED = CronTemplate(
    name="deploy_finished",
    category="deploy",
    priority=1,
    display_type="card",
    title_template="Deploy {result}: {service}",
    body_template="Branch: {branch}\nDuration: {duration}\n{details}",
)

DEPLOY_FAILED = CronTemplate(
    name="deploy_failed",
    category="deploy",
    priority=2,
    requires_ack=True,
    display_type="card",
    title_template="Deploy FAILED: {service}",
    body_template="Branch: {branch}\nError: {error}\nLogs: {log_url}",
)

# ---------------------------------------------------------------------------
# Reminder
# ---------------------------------------------------------------------------

REMINDER = CronTemplate(
    name="reminder",
    category="reminder",
    priority=2,
    requires_ack=True,
    display_type="card",
    title_template="Reminder: {title}",
    body_template="{body}",
)

# ---------------------------------------------------------------------------
# Health / System Alert
# ---------------------------------------------------------------------------

SYSTEM_ALERT = CronTemplate(
    name="system_alert",
    category="system",
    priority=2,
    requires_ack=True,
    display_type="card",
    title_template="System Alert: {alert_type}",
    body_template="{message}\nHost: {host}\nMetric: {metric_value}",
)

# ---------------------------------------------------------------------------
# Periodic Check-in (ADHD scaffolding)
# ---------------------------------------------------------------------------

PERIODIC_CHECKIN = CronTemplate(
    name="periodic_checkin",
    category="checkin",
    priority=1,
    requires_ack=True,
    display_type="card",
    title_template="Quick check-in",
    body_template=(
        "What are you working on right now?\n"
        "Open tasks: {task_count}\n"
        "{nudge}"
    ),
)


# Registry for lookup by name
TEMPLATES: dict[str, CronTemplate] = {
    t.name: t for t in [
        MORNING_BRIEFING,
        LINEAR_TASK_UPDATE,
        LINEAR_TASK_ASSIGNED,
        DEPLOY_STARTED,
        DEPLOY_FINISHED,
        DEPLOY_FAILED,
        REMINDER,
        SYSTEM_ALERT,
        PERIODIC_CHECKIN,
    ]
}


def get_template(name: str) -> Optional[CronTemplate]:
    return TEMPLATES.get(name)


def list_templates() -> list[str]:
    return list(TEMPLATES.keys())
