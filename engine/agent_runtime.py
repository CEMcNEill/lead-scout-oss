"""Entrypoints the orchestration skill calls.

This is the agent-plus-MCP runtime: a Claude Code skill drives the loop, calling
the connected MCPs for Clay, Gmail, and Slack, and these subcommands for the
deterministic and grounded work. Salesforce and usage stay headless (the `sf`
CLI), so the agent only needs MCPs for enrichment and the two human surfaces.

Subcommands (JSON to stdout unless noted):
  poll                          new task ids + enrichment targets (domain, name)
  process --task ID [--clay-company F] [--clay-contact F]
                                qualify the lead (SF + usage headless, Clay from
                                the agent's files), write the ledger, print the
                                draft to stage
  card --task ID --draft-url U  render the Slack card + reasoning with the draft
                                link, store the ref on the ledger
  set-thread --task ID --ts TS  record the Slack thread ref for the slow loop
  status [--json]               liveness + activity, from the heartbeat and ledger
  heartbeat --start TS --exit N stamp ledger/heartbeat.json after a sweep and print
                                a one-line notification body (the wrapper's helper)

The shell runs with null staging/notify here; the agent performs the actual Gmail
and Slack writes via MCP using this output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from engine.clay import RecordedClayCaller, name_from_email
from engine.env import load_dotenv
from engine.ledger import Ledger
from engine.providers import CompositeToolProvider, NullNotifier, NullStagingSink
from engine.service import (
    REPO_ROOT,
    _load_exemplar_bank,
    build_sf_client,
    default_rep_config,
    voice_profile_path,
)
from shared.contracts import TriggerMeta, TriggerSource


def _ledger() -> Ledger:
    import os

    return Ledger(os.environ.get("LEDGER_PATH", str(REPO_ROOT / "ledger" / "lead_runs.db")))


def _crm_and_usage(client):
    from engine.salesforce import SalesforceCrmFetcher, SfFieldMap
    from engine.usage import SalesforceUsageFetcher
    import os

    field_map = SfFieldMap(
        trigger_task_field=os.environ.get("SF_TRIGGER_TASK_FIELD") or None,
        trigger_contact_field=os.environ.get("SF_TRIGGER_CONTACT_FIELD") or None,
        account_ref_contact_field=os.environ.get("SF_ACCOUNT_REF_CONTACT_FIELD") or None,
    )
    return SalesforceCrmFetcher(client, field_map=field_map), SalesforceUsageFetcher(client)


def _enrich_name(lead: dict[str, Any]) -> str:
    name = lead.get("name")
    if name and "@" not in name and not name.startswith("-") and " " in name:
        return name
    return name_from_email(lead.get("email")) or (name or "")


# --- poll -----------------------------------------------------------------


def poll_targets(client, ledger, rep_config) -> list[dict[str, Any]]:
    from engine.salesforce import SalesforceTaskSource
    import os

    crm, _ = _crm_and_usage(client)
    source = SalesforceTaskSource(
        client,
        status=os.environ.get("SF_LEAD_TASK_STATUS", "Open"),
        extra_where=os.environ.get("SF_LEAD_TASK_FILTER") or None,
    )
    targets = []
    for task_id in source.poll(rep_config):
        if ledger.has_task(task_id):
            continue
        record = crm.read(task_id)
        lead = record.get("lead", {})
        targets.append({
            "task_id": task_id,
            "company_domain": lead.get("domain"),
            "contact_name": _enrich_name(lead),
            "contact_email": lead.get("email"),
        })
    return targets


# --- process --------------------------------------------------------------


def _clay_caller(record: dict[str, Any], company_file: str | None, contact_file: str | None):
    lead = record.get("lead", {})
    domain = lead.get("domain") or ""
    name = _enrich_name(lead)
    companies, contacts = {}, {}
    if company_file and domain:
        companies[domain] = json.loads(Path(company_file).read_text())
    if contact_file and name and domain:
        contacts[(name, domain)] = json.loads(Path(contact_file).read_text())
    return RecordedClayCaller(companies=companies, contacts=contacts)


def process_one(
    task_id, *, client, ledger, model, company_file=None, contact_file=None,
    repo_root: Path = REPO_ROOT,
):
    from engine.clay import ClayCompanyFetcher, ClayPersonFetcher
    from engine.service import assemble_shell

    crm, usage = _crm_and_usage(client)
    record = crm.read(task_id)
    caller = _clay_caller(record, company_file, contact_file)
    voice = voice_profile_path(repo_root).read_text()
    provider = CompositeToolProvider(
        crm_fetcher=crm,
        person_fetcher=ClayPersonFetcher(caller),
        company_fetcher=ClayCompanyFetcher(caller),
        usage_fetcher=usage,
        voice_profile=voice,
        exemplar_bank=_load_exemplar_bank(repo_root / "config" / "exemplars.json"),
    )
    shell = assemble_shell(
        ledger=ledger, inner_model=model, tool_provider=provider,
        staging_sink=NullStagingSink(), notifier=NullNotifier(),
    )
    return shell.process_lead_run(task_id, default_rep_config(), TriggerMeta(TriggerSource.BATCH, ""))


def _run_summary(run) -> dict[str, Any]:
    draft = run.staged_draft
    return {
        "run_id": run.id,
        "task_id": run.task_id,
        "status": run.status.value,
        "route": run.route.qualifier,
        "disposition": run.disposition.disposition.value if run.disposition else None,
        "hard_stops": run.hard_stops,
        "draft": ({"to": draft.to, "subject": draft.subject, "body": draft.body}
                  if draft else None),
    }


# --- card + thread --------------------------------------------------------


def render_card(ledger, task_id, draft_url: str | None) -> dict[str, Any]:
    from engine.slack import build_card, build_reasoning
    import os

    run = ledger.get_by_task(task_id)
    if run is None:
        raise KeyError(f"no ledger run for task {task_id}")
    if draft_url:
        run.staged_draft_ref = draft_url
        ledger.update(run)
    base = os.environ.get("SF_INSTANCE_URL")
    return {"card": build_card(run), "reasoning": build_reasoning(run, sf_account_base=base)}


def set_thread(ledger, task_id, ts: str) -> None:
    run = ledger.get_by_task(task_id)
    if run is None:
        raise KeyError(f"no ledger run for task {task_id}")
    run.slack_thread_ref = ts
    ledger.update(run)


# --- voice calibration corpus --------------------------------------------


def voice_corpus(ledger, rep_config) -> list[dict[str, Any]]:
    """The staged-draft corpus the voice-calibration skill reads (our SQLite
    ledger is the source; this is the drafts-ledger the skill expects). One row
    per staged draft: the body as staged (pre-edit) plus segment hints, so the
    scan can diff sent-vs-staged and slice efficacy by segment."""
    from shared.contracts import RunStatus

    def claim(run, field):
        return next((c.value for c in run.dossier if c.field == field), None)

    rows = []
    for run in ledger.list_runs(rep_id=rep_config.rep_id):
        if run.status != RunStatus.STAGED_FOR_REVIEW or run.staged_draft is None:
            continue
        d = run.staged_draft
        target = run.disposition.target if run.disposition else None
        rows.append({
            "ts": run.ts, "task": run.task_id, "play": run.route.lead_type,
            "email": d.to, "contactName": target.name if target else None,
            "title": claim(run, "contact_title") or claim(run, "seniority"),
            "company": claim(run, "company_name"), "segment": claim(run, "segment"),
            "subject": d.subject, "stagedBody": d.body, "draftRef": run.staged_draft_ref,
        })
    return rows


# --- nightly slow loop ----------------------------------------------------


def slow_targets(ledger, rep_config) -> list[dict[str, Any]]:
    """What the nightly skill must fetch: for staged runs, the sent item to diff
    (by recipient + subject); for runs with a Slack thread, the thread to read."""
    from shared.contracts import RunStatus

    targets = []
    for run in ledger.list_runs(rep_id=rep_config.rep_id):
        item: dict[str, Any] = {"task_id": run.task_id}
        if run.status == RunStatus.STAGED_FOR_REVIEW and run.staged_draft:
            item["draft_to"] = run.staged_draft.to
            item["draft_subject"] = run.staged_draft.subject
        if run.slack_thread_ref:
            item["slack_thread_ref"] = run.slack_thread_ref
        if len(item) > 1:
            targets.append(item)
    return targets


def slow_run(ledger, data: dict[str, Any], *, client, model, rep_config, stamp,
             updates_only: bool = False, no_voice: bool = False,
             repo_root: Path = REPO_ROOT):
    """Run the slow loop over agent-fetched Gmail sent items + Slack threads.
    `data` = {"sent": {recipient: [msg...]}, "threads": {ts: [msg...]}}.
    updates_only=True does the light 5-minute update check (corrections + recorded
    overrides, no voice, no proposals). no_voice=True keeps judgment learning +
    corrections but leaves voice to the voice-calibration skill."""
    from engine.gmail import GmailMessage, RecordedGmailClient
    from engine.loop_slow import SlowLoop
    from engine.slack import RecordedSlackClient

    sent = {
        recipient: [
            GmailMessage(
                id=m.get("id", ""), thread_id=m.get("thread_id", ""),
                subject=m.get("subject", ""), to=m.get("to", recipient),
                body=m.get("body", ""), date=m.get("date", ""),
            )
            for m in msgs
        ]
        for recipient, msgs in data.get("sent", {}).items()
    }
    loop = SlowLoop(
        ledger=ledger,
        gmail=RecordedGmailClient(sent=sent),
        slack=RecordedSlackClient(thread_messages=data.get("threads", {})),
        model=model, rep_config=rep_config,
        proposals_dir=repo_root / "config" / "proposals",
        voice_profile_path=voice_profile_path(repo_root),
        rubric_path=repo_root / "config" / "rubric.md",
        sf_writer=client,
    )
    if updates_only:
        return loop.run_updates_only(stamp)
    return loop.run(stamp, do_voice=not no_voice, do_proposals=True)


# --- heartbeat + status ---------------------------------------------------


def _heartbeat_path(ledger) -> Path:
    p = Path(ledger.path)
    if p.name in ("", ":memory:"):
        return Path("heartbeat.json")
    return p.parent / "heartbeat.json"


def _window_counts(ledger, since_iso: str | None, rep_config) -> dict[str, int]:
    """Counts over runs with ts >= since_iso (all runs if None). ts is UTC ISO, so
    a string compare is a time compare."""
    from shared.contracts import RunStatus

    runs = ledger.list_runs(rep_id=rep_config.rep_id)
    if since_iso:
        runs = [r for r in runs if r.ts >= since_iso]
    return {
        "processed": len(runs),
        "staged": sum(1 for r in runs if r.status == RunStatus.STAGED_FOR_REVIEW),
        "blocked": sum(1 for r in runs if r.status == RunStatus.BLOCKED),
        "errors": sum(1 for r in runs if r.status == RunStatus.ERROR),
    }


def write_heartbeat(ledger, rep_config, start_iso: str, exit_code: int) -> dict[str, Any]:
    """Stamp ledger/heartbeat.json after a sweep: liveness (start, finish, exit)
    plus this run's activity (rows written since start). status and any indicator
    read this file; the sweep wrapper calls it once per run."""
    import datetime as dt

    counts = _window_counts(ledger, start_iso, rep_config)
    hb = {
        "last_start": start_iso,
        "last_finish": dt.datetime.now(dt.timezone.utc).isoformat(),
        "exit_code": exit_code,
        "ok": exit_code == 0,
        **counts,
    }
    _heartbeat_path(ledger).write_text(json.dumps(hb, indent=2))
    return hb


def _summary_line(counts: dict[str, int]) -> str:
    parts = [f"processed {counts['processed']}", f"staged {counts['staged']}"]
    if counts["blocked"]:
        parts.append(f"blocked {counts['blocked']}")
    if counts["errors"]:
        parts.append(f"errors {counts['errors']}")
    return ", ".join(parts)


def heartbeat_message(hb: dict[str, Any]) -> str:
    """The desktop-notification body for one sweep, or '' to stay silent. Failures
    always speak; a successful sweep speaks only when it actually did something."""
    if not hb["ok"]:
        tail = _summary_line(hb) if hb["processed"] else "see ledger/agent.err.log"
        return f"Sweep failed (exit {hb['exit_code']}); {tail}"
    return _summary_line(hb) if hb["processed"] else ""


def _ago(iso: str) -> str:
    import datetime as dt

    try:
        then = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    secs = int((dt.datetime.now(dt.timezone.utc) - then).total_seconds())
    if secs < 90:
        return f"{secs}s ago"
    if secs < 5400:
        return f"{secs // 60} min ago"
    if secs < 172800:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _local(iso: str) -> str:
    import datetime as dt

    try:
        return dt.datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def status_report(ledger, rep_config) -> dict[str, Any]:
    hp = _heartbeat_path(ledger)
    hb = json.loads(hp.read_text()) if hp.exists() else None
    runs = ledger.list_runs(rep_id=rep_config.rep_id)
    return {
        "heartbeat": hb,
        "ledger_runs_total": len(runs),
        "ledger_last_run_ts": runs[-1].ts if runs else None,
    }


def render_status(report: dict[str, Any]) -> str:
    hb = report["heartbeat"]
    lines = ["lead-scout status"]
    if hb is None:
        lines.append("  scheduler: no sweep recorded yet (heartbeat missing)")
    else:
        state = "ok" if hb.get("ok") else f"FAILED (exit {hb.get('exit_code')})"
        lines.append(
            f"  last sweep: {_local(hb['last_finish'])} ({_ago(hb['last_finish'])}), {state}"
        )
        lines.append(f"  last sweep activity: {_summary_line(hb)}")
    last = report["ledger_last_run_ts"]
    total = report["ledger_runs_total"]
    if last:
        lines.append(f"  ledger: {total} runs total, most recent {_local(last)} ({_ago(last)})")
    else:
        lines.append(f"  ledger: {total} runs total")
    lines.append("  scheduler job: launchctl list | grep lead-agent")
    return "\n".join(lines)


# --- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    from engine.anthropic_model import AnthropicModel
    from engine.cost import ModelPolicy

    load_dotenv()
    parser = argparse.ArgumentParser(prog="engine.agent_runtime")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("poll")
    p = sub.add_parser("process")
    p.add_argument("--task", required=True)
    p.add_argument("--clay-company")
    p.add_argument("--clay-contact")
    c = sub.add_parser("card")
    c.add_argument("--task", required=True)
    c.add_argument("--draft-url")
    t = sub.add_parser("set-thread")
    t.add_argument("--task", required=True)
    t.add_argument("--ts", required=True)
    sub.add_parser("slow-targets")
    sub.add_parser("voice-corpus")
    sr = sub.add_parser("slow-run")
    sr.add_argument("--data", required=True)
    sr.add_argument("--updates-only", action="store_true",
                    help="light update check: corrections + overrides, no proposals")
    sr.add_argument("--no-voice", action="store_true",
                    help="judgment + corrections only; voice is left to voice-calibration")
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    hb = sub.add_parser("heartbeat")
    hb.add_argument("--start", required=True)
    hb.add_argument("--exit", dest="exit_code", type=int, required=True)
    args = parser.parse_args(argv)

    ledger = _ledger()
    try:
        if args.cmd == "poll":
            client = build_sf_client()
            print(json.dumps(poll_targets(client, ledger, default_rep_config()), indent=2))
        elif args.cmd == "process":
            client = build_sf_client()
            run = process_one(
                args.task, client=client, ledger=ledger, model=AnthropicModel(ModelPolicy()),
                company_file=args.clay_company, contact_file=args.clay_contact,
            )
            print(json.dumps(_run_summary(run), indent=2))
        elif args.cmd == "card":
            print(json.dumps(render_card(ledger, args.task, args.draft_url), indent=2))
        elif args.cmd == "set-thread":
            set_thread(ledger, args.task, args.ts)
            print(json.dumps({"ok": True}))
        elif args.cmd == "slow-targets":
            print(json.dumps(slow_targets(ledger, default_rep_config()), indent=2))
        elif args.cmd == "voice-corpus":
            print(json.dumps(voice_corpus(ledger, default_rep_config()), indent=2))
        elif args.cmd == "slow-run":
            import datetime as _dt

            data = json.loads(Path(args.data).read_text())
            result = slow_run(
                ledger, data, client=build_sf_client(),
                model=AnthropicModel(ModelPolicy()), rep_config=default_rep_config(),
                stamp=_dt.datetime.now().strftime("%Y-%m-%d"),
                updates_only=args.updates_only, no_voice=args.no_voice,
            )
            print(json.dumps({
                "voice_edits": len(result.voice_edits),
                "disagreements": len(result.disagreements),
                "crm_dispositions": result.crm_dispositions,
                "account_corrections": len(result.account_corrections),
                "voice_proposal": result.voice_proposal_path,
                "rubric_proposal": result.rubric_proposal_path,
                "acknowledgements": result.acknowledgements,
            }, indent=2))
        elif args.cmd == "status":
            report = status_report(ledger, default_rep_config())
            print(json.dumps(report, indent=2) if args.json else render_status(report))
        elif args.cmd == "heartbeat":
            hb = write_heartbeat(ledger, default_rep_config(), args.start, args.exit_code)
            msg = heartbeat_message(hb)
            if msg:
                print(msg)
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
