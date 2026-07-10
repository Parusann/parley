# router_client.py
# Fire-and-forget bridge from Parley's assistant mode to the local Switchboard
# (POST /ask on 127.0.0.1:7780). The request runs on a daemon thread so a slow
# or absent Switchboard can never stall or crash the dictation pipeline.

import http.client
import json
import logging
import threading
import urllib.parse
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "http://127.0.0.1:7780/ask"


# Kick off the background POST and return immediately. Returns the worker
# thread (so tests can join it) or None when there is nothing to send.
# on_failure (optional, called on the worker thread) fires ONLY when the
# request was definitely dropped (connection failure) — not on a timeout,
# where Switchboard may still be processing and answering aloud.
def send(text: str, cfg: dict, on_failure=None) -> Optional[threading.Thread]:
    if not text or not text.strip():
        return None

    cfg = cfg or {}
    endpoint = cfg.get('endpoint') or DEFAULT_ENDPOINT
    try:
        timeout = float(cfg.get('timeout_seconds', 30))
    except (TypeError, ValueError):
        timeout = 30.0

    payload = {
        'text': text,
        'source': 'parley',
        'mode': 'assistant',
        # Local time with UTC offset — Switchboard logs it as-is.
        'ts': datetime.now().astimezone().isoformat(),
    }

    thread = threading.Thread(
        target=_post_request,
        args=(endpoint, payload, timeout, on_failure),
        daemon=True,
        name='router-send',
    )
    thread.start()
    return thread


# Fire-and-forget liveness ping so the Amadeus dashboard globe knows when
# the user is talking: POST /event {kind:'listening', state:'start'|'stop'}.
# Best-effort — a dropped ping just means the globe misses one state; it
# never blocks or crashes dictation. The /event URL is derived from the
# configured /ask endpoint so both track one setting.
def send_event(kind: str, state: str, cfg: dict) -> None:
    cfg = cfg or {}
    ask_endpoint = cfg.get('endpoint') or DEFAULT_ENDPOINT
    event_endpoint = ask_endpoint.rsplit('/', 1)[0] + '/event'
    payload = {'kind': kind, 'state': state}

    def _fire():
        parsed = urllib.parse.urlsplit(event_endpoint)
        try:
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=2)
            conn.request(
                'POST', parsed.path or '/event',
                body=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
            )
            conn.getresponse().read()
            conn.close()
        except Exception:
            pass  # the globe missing a state is not worth a log line

    threading.Thread(target=_fire, daemon=True, name='router-event').start()


# Runs entirely on the daemon thread. Every outcome — success, connection
# refused, timeout, non-JSON body — terminates here; nothing propagates back
# into the dictation pipeline.
def _post_request(endpoint: str, payload: dict, timeout: float, on_failure=None):
    # Two explicit phases because "timed out" means opposite things in each:
    # before the TCP connection exists the request never arrived (dropped);
    # after it was sent, Switchboard may still be thinking and speaking.
    parsed = urllib.parse.urlsplit(endpoint)
    path = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)

    try:
        # Phase 1 — connect. Refused, unreachable, or a connect-phase
        # timeout (Windows turns dead local ports into timeouts): the
        # request definitely never arrived, so make it audible.
        try:
            conn.connect()
        except Exception as e:
            logger.warning(f"Switchboard unreachable ({e}); assistant request dropped")
            if on_failure is not None:
                try:
                    on_failure()
                except Exception:
                    logger.exception("assistant on_failure callback failed")
            return

        # Phase 2 — send + read.
        try:
            conn.request(
                'POST', path,
                body=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
            )
            data = json.loads(conn.getresponse().read())
            reply = (data.get('reply') or '').strip() if isinstance(data, dict) else ''
            if reply:
                logger.info(f"Switchboard reply: {reply}")
            else:
                logger.info("Switchboard answered without reply text")
        except TimeoutError:
            logger.warning(
                f"Switchboard did not respond within {timeout}s — "
                "request was delivered and may still be processing"
            )
        except Exception as e:
            # Connection dropped mid-request: outcome genuinely unknown, so
            # log it but don't fire the failure signal (it may have worked).
            logger.warning(f"Switchboard connection lost mid-request ({e}); reply unknown")
    finally:
        conn.close()
