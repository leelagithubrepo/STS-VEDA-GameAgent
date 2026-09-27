"""Source-bound top-HUD HP from saved OCR and an independently verified heart.

No OCR is invoked. Literal fraction text is never repaired or combined. Both
red glyph pixels and the source-grounded heart silhouette are required; position
alone cannot turn a username or arbitrary fraction into health.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import time

from .native_ocr import SCHEMA, _ReaderFailure, _png_identity, _regions, _validate_observations, extract_hud

_TEMPLATE_PATH = Path(__file__).resolve().parents[1]/'data/hp_heart_template.json'
_TOP_HUD = [.02, 0, .50, .075]
_FRACTION = re.compile(r'([0-9]{1,9})\s*/\s*([0-9]{1,9})\Z')
_GRID = 24
_MAX_PIXELS = 500_000


def _red(rgb):
    r, g, b = rgb
    return r >= 120 and r > 1.35*g and r > 1.18*b and b >= .6*g


def _white(rgb):
    return min(rgb) >= 170 and max(rgb)-min(rgb) <= 45


def _inside(box, outer):
    return outer[0] <= box[0] < box[2] <= outer[2] and outer[1] <= box[1] < box[3] <= outer[3]


def _components(mask, width, height):
    seen, found = bytearray(len(mask)), []
    for start, on in enumerate(mask):
        if not on or seen[start]:
            continue
        pending, points = [start], []
        seen[start] = 1
        while pending:
            point = pending.pop()
            points.append(point)
            x, y = point % width, point // width
            for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if 0 <= nx < width and 0 <= ny < height:
                    target = ny*width+nx
                    if mask[target] and not seen[target]:
                        seen[target] = 1
                        pending.append(target)
        if len(points) >= 8:
            found.append(points)
        if len(found) > 512:
            raise ValueError('hp_component_bound_exceeded')
    return found


def _template():
    with _TEMPLATE_PATH.open('rb') as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError('hp_template_byte_bound')
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise ValueError('hp_template_invalid')
    rows = doc.get('mask_rows')
    source_box = doc.get('source_box_px')
    if (doc.get('schema') != 'veda.hp-heart-template.v1' or doc.get('grid_size') != _GRID
            or not isinstance(rows, list) or len(rows) != _GRID
            or any(not isinstance(row, str) or len(row) != _GRID or set(row)-{'0', '1'} for row in rows)
            or not isinstance(doc.get('source_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', doc['source_sha256'])
            or not isinstance(source_box, list) or len(source_box) != 4
            or any(type(v) is not int or v < 0 for v in source_box)
            or source_box[0] >= source_box[2] or source_box[1] >= source_box[3]):
        raise ValueError('hp_template_invalid')
    return doc, {i for i, bit in enumerate(''.join(rows)) if bit == '1'}, hashlib.sha256(raw).hexdigest()


def _heart_shapes(image, roi, height, template, expected):
    from PIL import Image

    a, b, c, d = roi
    crop = image.crop(roi)
    rgb = list(crop.get_flattened_data() if hasattr(crop, 'get_flattened_data') else crop.getdata())
    mask = bytearray(_red(value) for value in rgb)
    matches = []
    for points in _components(mask, c-a, d-b):
        xs, ys = [p % (c-a) for p in points], [p // (c-a) for p in points]
        left, top, right, bottom = min(xs), min(ys), max(xs)+1, max(ys)+1
        w, h = right-left, bottom-top
        if (not 1.05 <= w/h <= 1.65 or not .012*height <= h <= .04*height
                or not .48 <= len(points)/(w*h) <= .87):
            continue
        # A component touching the search boundary may be a clipped symbol.
        if left == 0 or top == 0 or right == c-a or bottom == d-b:
            continue
        component = Image.new('L', (w, h))
        for point in points:
            component.putpixel((point % (c-a)-left, point // (c-a)-top), 255)
        small = component.resize((_GRID, _GRID), Image.Resampling.NEAREST)
        values = list(small.get_flattened_data() if hasattr(small, 'get_flattened_data') else small.getdata())
        present = {i for i, value in enumerate(values) if value}
        score = len(present & expected)/len(present | expected)
        density = lambda x0, y0, x1, y1: sum(y*_GRID+x in present for y in range(y0, y1)
                                              for x in range(x0, x1))/((x1-x0)*(y1-y0))
        notch = density(10, 0, 14, 3)
        left_lobe, right_lobe = density(4, 1, 9, 4), density(15, 1, 21, 4)
        # Template IoU alone would let a dense oval approximate a heart. The
        # two separated upper lobes and empty notch independently veto it.
        if score < .82 or notch > .30 or min(left_lobe, right_lobe) < .60:
            continue
        matches.append({'box_px': [a+left, b+top, a+right, b+bottom], 'template_iou': score,
                        'red_pixels': len(points), 'notch_density': notch,
                        'lobe_densities': [left_lobe, right_lobe],
                        'template_source_sha256': template['source_sha256'],
                        'template_source_box_px': template['source_box_px']})
    return matches


def _association(heart, box):
    a, b, c, d = box
    h = d-b
    left, top, right, bottom = heart['box_px']
    # OCR may box the icon together with an otherwise literal fraction. Keep
    # that original box, but sample glyph color strictly to the icon's right.
    return (abs((top+bottom-b-d)/2) <= .6*h and .5*h <= bottom-top <= 1.7*h
            and left >= a-2.2*h and right <= a+1.5*h
            and a-right <= 1.0*h and c-right >= 1.5*h)


def _glyph_color(image, box, heart, roi):
    a, b, c, d = box
    h = d-b
    sample = [math.ceil(max(a, heart['box_px'][2]+.10*h)), math.ceil(b), math.floor(c), math.floor(d)]
    if not _inside(sample, roi):
        return {'confirmed': False, 'sample_box_px': sample, 'error': 'no_separate_glyph_area'}
    crop = image.crop(sample)
    values = list(crop.get_flattened_data() if hasattr(crop, 'get_flattened_data') else crop.getdata())
    red, white = sum(_red(v) for v in values), sum(_white(v) for v in values)
    return {'confirmed': red >= max(8, len(values)*.035) and white <= red*.35,
            'sample_box_px': sample, 'red_pixels': red, 'white_pixels': white, 'sample_pixels': len(values)}


def _parse(row):
    candidates, values = [], set()
    for candidate in row['candidates']:
        match = _FRACTION.fullmatch(candidate['text'].strip())
        value = list(map(int, match.groups())) if match else None
        if value is not None:
            values.add(tuple(value))
        candidates.append({**candidate, 'fraction': value})
    reasons = []
    if len(values) > 1:
        reasons.append('conflicting_exact_fraction_alternatives')
    if any(maximum == 0 or hp > maximum for hp, maximum in values):
        reasons.append('invalid_hp_fraction')
    top = candidates[0]
    if top['fraction'] is None or top['confidence'] < .8:
        reasons.append('top_fraction_not_exact_or_confident')
    return {'fraction': top['fraction'] if not reasons else None, 'candidates': candidates,
            'distinct_candidates': [list(value) for value in sorted(values)], 'issues': reasons}


def refine_hp_reading(image_path, *, viewport, native, frame_id):
    """Return HP evidence from one immutable image and its full OCR envelope.

    Existing nominal-region HP is conflict evidence, not permission to accept
    a fraction lacking pixel support. Sources/protocol failures raise ValueError
    for atomic clearing by the coordinator. Ordinary absence/occlusion yields
    null HP. No expected value, deck, player name, history or OCR call is used.
    """
    from PIL import Image

    began = time.monotonic()
    if not isinstance(native, dict):
        raise ValueError('hp_invalid_native_envelope')
    path = Path(image_path).expanduser().resolve()
    if not isinstance(frame_id, str) or not frame_id.strip() or len(frame_id) > 256:
        raise ValueError('hp_invalid_frame_id')
    try:
        digest, dimensions = _png_identity(path)
        geometry = _regions(dimensions, {'viewport': viewport})
        _validate_observations(native.get('observations'), dimensions)
    except _ReaderFailure as exc:
        raise ValueError('hp_source_or_observation_invalid:'+str(exc)) from exc
    if (native.get('schema') != SCHEMA or native.get('ok') is not True
            or native.get('image_path') != str(path) or native.get('image_sha256') != digest
            or native.get('parent_image_sha256') != digest or native.get('source_dimensions') != dimensions
            or native.get('frame_id') != frame_id or native.get('parent_frame_id') != frame_id
            or native.get('runtime_authorization_eligible') is not False):
        raise ValueError('hp_native_source_mismatch')
    original = extract_hud(native['observations'], source_dimensions=dimensions, regions={'viewport': viewport})
    if (native.get('hud') != original or any(type(native['hud'][key]) is not type(original[key])
            for key in ('hp', 'max_hp', 'energy', 'energy_max'))):
        raise ValueError('hp_original_hud_mismatch')
    with path.open('rb') as stream:
        raw = stream.read(64*1024*1024+1)
    if len(raw) > 64*1024*1024:
        raise ValueError('hp_image_byte_bound')
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('hp_image_changed')
    with Image.open(BytesIO(raw)) as source:
        if list(source.size) != dimensions:
            raise ValueError('hp_decoded_dimensions_mismatch')
        image = source.convert('RGB')
    left, top, right, bottom = geometry['viewport']
    width, height = right-left, bottom-top
    roi = [math.ceil(left+width*_TOP_HUD[0]), math.ceil(top+height*_TOP_HUD[1]),
           math.floor(left+width*_TOP_HUD[2]), math.floor(top+height*_TOP_HUD[3])]
    selected, rejected, issues = [], [], []
    template, expected, template_sha = None, set(), None
    try:
        template, expected, template_sha = _template()
    except OSError:
        issues.append('hp_template_unavailable')
    except (ValueError, KeyError, TypeError):
        issues.append('hp_template_invalid')
    area = max(0, roi[2]-roi[0])*max(0, roi[3]-roi[1])
    hearts = []
    sampled, inspected = area, 0
    if not 0 < area <= _MAX_PIXELS:
        issues.append('hp_pixel_sample_bound')
    elif not issues:
        try:
            hearts = _heart_shapes(image, roi, height, template, expected)
        except ValueError as exc:
            if str(exc) != 'hp_component_bound_exceeded':
                raise
            issues.append(str(exc))
        for index, row in enumerate(native['observations']):
            x, y, w, h = row['box_original_pixels_top_left']
            box = [x, y, x+w, y+h]
            if not _inside(box, roi) or not .009*height <= h <= .045*height or not 1.5*h <= w <= 9*h:
                continue
            associated = [heart for heart in hearts if _association(heart, box)]
            if not associated:
                continue
            inspected += 1
            if inspected > 64 or sampled+w*h > 1_000_000:
                issues.append('hp_candidate_sample_bound_exceeded')
                break
            proof = {'observation_index': index, 'box_original_pixels_ltrb': box,
                     'raw_observation': deepcopy(row), 'heart_candidates': associated}
            if len(associated) != 1:
                rejected.append({**proof, 'issues': ['multiple_adjacent_heart_symbols']})
                issues.append('multiple_adjacent_heart_symbols')
                continue
            colour = _glyph_color(image, box, associated[0], roi)
            proof['glyph_color'] = colour
            sampled += colour.get('sample_pixels', 0)
            if not colour['confirmed']:
                rejected.append({**proof, 'issues': ['fraction_glyphs_not_red']})
                continue
            selected.append({**proof, **_parse(row)})
            if len(selected) > 32:
                issues.append('hp_candidate_bound_exceeded')
                break
    image.close()
    values = {tuple(pair) for item in selected for pair in item['distinct_candidates']}
    if len(values) > 1:
        issues.append('conflicting_exact_hp_fractions')
    if len(selected) > 1:
        issues.append('multiple_heart_backed_fraction_observations')
    issues += [issue for item in selected for issue in item['issues']]
    value = selected[0]['fraction'] if len(selected) == 1 and not issues else None
    old = [original['hp'], original['max_hp']] if original['hp'] is not None else None
    old_values = {tuple(pair) for pair in original['evidence']['hp']['distinct_candidates']}
    # All literal alternatives from the old narrow region remain contradiction
    # evidence, even if that older parser withheld a final value.
    if len(old_values) > 1 or (values and old_values and len(values | old_values) > 1):
        issues.append('conflict_with_nominal_region_hp')
        value = None
    error = (next((issue for issue in issues if issue.startswith('hp_template_')), None)
             or ('ambiguous_hp_evidence' if issues else None if value is not None else 'missing_heart_backed_hp_fraction'))
    if _png_identity(path) != (digest, dimensions):
        raise ValueError('hp_image_changed')
    return {'hp': value[0] if value is not None else None, 'max_hp': value[1] if value is not None else None,
            'error': error, 'refinement': {
                'schema': 'veda.hp-evidence.v1', 'status': 'verified' if value is not None else 'unknown',
                'image_path': str(path), 'image_sha256': digest, 'source_dimensions': dimensions,
                'frame_id': frame_id, 'viewport': list(viewport), 'search_region_px': roi,
                'original_nominal_hp': old, 'original_nominal_evidence': deepcopy(original['evidence']['hp']),
                'heart_candidates': hearts, 'selected': selected, 'rejected': rejected,
                'distinct_candidates': [list(pair) for pair in sorted(values)], 'issues': sorted(set(issues)),
                'template_sha256': template_sha, 'pixel_sample_area': area,
                'sampled_pixels': sampled, 'inspected_observations': inspected, 'native_invocations': 0,
                'timing_ms': (time.monotonic()-began)*1000,
                'runtime_authorized': False, 'controller_authorized': False,
                'runtime_authorization_eligible': False}}
