#!/usr/bin/env python3
"""Probe which ADD_SOURCE(url) wire shape the live NotebookLM backend accepts.

Background
----------
notebooklm-py 0.8.0 sends one static payload for ADD_SOURCE(url):

    [[[None, None, [url], None, ..., 1]], notebook_id, [2, None, None, [1, ..., [1]]]]

Its own docstring (``notebooklm/_source/add.py``) notes that shape was
"verified live against an un-migrated account", and that a backend which
disagrees rejects the payload with ``status=5``/``9`` before fetching the URL.
Our pipeline now gets exactly that: ``RPCError rpc_code=9`` in ~0.7s for every
URL, across unrelated publisher domains, while create/list/use/delete all
succeed on the same cookies.

This script does not guess in production. It creates one scratch notebook,
replays a matrix of candidate payloads against the real RPC, records which
(if any) the backend accepts, then deletes the notebook.

Usage
-----
    python scripts/notebooklm_wire_probe.py
    python scripts/notebooklm_wire_probe.py --storage /path/to/storage_state.json

Auth must be live. Verify first with ``notebooklm auth check --test``; a jar
holding only ``notebooklm.google.com`` cookies (the pre-rebrand host) will
fail every candidate for reasons that have nothing to do with wire shape.

The probe URL is deliberately a plain, public, non-paywalled page: if a
candidate fails on it, the failure is the payload, not the source.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from notebooklm import NotebookLMClient
from notebooklm.exceptions import RPCError
from notebooklm.rpc import RPCMethod

# Public, fast, definitely-parseable. Not a paper — this probe tests the
# envelope, not NotebookLM's ability to fetch a publisher PDF.
PROBE_URLS = [
    "https://en.wikipedia.org/wiki/Water",
    "https://en.wikipedia.org/wiki/Water_quality",
    "https://en.wikipedia.org/wiki/Drinking_water",
    "https://en.wikipedia.org/wiki/Groundwater",
    "https://en.wikipedia.org/wiki/Wastewater",
    "https://en.wikipedia.org/wiki/Water_treatment",
    "https://en.wikipedia.org/wiki/Aquifer",
    "https://en.wikipedia.org/wiki/Surface_water",
    "https://en.wikipedia.org/wiki/Water_pollution",
    "https://en.wikipedia.org/wiki/Hydrology",
]

NESTED_TAIL = [2, None, None, [1, None, None, None, None, None, None, None, None, None, [1]]]
BARE_CONTEXT = [1, None, None, None, None, None, None, None, None, None, [1]]


def _spec(url: str, *, length: int, trailing: Any) -> list[Any]:
    """Build a source spec: url at slot 2, padded to `length`, optional tail value."""
    spec: list[Any] = [None, None, [url]]
    spec += [None] * max(0, length - len(spec))
    if trailing is not None:
        spec.append(trailing)
    return spec


def candidates(url: str, notebook_id: str) -> list[tuple[str, list[Any], str]]:
    """(name, params, source_path) for each shape worth testing.

    Axes: the source spec (slot 0), the trailing template block, and the
    referer path the RPC is issued under — the rebrand moved the host, so the
    route the backend expects is itself a suspect.
    """
    nb_path = f"/notebook/{notebook_id}"
    return [
        # --- baseline: exactly what 0.8.0 ships. Expected to fail with code 9.
        (
            "0.8.0-baseline",
            [[_spec(url, length=10, trailing=1)], notebook_id, NESTED_TAIL],
            nb_path,
        ),
        # --- pre-#1546 flat shape, in case this account is on an older cohort
        #     and 0.8.0's nested block is what it actually rejects.
        (
            "legacy-flat",
            [[_spec(url, length=3, trailing=None)], notebook_id, [2], None, None],
            nb_path,
        ),
        # --- spec-length / trailing-code variations around the 0.8.0 shape.
        (
            "spec10-no-trailing",
            [[_spec(url, length=10, trailing=None)], notebook_id, NESTED_TAIL],
            nb_path,
        ),
        (
            "spec11-trailing-2",
            [[_spec(url, length=10, trailing=2)], notebook_id, NESTED_TAIL],
            nb_path,
        ),
        (
            "spec12-trailing-1-pad",
            [[_spec(url, length=11, trailing=1)], notebook_id, NESTED_TAIL],
            nb_path,
        ),
        # --- tail variations holding the 0.8.0 spec fixed.
        (
            "bare-context-tail",
            [[_spec(url, length=10, trailing=1)], notebook_id, BARE_CONTEXT],
            nb_path,
        ),
        (
            "no-tail",
            [[_spec(url, length=10, trailing=1)], notebook_id],
            nb_path,
        ),
        (
            "null-tail",
            [[_spec(url, length=10, trailing=1)], notebook_id, None],
            nb_path,
        ),
        # --- referer-path variations: the host moved, so the route may have too.
        (
            "baseline@root-path",
            [[_spec(url, length=10, trailing=1)], notebook_id, NESTED_TAIL],
            "/",
        ),
        (
            "baseline@app-path",
            [[_spec(url, length=10, trailing=1)], notebook_id, NESTED_TAIL],
            f"/app/notebook/{notebook_id}",
        ),
    ]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storage", default=None, help="path to storage_state.json")
    ap.add_argument("--profile", default=None, help="notebooklm profile name")
    ap.add_argument("--keep", action="store_true", help="do not delete the scratch notebook")
    ap.add_argument(
        "--payload-file",
        default=None,
        help=(
            "JSON file holding a payload captured from the notebook.google.com "
            "web UI (DevTools > Network > izAoDd > Payload, the f.req inner array). "
            "Use the literal tokens __URL__ and __NOTEBOOK_ID__ where the capture "
            "has its own; they are substituted before sending. Tests only this "
            "payload instead of the built-in matrix."
        ),
    )
    ap.add_argument(
        "--urls",
        default=None,
        help=(
            "Comma-separated URLs (or a @file of one URL per line). Holds the "
            "0.8.0 payload fixed and varies the URL instead — use this to tell a "
            "rejected wire shape apart from a rejected source."
        ),
    )
    args = ap.parse_args()

    url_list: list[str] | None = None
    if args.urls:
        if args.urls.startswith("@"):
            url_list = [
                ln.strip()
                for ln in Path(args.urls[1:]).read_text().splitlines()
                if ln.strip()
            ]
        else:
            url_list = [u.strip() for u in args.urls.split(",") if u.strip()]

    captured: list[Any] | None = None
    if args.payload_file:
        raw = Path(args.payload_file).read_text()
        captured = json.loads(
            raw.replace("__URL__", PROBE_URLS[0]).replace("__NOTEBOOK_ID__", "__NB__")
        )

    async with NotebookLMClient.from_storage(
        path=args.storage, profile=args.profile
    ) as client:
        executor = client._rpc_executor  # source_path/variant aren't on the public wrapper

        nb = await client.notebooks.create("wire-probe (safe to delete)")
        notebook_id = getattr(nb, "id", None) or str(nb)
        print(f"scratch notebook: {notebook_id}\n")

        results: list[tuple[str, str, Any]] = []
        try:
            if url_list is not None:
                # One shape (0.8.0's, the one the pipeline actually sends),
                # many URLs. Isolates source rejection from shape rejection.
                plan = [
                    (u, candidates(u, notebook_id)[0][1], f"/notebook/{notebook_id}")
                    for u in url_list
                ]
            elif captured is not None:
                plan = [(
                    "captured",
                    json.loads(json.dumps(captured).replace("__NB__", notebook_id)),
                    f"/notebook/{notebook_id}",
                )]
            else:
                # Each candidate gets its own URL so an accepted add can't dedupe
                # the next one via the idempotency probe.
                plan = [
                    candidates(PROBE_URLS[i % len(PROBE_URLS)], notebook_id)[i]
                    for i in range(len(candidates("", notebook_id)))
                ]

            for name, params, source_path in plan:

                print(f"[{name}] path={source_path}")
                print(f"  params={json.dumps(params)[:160]}")
                try:
                    result = await executor.rpc_call(
                        method=RPCMethod.ADD_SOURCE,
                        params=params,
                        source_path=source_path,
                        disable_internal_retries=True,
                    )
                    print("  -> ACCEPTED\n")
                    results.append((name, "ACCEPTED", str(result)[:120]))
                except RPCError as e:
                    print(f"  -> rpc_code={e.rpc_code} {str(e)[:110]}\n")
                    results.append((name, f"rpc_code={e.rpc_code}", str(e)[:120]))
                except Exception as e:  # noqa: BLE001 - probe reports, never dies
                    print(f"  -> {type(e).__name__}: {str(e)[:110]}\n")
                    results.append((name, type(e).__name__, str(e)[:120]))
        finally:
            if not args.keep:
                try:
                    await client.notebooks.delete(notebook_id)
                    print(f"deleted scratch notebook {notebook_id}")
                except Exception as e:  # noqa: BLE001
                    print(f"WARNING: could not delete {notebook_id}: {e}", file=sys.stderr)

        print("\n" + "=" * 62)
        print(f"{'candidate':<24} {'outcome'}")
        print("-" * 62)
        for name, outcome, _ in results:
            print(f"{name:<24} {outcome}")
        print("=" * 62)

        winners = [n for n, o, _ in results if o == "ACCEPTED"]
        if winners:
            print(f"\nAccepted: {', '.join(winners)}")
            print("Port the winning shape into app/services/notebooklm_wire_patch.py.")
            return 0

        print(
            "\nNo candidate accepted. The shape is not a near-neighbour of 0.8.0's —\n"
            "capture the real payload from the notebook.google.com web UI\n"
            "(DevTools > Network > izAoDd > Payload) and add it as a candidate."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
