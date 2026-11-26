"""Assign whole Feishu groups to evenly spaced days in a Beijing calendar week."""
from collections import defaultdict
import datetime as dt
import hashlib
import json
import re
import unicodedata

from feishu_plan import cell_text
from competitor_core import ApiError

BEIJING = dt.timezone(dt.timedelta(hours=8))
WEEKDAYS = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')
FREQUENCY_COLUMN = '抓取频率'


def view_schedule_key(target):
    identity = [target['base_token'], target['table_id'], target.get('view_id')]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def beijing_today():
    return dt.datetime.now(BEIJING).date()


def week_start(day):
    return day - dt.timedelta(days=day.weekday())


def weekly_frequency(value):
    label = re.sub(r'\s+', '', unicodedata.normalize('NFKC', cell_text(value)))
    if label in ('每天', '每日'):
        return 7
    count = re.fullmatch(r'(?:一周|每周)([1-7一二两三四五六七])次', label)
    if count:
        character = count[1].replace('两', '二')
        return int(character) if character.isascii() else '一二三四五六七'.index(character) + 1


def evenly_spaced_days(frequency):
    return sorted({tuple(sorted((offset + i * 7 // frequency) % 7 for i in range(frequency)))
                   for offset in range(7)})


def build_schedule(records, plan, day, previous=None):
    by_id = {row['record_id']: row for row in records}
    batches = defaultdict(list)
    invalid = defaultdict(list)
    for item in plan['new_rows'] + plan['update_rows']:
        row = by_id[item['record_id']]
        name = cell_text(row.get('分组'))
        group_key = 'group:' + name if name else 'asin:' + item['asin']
        try:
            frequency = weekly_frequency(row.get(FREQUENCY_COLUMN))
        except ApiError:
            frequency = None
        if frequency is None:
            invalid[group_key].append(row['record_id'])
        else:
            batches[(group_key, frequency)].append((row, item['asin']))

    scheduled = {f'{frequency}:{group_key}': {'group': group_key, 'frequency': frequency, 'members': members,
                 'asins': {asin for _, asin in members}}
                 for (group_key, frequency), members in sorted(batches.items())}

    by_day = [set() for _ in WEEKDAYS]
    labels_by_day = [[] for _ in WEEKDAYS]
    row_counts = [0] * len(WEEKDAYS)
    assignments = {}

    def place(key, weekdays):
        group = scheduled[key]
        assignments[key] = {'group': group['group'], 'frequency': group['frequency'], 'weekdays': list(weekdays),
                            'records': len(group['members']),
                            'unique_asins': len(group['asins'])}
        for weekday in weekdays:
            by_day[weekday].update(group['asins'])
            labels_by_day[weekday].append(key)
            row_counts[weekday] += len(group['members'])

    for key, group in scheduled.items():
        frequency = group['frequency']
        saved = (previous or {}).get(key, {})
        days = tuple(saved.get('weekdays', []))
        if frequency == 7:
            place(key, tuple(range(7)))
        elif saved.get('frequency') == frequency and days in evenly_spaced_days(frequency):
            place(key, days)

    pending = sorted(set(scheduled) - assignments.keys(), key=lambda key: (
        -len(scheduled[key]['asins']), -len(scheduled[key]['members']), -scheduled[key]['frequency'], key))
    for key in pending:
        group = scheduled[key]

        def cost(days):
            asin_load = [len(asins | group['asins']) if weekday in days else len(asins)
                         for weekday, asins in enumerate(by_day)]
            row_load = [count + len(group['members']) if weekday in days else count
                        for weekday, count in enumerate(row_counts)]
            return (max(asin_load), sum(count ** 2 for count in asin_load),
                    max(row_load), sum(count ** 2 for count in row_load), days)

        place(key, min(evenly_spaced_days(group['frequency']), key=cost))

    monday = week_start(day)
    daily = [{'date': (monday + dt.timedelta(days=i)).isoformat(), 'weekday': weekday,
              'groups': sorted({scheduled[key]['group'] for key in labels_by_day[i]}),
              'batches': sorted(labels_by_day[i], key=lambda key: (scheduled[key]['group'], key)),
              'records': row_counts[i], 'unique_asins': len(by_day[i])}
             for i, weekday in enumerate(WEEKDAYS)]
    due = [row for key in daily[day.weekday()]['batches'] for row, _ in scheduled[key]['members']]
    return due, {'timezone': 'Asia/Shanghai', 'week_start': monday.isoformat(), 'schedule_date': day.isoformat(),
                 'daily': daily, 'batches': assignments,
                 'invalid_frequency_groups': [{'group': key, 'record_ids': ids} for key, ids in sorted(invalid.items())],
                 'invalid_rows': plan['invalid_rows']}
