from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tests.test_map_travel import snapshot
from veda.map_edges import immediate_snapshot
from veda.map_travel import plan_map, save_cache, load_session_cache


class MapEdgesTests(unittest.TestCase):
    def test_current_single_edge_excludes_future_three_way_fork(self):
        snap = snapshot(); snap['facts']['floor'] = 5
        future = deepcopy(snap['ui']['options'])
        for n in future: n['node']['floor'] = 7
        direct = deepcopy(future[1]); direct.update(id='question',label='Question mark')
        direct['node'].update(node_id='question',floor=6,kind='event')
        graph = {'current_node_id':'current','outgoing_complete':True,'nodes':[direct]+future,
                 'edges':[['current','question']]+[['question',n['id']] for n in future],
                 'focused_id':'question','evidence_note':'Current circle has one edge to question mark; fork is next row.'}
        actual = immediate_snapshot(snap,graph)
        result = plan_map(actual)
        self.assertTrue(result['forced_move']); self.assertEqual('question',result['destination'])
        graph['focused_id']='left'
        with self.assertRaisesRegex(ValueError,'immediate'): immediate_snapshot(snap,graph)
        graph['focused_id']='question'; graph['edges'].append(['current','left'])
        with self.assertRaisesRegex(ValueError,'next-floor'): immediate_snapshot(snap,graph)

    def test_cache_same_content_reuses_path_new_content_gets_new_path(self):
        snap=snapshot(); cache=plan_map(snap)['cache']
        with TemporaryDirectory() as tmp:
            root=Path(tmp); first=save_cache(cache,session=root)
            self.assertEqual(first,save_cache(cache,session=root))
            changed=deepcopy(cache); changed['route']={'fixture':'new planning version'}
            other=save_cache(changed,session=root)
            self.assertNotEqual(first,other); self.assertTrue(Path(first).is_file())
            self.assertEqual(changed,load_session_cache(root,snap))
            with self.assertRaisesRegex(ValueError,'another version'): save_cache(changed,output=first)
