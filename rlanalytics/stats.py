"""Pure descriptive statistics for exact identities and observed match rosters."""
from collections.abc import Mapping
import hashlib
import json
import math

COUNT_METRICS = ('goals', 'assists', 'shots', 'saves', 'shots_against', 'goals_against', 'demos', 'demos_taken')

def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('statistics require finite nonnegative numbers or null')
    return value

def _duration(value):
    value = _number(value)
    return value if value is not None and value > 0 else None

def _matches(matches):
    unique, signatures = {}, {}
    for match in matches:
        for key in ('match_id', 'playlist'):
            if not isinstance(match.get(key), str) or not match[key].strip():
                raise ValueError(f'{key} must be a nonempty string')
        if match.get('schema_version', 1) != 1:
            raise ValueError('unsupported schema_version')
        for field in ('duration_seconds', 'blue_score', 'orange_score'):
            _number(match.get(field))
        players = match.get('players')
        if not isinstance(players, list) or not players:
            raise ValueError('match must contain players')
        identities, colors = set(), set()
        for player in players:
            identity = player.get('player_id')
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError('player_id must be a nonempty string')
            if identity in identities:
                raise ValueError(f'duplicate player_id {identity}')
            identities.add(identity)
            if player.get('team') not in ('blue', 'orange'):
                raise ValueError('player team must be blue or orange')
            colors.add(player['team'])
            for field in (*COUNT_METRICS, 'duration_seconds', 'boost_mean'):
                value = _number(player.get(field))
                if field == 'boost_mean' and value is not None and value > 100:
                    raise ValueError('boost_mean must be between 0 and 100')
        if colors != {'blue', 'orange'}:
            raise ValueError('match must contain both teams')
        content = dict(match)
        content.pop('replay_ids', None)
        content['players'] = sorted(players, key=lambda p: p['player_id'])
        signature = json.dumps(content, sort_keys=True, allow_nan=False)
        identity = match['match_id']
        if identity in unique:
            if signatures[identity] != signature:
                raise ValueError(f'conflicting records for match_id {identity}')
        else:
            unique[identity], signatures[identity] = match, signature
    return sorted(unique.values(), key=lambda m: (m['playlist'], m['match_id']))

def _row(kind, identity, playlist, team_size):
    return {
        'entity_type': kind, 'entity_id': identity, 'playlist': playlist, 'team_size': team_size,
        'match_count': 0, 'match_ids': [], 'series_ids': set(),
        'duration_seconds': 0, 'missing_duration_matches': 0,
        'metrics': {name: {'total': 0, 'observed_matches': 0, 'missing_matches': 0,
                          'rate_total': 0, 'exposure_seconds': 0, 'missing_exposure_matches': 0}
                    for name in COUNT_METRICS},
        'shooting': {'definition': 'credited_goals_per_shot', 'goals': 0, 'shots': 0,
                     'observed_matches': 0, 'missing_matches': 0},
        'boost': {'weighted_sum': 0, 'exposure_seconds': 0, 'observed_matches': 0,
                  'partial_matches': 0, 'missing_matches': 0, 'observed_observations': 0,
                  'missing_observations': 0, 'missing_exposure_observations': 0},
        'outcomes': {'wins': 0, 'losses': 0, 'draws': 0, 'observed_matches': 0, 'missing_matches': 0},
    }

def _add(row, match, color, duration, metrics, shooting_goals, players):
    row['match_count'] += 1
    row['match_ids'].append(match['match_id'])
    if match.get('series_id'):
        row['series_ids'].add(str(match['series_id']))
    if duration is None:
        row['missing_duration_matches'] += 1
    else:
        row['duration_seconds'] += duration
    for name in COUNT_METRICS:
        metric, value = row['metrics'][name], metrics.get(name)
        if value is None:
            metric['missing_matches'] += 1
            continue
        metric['total'] += value
        metric['observed_matches'] += 1
        if duration is None:
            metric['missing_exposure_matches'] += 1
        else:
            metric['rate_total'] += value
            metric['exposure_seconds'] += duration
    shooting, shots = row['shooting'], metrics.get('shots')
    if shooting_goals is None or shots is None:
        shooting['missing_matches'] += 1
    else:
        shooting['observed_matches'] += 1
        shooting['goals'] += shooting_goals
        shooting['shots'] += shots
    boost, valid = row['boost'], 0
    for player in players:
        value, exposure = player.get('boost_mean'), _duration(player.get('duration_seconds'))
        if value is None:
            boost['missing_observations'] += 1
        elif exposure is None:
            boost['missing_exposure_observations'] += 1
        else:
            boost['weighted_sum'] += value * exposure
            boost['exposure_seconds'] += exposure
            boost['observed_observations'] += 1
            valid += 1
    if valid:
        boost['observed_matches'] += 1
        if valid < len(players):
            boost['partial_matches'] += 1
    else:
        boost['missing_matches'] += 1
    outcomes = row['outcomes']
    own = match.get(f'{color}_score')
    other = match.get('orange_score' if color == 'blue' else 'blue_score')
    if own is None or other is None:
        outcomes['missing_matches'] += 1
    else:
        outcomes['observed_matches'] += 1
        outcomes['wins' if own > other else 'losses' if own < other else 'draws'] += 1

