#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

import psycopg


def fmt_ts(ts: datetime | None) -> str:
    if not ts:
        return "-"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> int:
    parser = argparse.ArgumentParser(description="Live ingestion progress monitor")
    parser.add_argument("--db-url", default=os.getenv("SUPABASE_DB_URL", ""), help="Postgres DSN")
    parser.add_argument("--interval", type=float, default=3.0, help="Refresh interval in seconds")
    parser.add_argument("--run-id", default="", help="Specific crawl run id to monitor")
    args = parser.parse_args()

    if not args.db_url:
        raise SystemExit("Missing --db-url or SUPABASE_DB_URL")

    with psycopg.connect(args.db_url, autocommit=True) as conn:
        prev_count = None
        prev_t = None

        while True:
            with conn.cursor() as cur:
                if args.run_id:
                    cur.execute(
                        """
                        select id, status, started_at, finished_at, notes
                        from crawl_runs
                        where id = %s
                        """,
                        (args.run_id,),
                    )
                else:
                    cur.execute(
                        """
                        select id, status, started_at, finished_at, notes
                        from crawl_runs
                        order by started_at desc
                        limit 1
                        """
                    )
                run = cur.fetchone()

                if not run:
                    print("No crawl_runs rows found yet.")
                    time.sleep(args.interval)
                    continue

                run_id, run_status, started_at, finished_at, notes = run

                cur.execute(
                    """
                    select
                      count(*)::bigint as total,
                      count(*) filter (where status='parsed')::bigint as parsed,
                      count(*) filter (where status='failed')::bigint as failed,
                      count(*) filter (where status='skipped')::bigint as skipped,
                      count(*) filter (where status='queued')::bigint as queued,
                      count(*) filter (where status='fetched')::bigint as fetched,
                      max(fetched_at) as last_fetch
                    from crawled_pages
                    where run_id = %s
                    """,
                    (run_id,),
                )
                total, parsed, failed, skipped, queued, fetched, last_fetch = cur.fetchone()

                cur.execute(
                    """
                    select page_kind, count(*)::bigint
                    from crawled_pages
                    where run_id = %s
                    group by page_kind
                    order by count(*) desc, page_kind
                    """,
                    (run_id,),
                )
                kinds = cur.fetchall()

            now = time.time()
            rpm = 0.0
            if prev_count is not None and prev_t is not None and now > prev_t:
                rpm = (total - prev_count) * 60.0 / (now - prev_t)
            prev_count = total
            prev_t = now

            print("\x1bc", end="")
            print("Ingestion Live Progress")
            print(f"run_id       : {run_id}")
            print(f"status       : {run_status}")
            print(f"started_at   : {fmt_ts(started_at)}")
            print(f"finished_at  : {fmt_ts(finished_at)}")
            print(f"last_fetch   : {fmt_ts(last_fetch)}")
            print(f"notes        : {notes or '-'}")
            print("")
            print(f"total_pages  : {total}")
            print(f"parsed       : {parsed}")
            print(f"failed       : {failed}")
            print(f"skipped      : {skipped}")
            print(f"queued       : {queued}")
            print(f"fetched      : {fetched}")
            print(f"ingest_rate  : {rpm:.2f} rows/min (last interval)")
            print("")
            print("page_kinds:")
            for kind, cnt in kinds:
                print(f"  - {kind}: {cnt}")

            if run_status in {"done", "failed"}:
                print("")
                print("Run completed. Exiting monitor.")
                break

            time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
