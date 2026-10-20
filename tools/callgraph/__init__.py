"""Static reachability: is a (vulnerable) function reachable from an HTTP route?"""

from callgraph.index import Index, build_index
from callgraph.reach import reachability

__all__ = ["Index", "build_index", "reachability"]
