from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from taroai.triggers.models import TriggerDefinition, TriggerStatus, TriggerType
from taroai.workers.models import JobType, TriggerDueJob


class TriggerScheduleEvaluation(BaseModel):
    job_type: JobType = JobType.TRIGGER_DUE
    due_jobs: list[TriggerDueJob] = Field(default_factory=list)
    next_run_at: datetime | None = None


class CronSchedule(BaseModel):
    minutes: set[int]
    hours: set[int]
    days_of_month: set[int]
    months: set[int]
    days_of_week: set[int]
    day_of_month_restricted: bool
    day_of_week_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("cron expression must contain 5 fields")

        days_of_month, day_of_month_restricted = _parse_cron_field(
            fields[2],
            minimum=1,
            maximum=31,
        )
        days_of_week, day_of_week_restricted = _parse_cron_field(
            fields[4],
            minimum=0,
            maximum=7,
        )
        normalized_days_of_week = {0 if value == 7 else value for value in days_of_week}

        return cls(
            minutes=_parse_cron_field(fields[0], minimum=0, maximum=59)[0],
            hours=_parse_cron_field(fields[1], minimum=0, maximum=23)[0],
            days_of_month=days_of_month,
            months=_parse_cron_field(fields[3], minimum=1, maximum=12)[0],
            days_of_week=normalized_days_of_week,
            day_of_month_restricted=day_of_month_restricted,
            day_of_week_restricted=day_of_week_restricted,
        )

    def next_after(self, value: datetime, schedule_timezone: ZoneInfo) -> datetime:
        local_value = _ensure_aware(value).astimezone(schedule_timezone)
        candidate = local_value.replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = candidate + timedelta(days=366 * 5)

        while candidate <= end:
            if self.matches(candidate):
                return candidate.astimezone(timezone.utc)
            candidate += timedelta(minutes=1)

        raise ValueError("no cron occurrence found within 5 years")

    def next_at_or_after(self, value: datetime, schedule_timezone: ZoneInfo) -> datetime:
        normalized_value = _ensure_aware(value)
        candidate = self.next_after(normalized_value - timedelta(minutes=1), schedule_timezone)
        if candidate < normalized_value:
            return self.next_after(normalized_value, schedule_timezone)
        return candidate

    def matches(self, value: datetime) -> bool:
        cron_weekday = (value.weekday() + 1) % 7
        day_of_month_matches = value.day in self.days_of_month
        day_of_week_matches = cron_weekday in self.days_of_week
        if self.day_of_month_restricted and self.day_of_week_restricted:
            day_matches = day_of_month_matches or day_of_week_matches
        else:
            day_matches = day_of_month_matches and day_of_week_matches

        return (
            value.minute in self.minutes
            and value.hour in self.hours
            and value.month in self.months
            and day_matches
        )


def evaluate_trigger_schedule(
    trigger: TriggerDefinition,
    now: datetime,
) -> TriggerScheduleEvaluation:
    if trigger.type != TriggerType.SCHEDULE or trigger.schedule is None:
        return TriggerScheduleEvaluation(next_run_at=trigger.next_run_at)

    if trigger.status == TriggerStatus.DISABLED:
        return TriggerScheduleEvaluation(next_run_at=trigger.next_run_at)

    schedule = trigger.schedule
    now_utc = _ensure_aware(now)
    if schedule.ends_at is not None and now_utc > _ensure_aware(schedule.ends_at):
        return TriggerScheduleEvaluation(next_run_at=None)

    schedule_timezone = ZoneInfo(schedule.timezone)
    cron = CronSchedule.parse(schedule.cron_expression)
    next_run_at = _initial_next_run_at(trigger, now_utc, cron, schedule_timezone)
    due_jobs: list[TriggerDueJob] = []

    while next_run_at <= now_utc and len(due_jobs) < schedule.max_catch_up_runs:
        if schedule.ends_at is not None and next_run_at > _ensure_aware(schedule.ends_at):
            return TriggerScheduleEvaluation(due_jobs=due_jobs, next_run_at=None)
        due_jobs.append(_build_due_job(trigger, scheduled_for=next_run_at))
        next_run_at = cron.next_after(next_run_at, schedule_timezone)

    if schedule.ends_at is not None and next_run_at > _ensure_aware(schedule.ends_at):
        next_run_at = None

    return TriggerScheduleEvaluation(due_jobs=due_jobs, next_run_at=next_run_at)


def _initial_next_run_at(
    trigger: TriggerDefinition,
    now: datetime,
    cron: CronSchedule,
    schedule_timezone: ZoneInfo,
) -> datetime:
    if trigger.next_run_at is not None:
        return _ensure_aware(trigger.next_run_at)

    schedule = trigger.schedule
    starts_at = schedule.starts_at if schedule is not None else None
    base_time = _ensure_aware(starts_at) if starts_at is not None else now
    return cron.next_at_or_after(base_time, schedule_timezone)


def _build_due_job(
    trigger: TriggerDefinition,
    scheduled_for: datetime,
) -> TriggerDueJob:
    requested_by_user_id = trigger.service_account_id or trigger.created_by_user_id
    if requested_by_user_id is None:
        raise ValueError(f"trigger {trigger.id} has no accountable identity")

    return TriggerDueJob(
        tenant_id=trigger.tenant_id,
        workspace_id=trigger.workspace_id,
        trigger_id=trigger.id,
        trigger_type=trigger.type.value,
        scheduled_for=scheduled_for,
        requested_by_user_id=requested_by_user_id,
    )


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_cron_field(
    raw_field: str,
    minimum: int,
    maximum: int,
) -> tuple[set[int], bool]:
    if raw_field == "*":
        return set(range(minimum, maximum + 1)), False

    values: set[int] = set()
    for part in raw_field.split(","):
        values.update(_parse_cron_part(part, minimum=minimum, maximum=maximum))

    return values, True


def _parse_cron_part(part: str, minimum: int, maximum: int) -> set[int]:
    range_part = part
    step = 1
    if "/" in part:
        range_part, step_part = part.split("/", maxsplit=1)
        step = int(step_part)
        if step < 1:
            raise ValueError("cron step must be greater than 0")

    if range_part == "*":
        start = minimum
        end = maximum
    elif "-" in range_part:
        start_part, end_part = range_part.split("-", maxsplit=1)
        start = int(start_part)
        end = int(end_part)
    else:
        start = int(range_part)
        end = start

    if start < minimum or end > maximum or start > end:
        raise ValueError("cron field value is outside supported range")

    return set(range(start, end + 1, step))
