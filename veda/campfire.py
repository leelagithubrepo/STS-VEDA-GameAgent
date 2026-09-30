"""Campfire decisions and actual result reuse. No capture or controller calls."""
from copy import deepcopy
from uuid import uuid4
from .menu_requests import _require, _ui, validate_menu_draft
from .menu_controls import CONTROL_PROFILE
from .map_survey import read_json
from .shop import resolve_snapshot, snapshot_from_result as verified_snapshot
from .shop_results import session_directory
from .menu_results import read_pending

SCHEMA='veda.campfire-snapshot.v1'


def _role(option):
    role = option.get('role')
    if role in {'rest', 'smith', 'proceed'}:
        return role
    value = str(option.get('id', '')).casefold()
    label = str(option.get('label', '')).casefold()
    if value == 'rest' or label == 'rest':
        return 'rest'
    if value == 'smith' or label == 'smith':
        return 'smith'
    if value in {'proceed', 'leave'} or label in {'proceed', 'leave'}:
        return 'proceed'
    return None


def output_path(session, result=False):
    root=session_directory(session)/'campfire-packets'; root.mkdir(exist_ok=True)
    return root/(('result-' if result else 'action-')+uuid4().hex+'.json')


def snapshot_from_result(packet, session):
    value=verified_snapshot(packet,session)
    _require(value['ui'].get('menu_family') in {'campfire_options','campfire_exit','card_upgrade'},
             'verified screen has left the campfire; use its next-screen handler')
    value['schema']=SCHEMA
    return value


def last_snapshot(session):
    state=read_json(session_directory(session)/'state.json'); last=state.get('last_verified',{})
    _require(last.get('menu_after') is not None, 'no retained menu; use inspected snapshot or --after-result')
    return snapshot_from_result({'operation':'verify','action_id':last['action_id'],'after':last['menu_after']},session)


def decision_key(value):
    import hashlib,json
    ui=_ui(value['ui'])
    # Focus, timestamps and control proofs do not change campfire strategy.
    data={k:value[k] for k in ('context','resources','inventory','facts')}
    data['ui']={k:ui[k] for k in ('choice_id','menu_family','options')}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


def plan_campfire(snapshot,decision=None):
    _require(isinstance(snapshot,dict) and set(snapshot)=={'schema','context','inventory','resources','facts','ui'}
             and snapshot['schema']==SCHEMA, 'reviewed campfire snapshot required')
    value=deepcopy(snapshot); ui=_ui(value['ui']); family=ui['menu_family']
    ui['options'] = [_role_option(o) for o in ui['options']]
    value['ui']['options'] = deepcopy(ui['options'])
    _require(family in {'campfire_options','campfire_exit','card_upgrade'} and value['facts'].get('node_type')=='rest',
             'campfire planner only handles an observed rest site')
    key=decision_key(value); selected=None; reason=None; routine=False
    if family=='campfire_exit':
        selected=next((o for o in ui['options'] if o['enabled'] and o.get('role')=='proceed'),None)
        reason='Campfire outcome verified; return to the map.'
    elif family=='card_upgrade' and ui['phase']=='confirm':
        selected=next((o for o in ui['options'] if ui['selected_ids']==ui['pending_ids']==[o['id']]),None)
        reason='Confirm the inspected preview of the already chosen upgrade.'
    elif decision is not None:
        _require(set(decision)=={'option_id','reason','decision_key'} and decision['decision_key']==key
                 and isinstance(decision['reason'],str) and decision['reason'].strip(),
                 'campfire options or game facts changed; choose from the actual current screen')
        selected=next((o for o in ui['options'] if o['id']==decision['option_id'] and o['enabled']),None)
        _require(selected is not None,'selected campfire option is unavailable')
        reason=decision['reason']
    elif family=='campfire_options':
        # A high-health campfire with a known unupgraded Armaments has a
        # deterministic, low-risk Smith fast path.  It avoids a second model
        # round while preserving the ordinary reviewed focus/upgrade proof.
        options = [_role_option(o) for o in ui['options']]
        smith = next((o for o in options if _role(o) == 'smith' and o['enabled']), None)
        rest = next((o for o in options if _role(o) == 'rest' and o['enabled']), None)
        hp, maximum = value['resources'].get('hp'), value['resources'].get('max_hp')
        cards = value['inventory'].get('current', {}).get('card', [])
        complete = value['inventory'].get('coverage', {}).get('card') == 'complete'
        if (smith is not None and complete and 'Armaments' in cards
                and type(hp) is int and type(maximum) is int and maximum > 0
                and hp / maximum >= 0.75):
            selected, reason, routine = smith, 'At or above 75% HP with an unupgraded Armaments, Smith is the fast-path campfire choice.', True
        elif (rest is not None and type(hp) is int and type(maximum) is int and maximum > 0
              and hp / maximum <= 0.50):
            selected, reason, routine = rest, 'At or below 50% HP, Rest is the fast-path damage-risk choice.', True
    if selected is None:
        return {'status':'strategy_required','decision_key':key,'options':ui['options'],
                'fast_path': {'kind':'campfire_options','decision_budget_seconds':10,
                              'next_step':'choose_once_then_focus_and_verify'},
                'instruction':'Choose Rest versus Smith once, or choose the actual upgrade card; retain that choice through focus.',
                'controller_input_sent':False}
    inventory=deepcopy(value['inventory']); resources=deepcopy(value['resources']); facts=deepcopy(value['facts'])
    role=_role(selected); kind='rest'; phase='choose'
    if family=='card_upgrade':
        kind='selection'; screen='rest'; phase='result'
        old,new=selected['card']['name'],selected['card']['upgrade_name']
        _require(old in inventory['current']['card'],'upgrade card must be in the reviewed inventory')
        inventory['current']['card'].remove(old);inventory['current']['card'].append(new)
        facts.update(campfire_phase='resolved',upgraded_card_id=selected['id'],upgraded_card_name=new)
    elif role=='rest':
        screen='rest'; phase='result'; facts['campfire_phase']='resolved'
        # The visible amount may support a forecast; actual healing is always reviewed separately.
        amount=selected.get('reward',{}).get('amount')
        _require(amount is None or type(amount) is int and amount>=0,'visible rest amount must be a nonnegative integer')
        resources['hp']=min(resources['max_hp'],resources['hp']+amount) if amount is not None else {
            'min':resources['hp'],'max':resources['max_hp']}
    elif role=='smith':
        screen='selection'
    elif role=='proceed' and family=='campfire_exit':
        screen='map';phase='result'
    else:
        raise ValueError('unsupported campfire option: use the screen guide; Rest/Smith handler does not guess special actions')
    post={'screen':screen,'phase':phase,'resources':resources,'inventory':inventory if family=='card_upgrade' else 'unchanged',
          'facts':facts,'allow_changed_facts':[]}
    # Rest/upgrade may show a selectable exit rather than a result-only panel.
    if role=='rest' or family=='card_upgrade': post['phase']='choose'
    draft={'schema':'veda.menu-draft.v1','decision_policy':'learning',
        **{k:deepcopy(value[k]) for k in ('context','inventory','resources','facts','ui')},
        'choice':{'kind':kind,'option_ids':[selected['id']],'postconditions':post},'reasoning':reason}
    validate_menu_draft(draft,CONTROL_PROFILE)
    return {'status':'planned','routine':routine,
            'fast_path': ({'kind':'campfire_options','option_id':selected['id'],
                           'decision_budget_seconds':10,'next_step':'focus_then_commit'}
                          if routine else None),
            'draft':draft,'decision':{'option_id':selected['id'],'reason':reason,'decision_key':key},
            'controller_input_sent':False}


