"""Count calls to specific (vulnerable) functions inside a running app.

Static reachability says a path *exists*; runtime hits say whether real traffic
*takes* it. Replaying recorded traffic against an instrumented staging build
turns "reachable" into "reachable and exercised N times".

Uses ``sys.monitoring`` (Python 3.12+) with ``PY_START`` events enabled only on
the target functions' code objects, so untargeted code runs at full speed.

Enable in an app::

    from runtime_hits import HitCounter
    counter = HitCounter.from_env()      # EA_HITS_TARGETS="jwt.api_jwt.PyJWT.decode,..."
    if counter:
        counter.start()
        app.add_middleware(HitsEndpoint, counter=counter, token=os.environ["EA_HITS_TOKEN"])

``GET /__ea/hits`` (header ``X-EA-Token``) returns the counts; ``DELETE`` resets.
"""

from runtime_hits.counter import HitCounter, HitsEndpoint, resolve_target

__all__ = ["HitCounter", "HitsEndpoint", "resolve_target"]
