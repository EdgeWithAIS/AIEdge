"""Usage records, daily limits and admin settings, kept in one DynamoDB table.

Every server shares the table, so limits and the admin page stay right with
several servers (for example several ECS tasks).

The table's only key is the string ``UserQuestionDateTime``. Each kind of item
uses its own key format:

    <user id>#<UTC date and time>     one record per question, for example
                                      auth0|123#2026-11-06T15:30:00.123456Z
    COUNT#<user id>#<yyyy-mm-dd>      questions counted that day, for the limit
    SETTINGS                          the admin settings

The admin page reads one day of questions at a time through the ``by-day``
global secondary index (partition key ``day``, sort key ``at``). Only question
records have ``day``, so the index holds nothing else.

A question is counted *before* its answer is built, with a conditional update,
so two questions sent at the same moment cannot both slip past the daily
limit. A question that ends in an error is given back. Question records and
day counts delete themselves through DynamoDB's TTL (``expires_at``).

To create a matching table from code instead of the console:

    python3 -m disaster_aid.store --create-table
"""
from __future__ import annotations

import argparse
import os
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal

DEFAULT_TABLE = "disaster-aid-navigator"
KEY = "UserQuestionDateTime"
DAY_INDEX = "by-day"
SETTINGS_KEY = "SETTINGS"
KEEP_USAGE_DAYS = 180

# Claude Haiku 5.5 list prices, US dollars per million tokens (prompts up to 100K tokens).
DEFAULT_PRICE_INPUT = 0.10
DEFAULT_PRICE_OUTPUT = 0.50

MODEL_PATTERN = re.compile(r"claude-[a-z0-9][a-z0-9.\-]{0,60}")


@dataclass
class Settings:
    claude_enabled: bool = True
    model: str = "claude-haiku-5-5"
    daily_limit: int = 20
    price_input_per_mtok: float = DEFAULT_PRICE_INPUT
    price_output_per_mtok: float = DEFAULT_PRICE_OUTPUT
    # Agent mode (agent.py) for every chat question. On by default since its live eval set met the bar
    # (evals/run_agent_evals.py); the regular answers are the fallback when it is off or Claude is off.
    agent_enabled: bool = True

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price_input_per_mtok
                + output_tokens * self.price_output_per_mtok) / 1_000_000


