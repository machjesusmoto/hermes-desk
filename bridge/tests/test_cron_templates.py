"""Tests for cron templates."""
from __future__ import annotations

import pytest

from hermes_bridge.cron_templates import (
    MORNING_BRIEFING,
    LINEAR_TASK_UPDATE,
    LINEAR_TASK_ASSIGNED,
    DEPLOY_STARTED,
    DEPLOY_FINISHED,
    DEPLOY_FAILED,
    REMINDER,
    SYSTEM_ALERT,
    PERIODIC_CHECKIN,
    TEMPLATES,
    get_template,
    list_templates,
)


class TestCronTemplates:
    def test_all_templates_registered(self):
        names = list_templates()
        assert "morning_briefing" in names
        assert "linear_task_update" in names
        assert "deploy_failed" in names
        assert "periodic_checkin" in names
        assert len(names) == 9

    def test_get_template(self):
        t = get_template("morning_briefing")
        assert t is MORNING_BRIEFING

    def test_get_template_unknown(self):
        assert get_template("nonexistent") is None

    def test_morning_briefing_render(self):
        result = MORNING_BRIEFING.render(
            name="Dylan",
            date="2026-07-15",
            weather="72°F Sunny",
            meeting_count=3,
            next_meeting="10am Standup",
            task_count=8,
            urgent_count=2,
        )
        assert result["title"] == "Good morning, Dylan"
        assert "72°F Sunny" in result["body"]
        assert "8 open" in result["body"]
        assert result["category"] == "calendar"
        assert result["display_type"] == "dashboard"
        assert result["source"] == "hermes-cron"

    def test_deploy_failed_render(self):
        result = DEPLOY_FAILED.render(
            service="hermes-desk",
            branch="main",
            error="Connection refused",
            log_url="https://ci.example.com/123",
        )
        assert result["title"] == "Deploy FAILED: hermes-desk"
        assert "Connection refused" in result["body"]
        assert result["priority"] == 2
        assert result["requires_ack"] is True

    def test_periodic_checkin_render(self):
        result = PERIODIC_CHECKIN.render(
            task_count=5,
            nudge="You've been heads-down for 2 hours.",
        )
        assert result["title"] == "Quick check-in"
        assert "5" in result["body"]
        assert result["requires_ack"] is True

    def test_linear_task_assigned_render(self):
        result = LINEAR_TASK_ASSIGNED.render(
            issue_id="MOT-82",
            issue_title="Proactive notification pipeline",
            priority="High",
            project="Hermes Desk",
        )
        assert "MOT-82" in result["title"]
        assert "Hermes Desk" in result["body"]

    def test_template_category_preserved(self):
        assert DEPLOY_STARTED.category == "deploy"
        assert REMINDER.category == "reminder"
        assert SYSTEM_ALERT.category == "system"
        assert PERIODIC_CHECKIN.category == "checkin"