def _finish(row):
    row['match_ids'].sort()
    for field in ('series_ids', 'names', 'team_names'):
        if field in row:
            row[field] = sorted(row[field])
    for metric in row['metrics'].values():
        metric['per_match'] = metric['total'] / metric['observed_matches'] if metric['observed_matches'] else None
        metric['per_5_minutes'] = metric['rate_total'] * 300 / metric['exposure_seconds'] if metric['exposure_seconds'] else None
        if not metric['observed_matches']:
            metric['total'] = None
        if not metric['exposure_seconds']:
            metric['rate_total'] = None
    shooting = row['shooting']
    shooting['percentage'] = shooting['goals'] * 100 / shooting['shots'] if shooting['shots'] else None
    if not shooting['observed_matches']:
        shooting['goals'] = shooting['shots'] = None
    boost = row['boost']
    boost['mean'] = boost['weighted_sum'] / boost['exposure_seconds'] if boost['exposure_seconds'] else None
    if not boost['observed_observations']:
        boost['weighted_sum'] = None
    outcomes = row['outcomes']
    outcomes['win_percentage'] = outcomes['wins'] * 100 / outcomes['observed_matches'] if outcomes['observed_matches'] else None
    return row

def aggregate_players(matches):
    """Group exact player IDs within playlists, retaining per-metric coverage."""
    rows = {}
    for match in _matches(matches):
        for player in sorted(match['players'], key=lambda p: p['player_id']):
            team_size = sum(p['team'] == player['team'] for p in match['players'])
            key = (match['playlist'], team_size, player['player_id'])
            if key not in rows:
                rows[key] = _row('player', player['player_id'], match['playlist'], team_size)
                rows[key].update(player_id=player['player_id'], names=set())
            row = rows[key]
            if player.get('name'):
                row['names'].add(str(player['name']))
            _add(row, match, player['team'], _duration(player.get('duration_seconds')),
                 player, player.get('goals'), [player])
    return [_finish(rows[key]) for key in sorted(rows)]

def _sum_complete(players, name):
    values = [player.get(name) for player in players]
    return sum(values) if values and all(value is not None for value in values) else None

def aggregate_teams(matches):
    """Group observed rosters per playlist; count opponent shots and time once."""
    rows = {}
    for match in _matches(matches):
        rosters = {color: sorted((p for p in match['players'] if p['team'] == color),
                                key=lambda p: p['player_id']) for color in ('blue', 'orange')}
        for color, players in rosters.items():
            identities = tuple(player['player_id'] for player in players)
            key = (match['playlist'], identities)
            if key not in rows:
                encoded = json.dumps(identities, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
                identity = 'roster:' + hashlib.sha256(encoded).hexdigest()
                rows[key] = _row('team', identity, match['playlist'], len(identities))
                rows[key].update(roster_player_ids=list(identities), team_names=set())
            row = rows[key]
            row['team_names'].update(str(p['team_name']) for p in players if p.get('team_name'))
            other = 'orange' if color == 'blue' else 'blue'
            metrics = {name: _sum_complete(players, name) for name in COUNT_METRICS}
            metrics['goals'] = match.get(f'{color}_score')
            metrics['goals_against'] = match.get(f'{other}_score')
            metrics['shots_against'] = _sum_complete(rosters[other], 'shots')
            _add(row, match, color, _duration(match.get('duration_seconds')), metrics,
                 _sum_complete(players, 'goals'), players)
    return [_finish(rows[key]) for key in sorted(rows)]

def flatten_rows(rows):
    """Return dictionaries with dotted keys, JSON lists and unchanged nulls."""
    result = []
    def visit(value, prefix, target):
        if isinstance(value, Mapping):
            for key in sorted(value):
                visit(value[key], f'{prefix}.{key}' if prefix else str(key), target)
        elif isinstance(value, (list, tuple)):
            target[prefix] = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        else:
            target[prefix] = value
    for row in rows:
        target = {}
        visit(row, '', target)
        result.append(target)
    columns = sorted({key for row in result for key in row})
    return [{key: row.get(key) for key in columns} for row in result]
