"""
setup_snapshot.py — run ONCE before the demo. Builds a Daytona snapshot with every in-sandbox
dependency (httpx, bs4, markdownify, Playwright + Chromium) preinstalled, so sandboxes start in
~100ms and never pip-install mid-race.


    python setup_snapshot.py            # creates snapshot named $DAYTONA_SNAPSHOT (default sandscrape-runtime)
    python setup_snapshot.py --status   # prints the snapshot state; wait for "active" before demoing
    python setup_snapshot.py --wait     # polls every 10s until the snapshot is active (or errors)
"""
import os
import sys
import time


from dotenv import load_dotenv


load_dotenv()


from daytona import CreateSnapshotParams, Daytona, DaytonaConfig, Image, Resources  # noqa: E402


NAME = os.getenv("DAYTONA_SNAPSHOT", "sandscrape-runtime")
# 3 sandboxes race at once. Tier 1 allows 10 GiB memory / 30 GiB disk in total across ALL running
# sandboxes, so keep each one small: 3 x 2 GiB = 6 GiB, leaving headroom for a self-heal re-race.
CPU = int(os.getenv("SANDBOX_CPU", "1"))
MEM = int(os.getenv("SANDBOX_MEMORY_GB", "2"))
DISK = int(os.getenv("SANDBOX_DISK_GB", "5"))


image = (
    Image.debian_slim("3.12")
    .pip_install(["httpx", "beautifulsoup4", "markdownify", "playwright"])
    .run_commands("python -m playwright install --with-deps chromium")
)


def state_of(daytona) -> str:
    try:
        snap = daytona.snapshot.get(NAME)
        return str(getattr(snap, "state", "unknown")).split(".")[-1].lower()
    except Exception as e:  # noqa: BLE001
        return f"not found ({type(e).__name__})"




if __name__ == "__main__":
    daytona = Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))
    if "--status" in sys.argv or "--wait" in sys.argv:
        while True:
            st = state_of(daytona)
            print(f"{time.strftime('%H:%M:%S')}  {NAME}: {st}", flush=True)
            if "--wait" not in sys.argv or st == "active" or "error" in st or "not found" in st:
                sys.exit(0 if st == "active" else 1)
            time.sleep(10)
    print(f"Building snapshot '{NAME}' ({CPU} vCPU / {MEM} GiB RAM / {DISK} GiB disk; takes a few minutes)…")
    daytona.snapshot.create(
        CreateSnapshotParams(name=NAME, image=image, resources=Resources(cpu=CPU, memory=MEM, disk=DISK)),
        on_logs=lambda chunk: print(chunk, end=""),
    )
    print(f"\nDone. Set DAYTONA_SNAPSHOT={NAME} in .env")