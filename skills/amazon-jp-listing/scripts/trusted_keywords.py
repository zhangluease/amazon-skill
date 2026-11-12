"""Faithful identifier roles from trusted input; no category-specific prefix rules."""
import copy
import hashlib
import json
import re

from model_ids import normalize_model, valid_model_list, model_pattern

FACT_ID = 'trusted-keyword-models'
ROLES = {'product_part', 'compatible_model', 'other', 'pending'}
NEGATIVE = re.compile(r'非対応|非互換|不適合|対応しない|適合しない|不兼容|不适用|不适配|NOT\s+(?:COMPATIBLE|SUPPORTED)|INCOMPATIBLE')
TOKEN = re.compile(r'[A-Z0-9]+(?:[-._+/][A-Z0-9]+)*')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def trusted_input(raw, policy):
    return (raw.get('source_kind') == 'user_spreadsheets'
            and policy.get('keyword_inputs', {}).get('trust_provided_keywords') is True)


def words(raw):
    result = []
    for row in raw.get('keywords', []):
        word = row.get('keyword') if isinstance(row, dict) else row
        if not isinstance(word, str):
            raise ValueError('Every keyword row must contain a string')
        result.append(word)
    return result


def identifier_template(profile, raw, policy):
    """Find neutral candidates. Only explicit product fields can preassign a role.

    Shapes, prefixes, length and frequency never determine what an ID identifies.
    Agents may add named models or split a repeated ID by its actual row context.
    """
    declared = {}
    own_fields = {'part_number', 'part_numbers', 'product_model', 'model_number', 'replacement_part_number'}
    for fact in profile.get('facts', []):
        if fact.get('confirmation_basis') == 'user_provided_keywords' or fact.get('status') in ('unknown', 'conflict'):
            continue  # A derived registration cannot classify its own original input.
        role = 'compatible_model' if fact.get('field') == 'compatible_models' else 'product_part' if fact.get('field') in own_fields else None
        values = fact.get('value')
        values = [values] if isinstance(values, str) else values if isinstance(values, list) else []
        if role:
            for value in values:
                if isinstance(value, str) and value.strip():
                    declared.setdefault(normalize_model(value), set()).add(role)
    context = {'product_identity': profile.get('product_identity'), 'category': profile.get('category'),
               'compatibility_basis': profile.get('compatibility_basis'),
               'declared_identifiers': {key: sorted(roles) for key, roles in declared.items()}}
    result = {'schema_version': '1.0', 'raw_fingerprint': fingerprint(raw),
              'context_fingerprint': fingerprint(context), 'records': []}
    if not trusted_input(raw, policy):
        return result
    candidates = {}
    input_words = words(raw)
    for position, word in enumerate(input_words):
        normalized_word = normalize_model(word)
        declared_spans = []
        for identifier in declared:
            matches = list(model_pattern([identifier]).finditer(normalized_word))
            if matches:
                candidates.setdefault(identifier, {'identifier': identifier, 'source_positions': []})['source_positions'].append(position)
                declared_spans.extend((m.start(), m.end()) for m in matches)
        for match in TOKEN.finditer(normalized_word):
            identifier = match.group()
            if not any(char.isdigit() for char in identifier):
                continue  # This is candidate discovery, not a restriction on actual model names.
            if any(start <= match.start() and match.end() <= end for start, end in declared_spans):
                continue
            entry = candidates.setdefault(identifier, {'identifier': identifier, 'source_positions': []})
            if position not in entry['source_positions']:
                entry['source_positions'].append(position)
    for identifier, entry in candidates.items():
        roles = declared.get(identifier, set())
        negative_only = all(NEGATIVE.search(normalize_model(input_words[p])) for p in entry['source_positions'])
        role = 'other' if negative_only else next(iter(roles)) if len(roles) == 1 else 'pending'
        reason = ('原词明确为否定兼容关系' if negative_only else '用户产品资料已明确编号所指对象' if role != 'pending' else '')
        result['records'].append(dict(entry, role=role, reason=reason))
    return result


