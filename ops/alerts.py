"""Alert channel (ntfy — DECISIONS.md 2026-07-17 Stage 3) + the loud-failure

wrapper every ops entrypoint uses. Failures are never silent: they alert AND
exit non-zero. If the alert itself fails, the error still reaches stderr and
the journal (systemd captures it) — degraded, not silent.
"""

import os
import sys
import traceback
from typing import Callable

import httpx
from dotenv import load_dotenv

from config.loader import CONFIG

load_dotenv()

NTFY_BASE = CONFIG.get("stage3", {}).get("alerts", {}).get("base_url", "https://ntfy.sh")


def notify(title: str, message: str, priority: str = "default", tags: str = "") -> bool:
    """Send a push alert. priority: min|low|default|high|urgent."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[alert:no-topic] {title}: {message}", file=sys.stderr)
        return False
    try:
        httpx.post(
            f"{NTFY_BASE}/{topic}",
            content=message.encode(),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            timeout=15,
        ).raise_for_status()
        return True
    except httpx.HTTPError as e:
        print(f"[alert:send-failed {e}] {title}: {message}", file=sys.stderr)
        return False


def loud(job_name: str) -> Callable:
    """Decorator: any exception → urgent alert + non-zero exit. Loud, never

    silent (CLAUDE.md / Gate C)."""

    def wrap(fn: Callable) -> Callable:
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except SystemExit:
                raise
            except Exception as e:
                notify(
                    f"{job_name} FAILED",
                    f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}",
                    priority="urgent",
                    tags="rotating_light",
                )
                print(f"[{job_name}] FAILED: {e}", file=sys.stderr)
                traceback.print_exc()
                sys.exit(1)

        return inner

    return wrap