def validate_settings(current: Settings, changes: dict) -> Settings:
    """Apply admin changes, or raise ValueError with a message the page can show."""
    if not isinstance(changes, dict):
        raise ValueError("Send the settings as JSON.")
    known = {f.name for f in fields(Settings)}
    unknown = set(changes) - known
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}.")
    new = asdict(current)
    if "claude_enabled" in changes:
        if not isinstance(changes["claude_enabled"], bool):
            raise ValueError("Claude answers must be on or off.")
        new["claude_enabled"] = changes["claude_enabled"]
    if "agent_enabled" in changes:
        if not isinstance(changes["agent_enabled"], bool):
            raise ValueError("Agent mode must be on or off.")
        new["agent_enabled"] = changes["agent_enabled"]
    if "model" in changes:
        model = str(changes["model"]).strip()
        if not MODEL_PATTERN.fullmatch(model):
            raise ValueError('The model must be a Claude model ID, for example "claude-haiku-5-5".')
        new["model"] = model
    if "daily_limit" in changes:
        limit = changes["daily_limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 10_000:
            raise ValueError("The daily limit must be a whole number from 0 to 10000.")
        new["daily_limit"] = limit
    for key in ("price_input_per_mtok", "price_output_per_mtok"):
        if key in changes:
            price = changes[key]
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not 0 <= price <= 1000:
                raise ValueError("Prices must be numbers from 0 to 1000 dollars per million tokens.")
            new[key] = float(price)
    return Settings(**new)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UsageStore:
    """Settings, daily limits and usage records in DynamoDB."""

    def __init__(self, table_name: str | None = None, region: str | None = None,
                 endpoint_url: str | None = None):
        import boto3

        self.table_name = table_name or os.environ.get("DISASTER_AID_TABLE") or DEFAULT_TABLE
        resource = boto3.resource("dynamodb", region_name=region or _region(),
                                  endpoint_url=endpoint_url or os.environ.get("DYNAMODB_ENDPOINT") or None)
        self.table = resource.Table(self.table_name)
        self._errors = resource.meta.client.exceptions

    # Settings -----------------------------------------------------------------

    def settings(self) -> Settings:
        item = self.table.get_item(Key={KEY: SETTINGS_KEY}, ConsistentRead=True).get("Item") or {}
        known = {f.name: f for f in fields(Settings)}
        return Settings(**{k: _plain(v, known[k].type) for k, v in item.items() if k in known})

    def seed_settings(self, settings: Settings) -> None:
        """Save first-run settings. Does nothing once settings have been saved."""
        try:
            self.table.put_item(Item=self._settings_item(settings),
                                ConditionExpression="attribute_not_exists(#k)",
                                ExpressionAttributeNames={"#k": KEY})
        except self._errors.ConditionalCheckFailedException:
            pass

    def save_settings(self, settings: Settings) -> None:
        self.table.put_item(Item=self._settings_item(settings))

    @staticmethod
    def _settings_item(settings: Settings) -> dict:
        item = {KEY: SETTINGS_KEY, "type": "settings"}
        for key, value in asdict(settings).items():
            item[key] = Decimal(str(value)) if isinstance(value, float) else value
        return item

    # Usage ----------------------------------------------------------------------

    @staticmethod
    def _count_key(sub: str, day: str) -> dict:
        return {KEY: f"COUNT#{sub}#{day}"}

    def start(self, *, sub: str, email: str | None, role: str, kind: str,
              limit: int | None, now: datetime) -> str | None:
        """Count a new question. Returns its record key, or None if the daily limit is used up.

        ``limit=None`` means no limit (admins).
        """
        day = now.date().isoformat()
        update = {
            "Key": self._count_key(sub, day),
            "UpdateExpression": "ADD #n :one SET #t = :type, expires_at = :expires",
            "ExpressionAttributeNames": {"#n": "count", "#t": "type"},
            "ExpressionAttributeValues": {":one": 1, ":type": "daily_count",
                                          ":expires": _epoch(now + timedelta(days=2))},
        }
        if limit is not None:
            update["ConditionExpression"] = "attribute_not_exists(#n) OR #n < :limit"
            update["ExpressionAttributeValues"][":limit"] = limit
        try:
            self.table.update_item(**update)
        except self._errors.ConditionalCheckFailedException:
            return None
        at = _stamp(now)
        while True:
            key = f"{sub}#{at}"
            item = {KEY: key, "type": "question", "day": day, "at": at, "sub": sub, "role": role,
                    "kind": kind, "status": "pending",
                    "expires_at": _epoch(now + timedelta(days=KEEP_USAGE_DAYS))}
            if email:
                item["email"] = email
            try:
                # Never overwrite: two questions from one person in the same microsecond get new times.
                self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(#k)",
                                    ExpressionAttributeNames={"#k": KEY})
                return key
            except self._errors.ConditionalCheckFailedException:
                now += timedelta(microseconds=1)
                at = _stamp(now)

    def finish(self, record_key: str, *, model: str | None, input_tokens: int = 0,
               output_tokens: int = 0, cost_usd: float = 0.0) -> None:
        values = {":ok": "ok", ":in": input_tokens, ":out": output_tokens,
                  ":cost": Decimal(str(round(cost_usd, 10)))}
        expression = "SET #s = :ok, input_tokens = :in, output_tokens = :out, cost_usd = :cost"
        if model:
            expression += ", model = :model"
            values[":model"] = model
        self.table.update_item(Key={KEY: record_key}, UpdateExpression=expression,
                               ExpressionAttributeNames={"#s": "status"},
                               ExpressionAttributeValues=values)

    def fail(self, record_key: str, error: str) -> None:
        """Mark a question as failed and give it back. Only an error *type* is stored."""
        self.table.update_item(Key={KEY: record_key},
                               UpdateExpression="SET #s = :error, #e = :type",
                               ExpressionAttributeNames={"#s": "status", "#e": "error"},
                               ExpressionAttributeValues={":error": "error", ":type": error[:80]})
        sub, at = record_key.rsplit("#", 1)
        try:
            self.table.update_item(Key=self._count_key(sub, at[:10]),
                                   UpdateExpression="ADD #n :minus",
                                   ConditionExpression="#n > :zero",
                                   ExpressionAttributeNames={"#n": "count"},
                                   ExpressionAttributeValues={":minus": -1, ":zero": 0})
        except self._errors.ConditionalCheckFailedException:
            pass

    def used_today(self, sub: str, now: datetime) -> int:
        item = self.table.get_item(Key=self._count_key(sub, now.date().isoformat()),
                                   ConsistentRead=True).get("Item") or {}
        return int(item.get("count", 0))

    def _records(self, day: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        items, start = [], None
        while True:
            kwargs = {"IndexName": DAY_INDEX, "KeyConditionExpression": Key("day").eq(day)}
            if start:
                kwargs["ExclusiveStartKey"] = start
            try:
                page = self.table.query(**kwargs)
            except self._errors.ClientError as err:
                if err.response.get("Error", {}).get("Code") != "ValidationException":
                    raise
                from .errors import PublicError
                raise PublicError(f'The DynamoDB table needs a global secondary index named "{DAY_INDEX}" '
                                  '(partition key "day", sort key "at", both strings). '
                                  "See the README.") from None
            items.extend(page.get("Items", []))
            start = page.get("LastEvaluatedKey")
            if not start:
                return items

    def summary(self, now: datetime, days: int = 14) -> dict:
        first = now.date() - timedelta(days=days - 1)
        by_day, users, errors = [], {}, []
        for offset in range(days):
            day = (now.date() - timedelta(days=offset)).isoformat()
            records = self._records(day)
            if not records:
                continue
            row = {"day": day, "questions": 0, "errors": 0, "claude_answers": 0,
                   "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            for r in records:
                tokens_in, tokens_out = int(r.get("input_tokens", 0)), int(r.get("output_tokens", 0))
                cost = float(r.get("cost_usd", 0))
                failed = r.get("status") == "error"
                row["errors" if failed else "questions"] += 1
                if r.get("model") and r["model"] not in ("rule", "template-baseline"):
                    row["claude_answers"] += 1
                row["input_tokens"] += tokens_in
                row["output_tokens"] += tokens_out
                row["cost_usd"] += cost
                user = users.setdefault(r["sub"], {"sub": r["sub"], "email": None, "role": None,
                                                   "questions": 0, "tokens": 0, "cost_usd": 0.0})
                user["email"] = user["email"] or r.get("email")
                user["role"] = user["role"] or r.get("role")
                user["questions"] += 0 if failed else 1
                user["tokens"] += tokens_in + tokens_out
                user["cost_usd"] += cost
                if failed:
                    errors.append({"at": r.get("at", ""), "email": r.get("email"),
                                   "kind": r.get("kind"), "error": r.get("error")})
            by_day.append(row)
        totals = {key: sum(r[key] for r in by_day)
                  for key in ("questions", "errors", "claude_answers", "input_tokens",
                              "output_tokens", "cost_usd")}
        top = sorted(users.values(), key=lambda u: (-u["cost_usd"], -u["questions"]))[:20]
        return {
            "days": days,
            "since": first.isoformat(),
            "today": now.date().isoformat(),
            "totals": totals,
            "by_day": by_day,
            "top_users": top,
            "recent_errors": sorted(errors, key=lambda e: e["at"], reverse=True)[:20],
        }


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def _stamp(when: datetime) -> str:
    """UTC date and time to the microsecond, for example 2026-11-06T15:30:00.123456Z."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _epoch(when: datetime) -> int:
    return int(when.timestamp())


def _plain(value, kind):
    """DynamoDB returns numbers as Decimal; turn them back into the setting's type."""
    if isinstance(value, Decimal):
        return int(value) if kind in (int, "int") else float(value)
    return value


def create_table(table_name: str | None = None, region: str | None = None,
                 endpoint_url: str | None = None, wait: bool = True) -> str:
    """Create the on-demand table, its by-day index and TTL. Returns its name."""
    import boto3

    name = table_name or os.environ.get("DISASTER_AID_TABLE") or DEFAULT_TABLE
    client = boto3.client("dynamodb", region_name=region or _region(),
                          endpoint_url=endpoint_url or os.environ.get("DYNAMODB_ENDPOINT") or None)
    client.create_table(
        TableName=name,
        AttributeDefinitions=[{"AttributeName": KEY, "AttributeType": "S"},
                              {"AttributeName": "day", "AttributeType": "S"},
                              {"AttributeName": "at", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": KEY, "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[{
            "IndexName": DAY_INDEX,
            "KeySchema": [{"AttributeName": "day", "KeyType": "HASH"},
                          {"AttributeName": "at", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
        DeletionProtectionEnabled=True,
        Tags=[{"Key": "app", "Value": "disaster-aid-navigator"}],
    )
    if wait:
        client.get_waiter("table_exists").wait(TableName=name)
    client.update_time_to_live(TableName=name, TimeToLiveSpecification={
        "Enabled": True, "AttributeName": "expires_at"})
    return name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Disaster Aid Navigator DynamoDB table")
    parser.add_argument("--create-table", action="store_true", help="Create the table (once)")
    parser.add_argument("--table", help=f"Table name (default: DISASTER_AID_TABLE or {DEFAULT_TABLE})")
    parser.add_argument("--region", help="AWS region (default: AWS_REGION or us-east-1)")
    args = parser.parse_args(argv)
    if not args.create_table:
        parser.error("Nothing to do. Use --create-table.")
    name = create_table(args.table, args.region)
    print(f"Created table {name} in {args.region or _region()} (on-demand, by-day index, TTL on expires_at).")


if __name__ == "__main__":
    main()
