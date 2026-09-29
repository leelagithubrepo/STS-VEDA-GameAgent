"""Project reviewed outgoing connections into the immediate selectable set."""
from copy import deepcopy
from .menu_requests import _require
from .map_travel import _snapshot


def immediate_snapshot(snapshot, connections):
    value = deepcopy(snapshot)
    _require(isinstance(connections, dict) and set(connections) ==
        {'current_node_id', 'outgoing_complete', 'nodes', 'edges', 'focused_id', 'evidence_note'}
        and connections['outgoing_complete'] is True
        and connections['current_node_id'] == value['facts']['current_node_id']
        and isinstance(connections['evidence_note'], str) and connections['evidence_note'].strip(),
        'inspect the current circled node and all its outgoing edges')
    nodes = connections['nodes']; edges = connections['edges']
    _require(isinstance(nodes, list) and 1 <= len(nodes) <= 64 and all(isinstance(n,dict) and 'id' in n for n in nodes),
             'reviewed node options required')
    by_id = {n['id']: n for n in nodes}
    _require(len(by_id) == len(nodes) and isinstance(edges, list) and len(edges) <= 128
             and all(isinstance(e,list) and len(e)==2 and all(isinstance(n,str) for n in e) for e in edges),
             'unique nodes and explicit from/to edges required')
    current = connections['current_node_id']
    _require(len({tuple(e) for e in edges}) == len(edges) and
             all(a == current or a in by_id for a,b in edges) and all(b in by_id for a,b in edges),
             'edges need unique known endpoints')
    targets = [b for a,b in edges if a == current]
    _require(bool(targets), 'inspect at least one immediate outgoing connection')
    options = sorted((deepcopy(by_id[n]) for n in targets), key=lambda n:n['node']['x'])
    _require(connections['focused_id'] in targets, 'actual focus must be an immediate selectable node')
    # _snapshot checks act/floor adjacency, free enabled nodes and complete order.
    value['ui'] = {'menu_family':'map_nodes', 'choice_id': value['ui']['choice_id'],
        'focused_id':connections['focused_id'], 'options':options,
        'map_siblings': {'complete':True, 'selectable_count':len(options), 'from_node_id':current,
                         'evidence_note':connections['evidence_note']}}
    return _snapshot(value)
