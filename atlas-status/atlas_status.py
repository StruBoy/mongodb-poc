#!/usr/bin/env python3
"""Show MongoDB Atlas cluster status and any in-flight deployment changes.

Auth: Atlas Service Account (OAuth 2.0 client credentials).
Reads from atlas-status/.env (and falls back to CWD/.env):
  ATLAS_CLIENT_ID=...
  ATLAS_CLIENT_SECRET=...
  ATLAS_PROJECT_ID=...           # or pass --project-id

Usage:
  python atlas_status.py                       # all clusters in project
  python atlas_status.py --cluster my-cluster  # one cluster
  python atlas_status.py --events-window 240   # widen event scan to 4h
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv()

BASE_URL = "https://cloud.mongodb.com/api/atlas/v2"
OAUTH_URL = "https://cloud.mongodb.com/api/oauth/token"
ATLAS_API_VERSION = "2024-11-13"

# stateName values that mean "deployment in progress"
NON_IDLE_STATES = {"CREATING", "UPDATING", "DELETING", "REPAIRING"}

# Event types tied to deployment / topology / maintenance activity
DEPLOYMENT_EVENT_KEYWORDS = ("CLUSTER", "MAINTENANCE", "AUTO_SCALING", "TOPOLOGY", "REPLICA_SET")


def get_access_token(client_id: str, client_secret: str) -> str:
    resp = requests.post(
        OAUTH_URL,
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=15,
    )
    if resp.status_code != 200:
        sys.exit(f"auth failed ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def atlas_get(token: str, path: str, params=None):
    resp = requests.get(
        f"{BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": f"application/vnd.atlas.{ATLAS_API_VERSION}+json",
        },
        params=params,
        timeout=30,
    )
    if resp.status_code != 200:
        raise requests.HTTPError(f"GET {path} -> {resp.status_code}: {resp.text}")
    return resp.json()


def list_clusters(token: str, project_id: str):
    return atlas_get(token, f"/groups/{project_id}/clusters").get("results", [])


def get_cluster(token: str, project_id: str, cluster_name: str):
    return atlas_get(token, f"/groups/{project_id}/clusters/{cluster_name}")


def recent_events(token: str, project_id: str, minutes: int):
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)) \
        .replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return atlas_get(
        token,
        f"/groups/{project_id}/events",
        params={"minDate": since, "itemsPerPage": 200},
    ).get("results", [])


def summarize_cluster(c: dict) -> str:
    name = c.get("name", "?")
    state = c.get("stateName", "?")
    paused = c.get("paused", False)
    version = c.get("mongoDBVersion") or c.get("mongoDBMajorVersion") or "?"
    cluster_type = c.get("clusterType", "?")

    tier = "?"
    regions = []
    for spec in c.get("replicationSpecs", []) or []:
        for region_cfg in spec.get("regionConfigs", []) or []:
            es = region_cfg.get("electableSpecs") or {}
            tier = es.get("instanceSize", tier)
            regions.append(f"{region_cfg.get('providerName','?')}:{region_cfg.get('regionName','?')}")

    badge = "[PAUSED] " if paused else ""
    state_marker = " *" if state in NON_IDLE_STATES else ""
    return (f"{badge}{name}  state={state}{state_marker}  type={cluster_type}  "
            f"tier={tier}  v={version}  regions={','.join(regions) or '?'}")


def is_deployment_event(event_type: str) -> bool:
    return any(k in event_type for k in DEPLOYMENT_EVENT_KEYWORDS)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--project-id", default=os.getenv("ATLAS_PROJECT_ID"),
                   help="Atlas project (group) ID. Defaults to $ATLAS_PROJECT_ID.")
    p.add_argument("--cluster", help="Filter to a single cluster by name.")
    p.add_argument("--events-window", type=int, default=60,
                   help="Minutes of recent events to scan (default 60).")
    p.add_argument("--raw", action="store_true", help="Dump raw cluster JSON and exit.")
    args = p.parse_args()

    client_id = os.getenv("ATLAS_CLIENT_ID")
    client_secret = os.getenv("ATLAS_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("error: set ATLAS_CLIENT_ID and ATLAS_CLIENT_SECRET (service account creds).")
    if not args.project_id:
        sys.exit("error: --project-id or $ATLAS_PROJECT_ID required.")

    token = get_access_token(client_id, client_secret)

    if args.cluster:
        clusters = [get_cluster(token, args.project_id, args.cluster)]
    else:
        clusters = list_clusters(token, args.project_id)

    if args.raw:
        import json
        print(json.dumps(clusters, indent=2, default=str))
        return

    print(f"Atlas project: {args.project_id}")
    print(f"Clusters ({len(clusters)}):")
    for c in clusters:
        print(f"  - {summarize_cluster(c)}")

    in_flight = [c for c in clusters if c.get("stateName") in NON_IDLE_STATES]
    print()
    if in_flight:
        print(f"In-flight deployment changes ({len(in_flight)}):")
        for c in in_flight:
            print(f"  - {c.get('name')}: state={c.get('stateName')}")
    else:
        print("Deployment: all clusters IDLE — no in-flight changes.")

    print(f"\nRecent deployment events (last {args.events_window} min):")
    try:
        events = recent_events(token, args.project_id, args.events_window)
    except requests.HTTPError as e:
        print(f"  (could not fetch events: {e})")
        return

    cluster_filter = {args.cluster} if args.cluster else {c.get("name") for c in clusters}
    deployment_events = [
        e for e in events
        if is_deployment_event(e.get("eventTypeName") or "")
        and (e.get("clusterName") in cluster_filter or e.get("clusterName") is None)
    ]

    if not deployment_events:
        print("  (none)")
        return

    for e in sorted(deployment_events, key=lambda x: x.get("created", "")):
        ts = e.get("created", "?")
        etype = e.get("eventTypeName", "?")
        cname = e.get("clusterName") or "-"
        user = e.get("username") or e.get("userId") or "-"
        print(f"  [{ts}] {etype}  cluster={cname}  by={user}")


if __name__ == "__main__":
    main()
