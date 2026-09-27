"""Resumable saved-image audits; processing success is not recognition accuracy.

Recognition receives only a PNG, its identity and a reviewed viewport. Labels
are a separate evaluation input. Runs preserve per-source results, failures,
implementation fingerprints and duplicate accounting without game control.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import stat
import struct
import time

from .saved_frame_reader import read_saved_frame

SCHEMA = 'veda.archive-regression-input.v1'
READER_FILES = (
    'veda/native_ocr.py', 'veda/saved_frame_reader.py', 'veda/energy_refinement.py',
    'veda/card_catalog.py', 'veda/card_regions.py', 'veda/card_costs.py',
    'veda/card_discovery.py', 'veda/card_refinement.py', 'veda/combat_evidence.py',
    'veda/combat_refinement.py', 'veda/intent_evidence.py', 'veda/saved_frame_coverage.py',
    'scripts/native_ocr.swift', 'data/intent_templates.json',
    'veda/hp_evidence.py', 'data/hp_heart_template.json',
)
_ROW_KEYS = {'image', 'sha256', 'dimensions', 'viewport', 'viewport_review', 'group_id', 'cohort'}
_COHORTS = {'development', 'locked_evaluation', 'exposure_unknown'}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _document_bytes(path, *, limit=16_000_000):
    with Path(path).open('rb') as stream:
        data = stream.read(limit+1)
    if len(data) > limit:
        raise ValueError('audit JSON exceeds its byte bound')
    return data


def read_document(path, *, limit=16_000_000):
    return json.loads(_document_bytes(path,limit=limit))


def load_inputs(path):
    path = Path(path).expanduser().resolve()
    raw = _document_bytes(path)
    manifest_hash = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)
    if (not isinstance(doc, dict) or doc.get('schema') != SCHEMA
            or set(doc)-{'schema','created_at','images','excluded','notes'}):
        raise ValueError('invalid label-free audit manifest')
    rows, excluded = doc.get('images'), doc.get('excluded', [])
    if not isinstance(rows,list) or not 1 <= len(rows) <= 10000 or not isinstance(excluded,list) or len(excluded)>10000:
        raise ValueError('bounded image and exclusion lists required')
    images, paths, duplicate_keys, groups = [], set(), {}, {}
    for row in rows:
        if not isinstance(row,dict) or set(row) != _ROW_KEYS:
            raise ValueError('image rows accept only source, viewport and cohort metadata; labels are separate')
        if not all(isinstance(row[k],str) and row[k].strip() for k in ('image','sha256','viewport_review','group_id','cohort')):
            raise ValueError('named source, viewport review and group required')
        if not re.fullmatch('[0-9a-f]{64}',row['sha256']) or row['cohort'] not in _COHORTS:
            raise ValueError('invalid image hash or cohort')
        dims, vp = row['dimensions'], row['viewport']
        if (not isinstance(dims,list) or len(dims)!=2 or any(type(v) is not int or v<=0 for v in dims)
                or not isinstance(vp,list) or len(vp)!=4
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in vp)
                or not 0<=vp[0]<vp[2]<=dims[0] or not 0<=vp[1]<vp[3]<=dims[1]):
            raise ValueError('reviewed viewport must fit explicit source dimensions')
        image = str((path.parent/row['image']).resolve())
        if image in paths:
            raise ValueError('duplicate source path')
        paths.add(image)
        key = row['sha256']
        binding = (dims,vp,row['group_id'],row['cohort'])
        if key in duplicate_keys and duplicate_keys[key] != binding:
            raise ValueError('identical source crosses viewport, group or evaluation split')
        if row['group_id'] in groups and groups[row['group_id']] != row['cohort']:
            raise ValueError('a scene group cannot cross development and evaluation')
        duplicate_keys[key],groups[row['group_id']] = deepcopy(binding),row['cohort']
        images.append({**deepcopy(row),'image':image})
    for row in excluded:
        if not isinstance(row,dict) or set(row)-{'image','sha256','reason'} or not all(
                isinstance(row.get(k),str) and row[k].strip() for k in ('image','reason')):
            raise ValueError('excluded sources require an image and explicit reason')
        image=str((path.parent/row['image']).resolve())
        if image in paths:
            raise ValueError('source cannot be both included and excluded')
        paths.add(image)
    if sha(path) != manifest_hash:
        raise ValueError('manifest changed while loading reviewed inputs')
    return {**doc,'images':images,'manifest_path':str(path),'manifest_sha256':manifest_hash}


def _source_observation(path):
    """Fingerprint the observed source, including a stable failed state.

    Invalid readable sources retain their actual bytes' hash. Missing/unreadable
    and oversized sources retain a bounded failure witness; source-state changes
    require a new audit rather than silently retrying a prior failure.
    """
    path=Path(path)
    observed={'path':str(path)}
    try:
        info=path.stat()
    except OSError as exc:
        return {**observed,'kind':'unreadable','error':type(exc).__name__,'errno':exc.errno,'stat':None}
    metadata={key:getattr(info,key) for key in ('st_mode','st_size','st_mtime_ns','st_ctime_ns','st_ino','st_dev')}
    if not stat.S_ISREG(info.st_mode):
        return {**observed,'kind':'unreadable','error':'not_regular_file','stat':metadata}
    try:
        with path.open('rb') as stream:
            raw=stream.read(64*1024*1024+1)
    except OSError as exc:
        return {**observed,'kind':'unreadable','error':type(exc).__name__,'errno':exc.errno,'stat':metadata}
    digest=hashlib.sha256(raw).hexdigest()
    if len(raw)>64*1024*1024:
        return {**observed,'kind':'invalid','error':'image_too_large','bounded_prefix_sha256':digest,'stat':metadata}
    result={**observed,'sha256':digest,'bytes':len(raw)}
    if len(raw)<33 or raw[:8]!=b'\x89PNG\r\n\x1a\n' or raw[12:16]!=b'IHDR':
        return {**result,'kind':'invalid','error':'image_not_png'}
    width,height=struct.unpack('>II',raw[16:24])
    if not width or not height or width*height>100_000_000:
        return {**result,'kind':'invalid','error':'image_dimensions_invalid'}
    return {**result,'kind':'png','dimensions':[width,height]}


def _source_observations(paths,row):
    observed=[_source_observation(path) for path in paths]
    if any(item['kind']!='png' for item in observed):
        error='source_unreadable_or_invalid'
    elif any(item['sha256']!=row['sha256'] or item['dimensions']!=row['dimensions'] for item in observed):
        error='source_identity_changed'
    else:
        error=None
    return error,observed


def reader_fingerprint(root, helper):
    root=Path(root)
    return {**{name:sha(root/name) for name in READER_FILES},
            'native_helper':sha(helper)}


def _write(path, doc):
    payload=json.dumps(doc,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n'
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('x') as stream:
        stream.write(payload)
        stream.flush()
    temp.replace(path)


def _prediction_check(prediction,row):
    if (not isinstance(prediction,dict) or prediction.get('schema')!='veda.partial-saved-frame.v1'
            or type(prediction.get('ok')) is not bool or prediction.get('partial') is not True
            or any(prediction.get(k) is not False for k in
                   ('runtime_authorized','controller_authorized','runtime_authorization_eligible'))):
        raise ValueError('reader violated partial/no-authority result contract')
    if prediction['ok'] and (prediction.get('image_path')!=row['image']
            or prediction.get('image_sha256')!=row['sha256']
            or prediction.get('source_dimensions')!=row['dimensions']
            or prediction.get('viewport')!=row['viewport']):
        raise ValueError('reader result source mismatch')
    for key in ('cards','combat_evidence','coverage'):
        if key in prediction and not isinstance(prediction[key],dict):
            raise ValueError('reader result section is not an object')
    for section,key in ((prediction.get('combat_evidence',{}),'intent_evidence'),
                        (prediction.get('coverage',{}),'hand')):
        if key in section and not isinstance(section[key],dict):
            raise ValueError('reader result nested section is not an object')
    # Apply the documented no-authority contract even inside retained original,
    # focused, proposal and merge proof objects. Numeric 0 is not Boolean False.
    false_or_unknown={'runtime_authorized','controller_authorized','runtime_authorization_eligible',
        'runtime_ready','combat_ready','hand_ready','hand_complete','complete_hand',
        'intent_coverage_complete','enemy_roster_complete','subset_is_total_incoming_damage'}
    unknown_only={'incoming_damage','enemy_count'}
    stack=[(prediction,0)];nodes=0
    while stack:
        value,depth=stack.pop();nodes+=1
        if depth>64 or nodes>2_000_000:
            raise ValueError('reader result exceeds nested proof bound')
        if isinstance(value,dict):
            for key,item in value.items():
                if key in false_or_unknown and item is not None and item is not False:
                    raise ValueError('partial reader asserted authority/readiness/completeness: '+key)
                if key in unknown_only and item is not None:
                    raise ValueError('partial reader asserted complete combat facts: '+key)
                stack.append((item,depth+1))
        elif isinstance(value,list):
            stack.extend((item,depth+1) for item in value)
    hand=prediction.get('coverage',{}).get('hand',{})
    if ((hand.get('complete') is not None and hand['complete'] is not False)
            or hand.get('card_count') is not None or hand.get('ordered_hand') is not None):
        raise ValueError('partial reader asserted complete hand coverage')


def summarize_records(records):
    issues=Counter(); crop_failures=Counter();titles=Counter();latencies=[]
    counts=Counter();cohorts=Counter()
    for record in records:
        counts[record['status']]+=1;cohorts[record['source']['cohort']]+=1
        pred=record.get('prediction') or {}
        issues.update(pred.get('issues',[]))
        if record.get('error'):issues[record['error']]+=1
        if pred.get('ok'):
            for card in pred.get('cards',{}).get('card_candidates',[]):
                if card.get('name'):titles[card['name']]+=1
            counts['published_attack_depictions']+=len(pred.get('combat_evidence',{}).get('intent_evidence',{}).get('attack_candidates',[]))
            counts['published_block_values']+=pred.get('combat_evidence',{}).get('player_block') is not None
            counts['card_depictions']+=len(pred.get('cards',{}).get('card_candidates',[]))
        ms=pred.get('timing_ms',{}).get('total')
        if type(ms) in (int,float) and math.isfinite(ms):latencies.append(ms)
        # Walk all crop sections without treating duplicated proof records as
        # independent failures: identify a region by its operation/ID/box/mode.
        seen=set()
        def walk(value, trail=()):
            if isinstance(value,dict):
                if value.get('ok') is False and isinstance(value.get('error'),str) and 'id' in value:
                    key=(str(value.get('id')),str(value.get('box_original_pixels_ltrb')),str(value.get('preprocessing')))
                    if key not in seen:
                        seen.add(key);crop_failures[value['error']]+=1
                for k,v in value.items():walk(v,trail+(k,))
            elif isinstance(value,list):
                for v in value:walk(v,trail)
        walk(pred)
    latencies.sort()
    median=None if not latencies else (latencies[(len(latencies)-1)//2]+latencies[len(latencies)//2])/2
    return {'unique_records':len(records),'outcomes':dict(counts),'cohorts':dict(cohorts),
            'issues':dict(issues),'crop_failure_counts':dict(crop_failures),
            'observed_title_predictions':dict(titles),'median_saved_read_ms':median,
            'accuracy':None,'accuracy_reason':'Processing and prediction counts need independent labels before correctness can be scored.',
            'runtime_authorized':False,'controller_authorized':False}


def run_sweep(manifest_path, *, output_dir, reader, fingerprint, resume=False,
              max_new=None, progress=None, read_frame=read_saved_frame):
    """Run serial bounded reads; resume verifies artifacts and never retries silently."""
    inputs=load_inputs(manifest_path)
    output=Path(output_dir).expanduser().resolve();output.mkdir(parents=True,exist_ok=True)
    if max_new is not None and (type(max_new) is not int or max_new<1):
        raise ValueError('max_new must be a positive integer')
    with (output/'.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise ValueError('another audit owns this output directory') from exc
        hashes=fingerprint()
        binding={'manifest_sha256':inputs['manifest_sha256'],'reader_fingerprint':hashes,
                 'runner_sha256':sha(Path(__file__))}
        journal=output/'run.json'
        if journal.exists():
            if not resume:raise ValueError('existing audit requires explicit resume')
            state=read_document(journal)
            if state.get('binding')!=binding:raise ValueError('resume inputs, reader or runner changed')
        else:
            if resume:raise ValueError('no audit exists to resume')
            if list(output.glob('*.json')):raise ValueError('output directory already contains unrelated audit results')
            state={'schema':'veda.archive-regression-run.v1','binding':binding,
                   'started_at':datetime.now(timezone.utc).isoformat(),'records':[],
                   'runtime_authorized':False,'controller_authorized':False}
            _write(journal,state)
        by_hash={};aliases={}
        for row in inputs['images']:
            by_hash.setdefault(row['sha256'],row)
            aliases.setdefault(row['sha256'],[]).append(row['image'])
        records=[];done=set();source_observations={}
        for entry in state['records']:
            digest=entry['source_sha256']
            if digest in done or digest not in by_hash or entry['file']!=digest+'.json':
                raise ValueError('invalid or duplicated resume record')
            saved=output/entry['file']
            if sha(saved)!=entry['result_sha256']:raise ValueError('preserved audit result changed')
            record=read_document(saved)
            if record.get('source')!=by_hash[digest]:raise ValueError('preserved result source changed')
            if not isinstance(record.get('source_observations'),list):
                raise ValueError('preserved result lacks observed source identity')
            if record.get('prediction') is not None:_prediction_check(record['prediction'],by_hash[digest])
            source_observations[digest]=record['source_observations']
            records.append(record);done.add(digest)
        new=0
        for digest,row in by_hash.items():
            # Every alias must still refer to the same source, including on resume.
            source_error,observed_sources=_source_observations(aliases[digest],row)
            if digest in done:
                if observed_sources!=source_observations[digest]:
                    raise ValueError('a completed source changed on resume')
                continue
            if max_new is not None and new>=max_new:break
            if fingerprint()!=hashes or sha(inputs['manifest_path'])!=binding['manifest_sha256']:
                raise ValueError('reader or manifest changed during audit')
            destination=output/(digest+'.json')
            if destination.exists():raise ValueError('unindexed result exists; inspect it before resuming')
            record={'source':deepcopy(row),'aliases':aliases[digest],
                    'source_observations':observed_sources,
                    'status':'source_failure' if source_error else None,'error':source_error,'prediction':None}
            if not source_error:
                try:
                    prediction=read_frame(Path(row['image']),viewport=row['viewport'],reader=reader,expected_sha256=digest)
                    _prediction_check(prediction,row)
                    record.update(prediction=prediction,status='processed' if prediction['ok'] else 'reader_failure')
                except (ValueError,OSError,TypeError,KeyError,RuntimeError) as exc:
                    record.update(status='reader_exception',error=type(exc).__name__+': '+str(exc)[:500])
            if _source_observations(aliases[digest],row)[1]!=observed_sources:
                raise ValueError('source changed during image processing')
            if fingerprint()!=hashes or sha(inputs['manifest_path'])!=binding['manifest_sha256']:
                raise ValueError('reader or manifest changed during audit')
            _write(destination,record)
            state['records'].append({'source_sha256':digest,'file':destination.name,'result_sha256':sha(destination)})
            state['updated_at']=datetime.now(timezone.utc).isoformat()
            _write(journal,state)
            records.append(record);done.add(digest);new+=1
            if progress:progress({'completed':len(done),'total_unique':len(by_hash),'image':Path(row['image']).name,'status':record['status']})
        summary=summarize_records(records)
        summary.update(schema='veda.archive-regression-summary.v1',manifest_sha256=inputs['manifest_sha256'],
                       total_image_paths=len(inputs['images'])+len(inputs.get('excluded',[])),
                       eligible_paths=len(inputs['images']),unique_eligible_sources=len(by_hash),
                       duplicate_paths=len(inputs['images'])-len(by_hash),excluded=inputs.get('excluded',[]),
                       processing_complete=len(done)==len(by_hash),reader_fingerprint=hashes,
                       recognition_complete=False)
        state['summary_sha256']=None
        if summary['processing_complete']:state['finished_at']=datetime.now(timezone.utc).isoformat()
        _write(output/'summary.json',summary)
        state['summary_sha256']=sha(output/'summary.json');_write(journal,state)
        return summary