def collect_models(profile, raw, policy, assignments=None):
    result = {'models': [], 'part_numbers': [], 'source_positions': {}, 'negative_rows': [], 'issues': []}
    if not trusted_input(raw, policy):
        return result
    template = identifier_template(profile, raw, policy)
    assignments = template if assignments is None else assignments
    def issue(code, message, severity='error'):
        result['issues'].append({'code': code, 'path': '02_identifier_roles.json', 'message': message, 'severity': severity})
    if (not isinstance(assignments, dict) or assignments.get('raw_fingerprint') != template['raw_fingerprint']
            or assignments.get('context_fingerprint') != template['context_fingerprint']):
        issue('identifier_roles_stale', '编号角色记录未绑定当前原词和商品身份；按当前内容重新理解角色，无需外部核验')
        return result
    records = assignments.get('records')
    if not isinstance(records, list):
        issue('identifier_roles_shape', 'records 必须是编号角色数组')
        return result
    input_words = words(raw)
    result['negative_rows'] = [i for i, word in enumerate(input_words) if NEGATIVE.search(normalize_model(word))]
    expected = {(record['identifier'], p) for record in template['records'] for p in record['source_positions']}
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            issue('identifier_roles_shape', '每条角色记录须是对象'); continue
        identifier, role, positions = record.get('identifier'), record.get('role'), record.get('source_positions')
        if (not isinstance(identifier, str) or not valid_model_list([identifier]) or not isinstance(role, str) or role not in ROLES
                or not isinstance(positions, list) or not positions or any(type(p) is not int or not 0 <= p < len(input_words) for p in positions)
                or len(positions) != len(set(positions))):
            issue('identifier_roles_shape', '记录需要完整原文标识、有效角色及不重复原始行位置'); continue
        identifier = normalize_model(identifier)
        pattern = model_pattern([identifier])
        if any(not pattern.search(input_words[p]) for p in positions):
            issue('identifier_not_in_source', '标识不在所引用的给定关键词行中：' + identifier); continue
        pairs = {(identifier, p) for p in positions}
        if seen & pairs:
            issue('identifier_role_duplicate', '同一行的同一标识不能被重复分配角色：' + identifier)
        seen.update(pairs)
        # A complete named ID may contain neutral numeric candidates discovered earlier.
        for candidate, position in expected:
            if position in positions and model_pattern([candidate]).search(identifier):
                seen.add((candidate, position))
        if role == 'pending':
            issue('identifier_role_pending', '尚未根据本品身份和原词语义区分编号角色：' + identifier, 'incomplete'); continue
        if not isinstance(record.get('reason'), str) or not record['reason'].strip():
            issue('identifier_role_reason', '角色判断须说明标识指向本品、适配对象或其他内容：' + identifier); continue
        if role == 'compatible_model':
            if any(p in result['negative_rows'] for p in positions):
                issue('identifier_negative_fit', '明确否定的适配关系不能登记成正向兼容：' + identifier); continue
            if identifier not in result['source_positions']:
                result['models'].append(identifier)
                result['source_positions'][identifier] = []
            result['source_positions'][identifier].extend(p for p in positions if p not in result['source_positions'][identifier])
        elif role == 'product_part' and identifier not in result['part_numbers']:
            result['part_numbers'].append(identifier)
    for identifier, position in sorted(expected - seen):
        issue('identifier_role_missing', '给定原词中的编号尚未说明角色：%s（行位置 %d）' % (identifier, position), 'incomplete')
    return result


def bind_models(profile, inventory):
    """Register only IDs assigned a compatible-object role, trusting the user input."""
    result = copy.deepcopy(profile)
    facts = result.setdefault('facts', [])
    if not isinstance(facts, list):
        raise ValueError('profile.facts must be an array')
    fact = next((v for v in facts if v.get('fact_id') == FACT_ID), None)
    if fact is not None and (fact.get('field') != 'compatible_models' or fact.get('confirmation_basis') != 'user_provided_keywords'):
        raise ValueError('Reserved trusted keyword fact ID conflicts with an existing fact')
    known = set(inventory['models'])
    for item in facts:
        if item.get('fact_id') != FACT_ID and item.get('confirmation_basis') == 'user_provided_keywords':
            original = item.get('keyword_original_value', item.get('value', []))
            if original == [] and known:
                item['value'] = list(inventory['models'])
            elif not known or not {normalize_model(v) for v in original} <= known:
                item.update(status='unknown', value=original, confirmation_basis='input_role_changed',
                            source_ref=item.get('keyword_original_source', '原适配占位，角色变化后需重新解释'))
    if not inventory['models']:
        if fact is not None:
            facts.remove(fact)
        return result
    source = '02_kw_raw.json: 用户可信原词；编号语义角色见02_identifier_roles.json，逐对象行位置见keyword_source_positions'
    for item in facts:
        if item.get('field') == 'compatible_models' and item.get('status') == 'unknown':
            values = item.get('value')
            if values == [] or valid_model_list(values) and {normalize_model(v) for v in values} <= known:
                item.update(previous_status='unknown', keyword_original_value=copy.deepcopy(values),
                            keyword_original_source=item.get('source_ref'), status='confirmed', value=values or list(inventory['models']),
                            source_ref=source, confirmation_basis='user_provided_keywords',
                            resolution='根据给定输入的语义角色采用适配关系，无需官网或说明书二次核验')
    data = {'fact_id': FACT_ID, 'field': 'compatible_models', 'value': list(inventory['models']),
            'group_label': '给定关键词中的适配对象', 'status': 'confirmed', 'source_ref': source,
            'confirmation_basis': 'user_provided_keywords', 'keyword_source_positions': copy.deepcopy(inventory['source_positions'])}
    if fact is None:
        facts.append(data)
    else:
        fact.update(data)
    return result


def registration_issues(profile, inventory):
    present = {normalize_model(model) for fact in profile.get('facts', [])
               if fact.get('field') == 'compatible_models' and fact.get('status') == 'confirmed'
               and isinstance(fact.get('source_ref'), str) and fact['source_ref'].strip()
               and valid_model_list(fact.get('value')) for model in fact['value']}
    return list(inventory['issues']) + [{'code': 'trusted_keyword_model_missing', 'path': '01_product_profile.json.facts',
             'message': '已确定为适配对象的给定标识须登记为本品适配事实，不需外部二次核验：' + model,
             'severity': 'error'} for model in inventory['models'] if model not in present]
