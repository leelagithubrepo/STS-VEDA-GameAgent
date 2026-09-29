"""Scoped campfire controls for inspected default-PS5 options. No input."""
from .choice_execution import _require, _text, _same_json, _proof

FAMILIES = {'campfire_options', 'campfire_exit'}
SELECT_RULE = 'ps5-campfire-focused-select-v1'
FOCUS_RULE = 'ps5-campfire-adjacent-focus-v1'
RULES = {SELECT_RULE, FOCUS_RULE}


def checked_ui(obs):
    from .menu_controls import _base, _grid
    ui = _base(obs, obs['ui']['menu_family'])
    _require(ui['menu_family'] in FAMILIES and ui['screen'] == 'rest' and ui['phase'] == 'choose'
             and ui['selection_mode'] == 'immediate' and not ui['selected_ids'] and not ui['pending_ids']
             and ui.get('confirm') is None and obs['facts'].get('node_type') == 'rest',
             'campfire controls need an actual rest-site option screen')
    _require(all(o['costs'] == {} and _text(o.get('role')) for o in ui['options']),
             'campfire options need observed roles and no currency costs')
    if ui['menu_family'] == 'campfire_options':
        _grid(ui)
        for o in ui['options']:
            if o['role'] in {'rest','smith'}:
                _require(o['label'].strip('[]').casefold() == o['role'], 'Rest/Smith role must match the observed label')
    else:
        _require(len(ui['options']) == 1 and ui['options'][0]['role'] == 'proceed'
                 and ui['options'][0]['enabled'] and not ui['navigation']
                 and obs['facts'].get('campfire_phase') == 'resolved',
                 'campfire exit requires the observed resolved site and actual Proceed/Leave option')
        _require(ui['options'][0].get('activate', {}).get('evidence', {}).get('kind') == 'visible_hint',
                 'use the actual visible campfire exit button hint')
        _proof(ui['options'][0]['activate'], obs, 'activate:' + ui['options'][0]['id'])
    return ui


def bind(obs):
    from .menu_controls import _binding, _bind_adjacency
    for option in obs['ui']['options']:
        hint=option.pop('activate_hint',None)
        if hint is not None:
            _require(set(hint)=={'button','hint_text'} and option.get('activate') is None,'one actual activation hint required')
            option['activate']={'button':hint['button'],'evidence':{
                'kind':'visible_hint','reviewer':obs['review']['reviewer'],'meaning':'activate:'+option['id'],
                'layout_id':obs['ui']['layout_id'],'frame_id':obs['frame']['frame_id'],
                'image_sha256':obs['frame']['image_sha256'],'hint_text':hint['hint_text']}}
    ui=checked_ui(obs)
    if ui['menu_family'] == 'campfire_options':
        for o in ui['options']:
            if o['enabled'] and o['role'] in {'rest','smith'} and o.get('activate') is None:
                o['activate']=_binding(obs,'cross','activate:'+o['id'],SELECT_RULE)
        _bind_adjacency(obs,FOCUS_RULE)


def validate_binding(binding, obs, meaning, *, navigation=False):
    from .menu_controls import _adjacent_binding
    ui=checked_ui(obs)
    _require(ui['menu_family']=='campfire_options', 'selection profile is scoped to Rest/Smith choices')
    if binding['evidence']['rule_id']==SELECT_RULE:
        _require(not navigation and binding['button']=='cross' and any(
            meaning=='activate:'+o['id'] and o['enabled'] and o['role'] in {'rest','smith'} for o in ui['options']),
            'campfire selection only activates an enabled reviewed Rest or Smith option')
    else:
        _adjacent_binding(binding,ui,meaning,navigation)


def validate_choice(choice, obs):
    ui=checked_ui(obs)
    selected=[o for o in ui['options'] if choice['option_ids']==[o['id']]]
    _require(choice['kind']=='rest' and len(selected)==1, 'one campfire choice required')
    role=selected[0]['role']; post=choice['postconditions']
    _require(role in {'rest','smith','proceed'} and 'alternatives' not in post
             and _same_json(post.get('context'),obs['context'])
             and post.get('inventory_digest') in {'unchanged',obs['inventory_digest']},
             'campfire option preserves run context and inventory; Smith only opens the picker')
    expected={'rest':{'rest','map'},'smith':{'selection'},'proceed':{'map'}}
    _require(post['screen'] in expected[role], 'campfire choice needs its matching result screen')
    for k,v in obs['resources'].items():
        if k!='hp' or role!='rest':
            _require(_same_json(post['resources'].get(k),v), 'campfire choice cannot predict unrelated resource changes')