def _role_option(option):
    value = deepcopy(option)
    role = _role(value)
    if role is not None:
        value['role'] = role
    return value


def observed_result(session,kind,*,note,unchanged=False,focused_id=None,ui=None,actual=None,preview=None):
    state=read_json(session_directory(session)/'state.json'); identity=(state.get('pending') or {}).get('action_id')
    pending,_=read_pending(session,identity); before=pending['request']; family=before['observation']['ui'].get('menu_family')
    _require(family in {'campfire_options','campfire_exit','card_upgrade'}
             and before['observation']['facts'].get('node_type')=='rest','pending campfire action required')
    _require(isinstance(note,str) and note.strip(), 'actual observed-result note required')
    _require(focused_id is None or kind=='focus', 'focused-id belongs only to a focus result')
    _require(preview is None or kind=='upgrade_preview', 'preview belongs only to upgrade_preview')
    _require(ui is None or kind in {'menu','healed','upgraded'}, 'UI belongs only to a changed menu/result')
    result={'schema':'veda.menu-result.v1','action_id':identity,'resources':'unchanged','inventory':'unchanged',
            'facts':'unchanged','observed_result':note}
    if kind in {'focus','menu','upgrade_preview','map'}:
        _require(unchanged and actual is None,'inspect and declare unchanged gameplay facts/resources/inventory')
        if kind=='focus': result['result']={'kind':'focus','focused_id':focused_id}
        elif kind=='upgrade_preview':
            _require(isinstance(preview,dict) and set(preview)=={'selected_id','observed_upgrade_text','confirm_hint'},
                     'actual selected-card preview and confirm hint required')
            result['result']={'kind':'upgrade_preview',**deepcopy(preview)}
        elif kind=='map':
            result['result']={'kind':'menu','ui':{'screen':'map','phase':'result','choice_id':'campfire-departed',
                'layout_id':'map-result','focused_id':None,'options':[]}}
        else:
            _require(isinstance(ui,dict),'actual menu UI required')
            result['result']={'kind':'menu','ui':deepcopy(ui)}
    elif kind in {'healed','upgraded'}:
        _require(not unchanged and isinstance(actual,dict) and actual.get('others_unchanged') is True
                 and isinstance(ui,dict) and ui.get('menu_family')=='campfire_exit'
                 and pending['proposal']['step_kind']=='commit',
                 'actual resolved campfire UI, committed action and explicit changed values required')
        if kind=='healed':
            chosen=next(o for o in before['observation']['ui']['options'] if before['choice']['option_ids']==[o['id']])
            _require(family=='campfire_options' and chosen.get('role')=='rest'
                     and set(actual)=={'hp','others_unchanged'} and type(actual['hp']) is int
                     and 0<actual['hp']<=before['observation']['resources']['max_hp'],'actual HP after Rest required')
            result['resources']=dict(before['observation']['resources'],hp=actual['hp'])
            result['facts']=dict(before['observation']['facts'],campfire_phase='resolved')
        else:
            _require(family=='card_upgrade' and set(actual)=={'upgraded_card_id','upgraded_card_name','others_unchanged'},
                     'actual selected upgrade identity required')
            chosen=next(o for o in before['observation']['ui']['options'] if before['choice']['option_ids']==[o['id']])
            _require(actual['upgraded_card_id']==chosen['id'] and actual['upgraded_card_name']==chosen['card']['upgrade_name'],
                     'actual upgrade differs; use a full observed menu result')
            result['inventory']='selected_upgrade_applied'
            result['facts']=dict(before['observation']['facts'],campfire_phase='resolved',
                upgraded_card_id=actual['upgraded_card_id'],upgraded_card_name=actual['upgraded_card_name'])
        result['result']={'kind':'menu','ui':deepcopy(ui)}
    else: raise ValueError('unsupported campfire result')
    return result
