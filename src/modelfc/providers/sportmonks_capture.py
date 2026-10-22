"""Opt-in two-request evidence export. No credential loading, CLI or runtime caller."""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import base64
import fcntl
import os
import time
from zoneinfo import ZoneInfo
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, build_opener

from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import private_store, encoded, fingerprint
from modelfc.providers.oddspapi_saved_response import _json
from modelfc.providers.sportmonks_results import _NoRedirect
from modelfc.providers.sportmonks_saved import (
    LEAGUES, SavedPage, normalize, _contract, _id, _text, _time,
)

BASE = 'https://api.sportmonks.com/v3/football'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PHASE_BYTES = 16 * 1024 * 1024
MAX_REQUESTS = 2
TIMEOUT = 30
PHASE_RECORD = 'phase-' + fingerprint(b'sportmonks-capture-only-v1') + '.json'
INCLUDES = 'participants;state;season;stage;group'


class CaptureRejected(ValueError):
    """Only fixed, non-provider diagnostics may cross this API boundary."""


def _require(ok, reason):
    if not ok:
        raise CaptureRejected(reason)


@dataclass(frozen=True)
class Target:
    competition: str
    fixture_id: int
    kickoff: datetime
    home_id: int
    away_id: int
    contract_raw: bytes = field(repr=False)
    contract_sha256: str


def _plans(phase, start, end, targets, observed):
    if phase == 'DISCOVERY':
        _require(not targets and type(start) is date and type(end) is date
                 and start <= end < observed.date() and (end-start).days < 31, 'DISCOVERY_SCOPE_REJECTED')
        return [(code, f'/fixtures/between/{start}/{end}', INCLUDES, None)
                for code in LEAGUES]
    _require(phase == 'CAPTURE' and start is None and end is None
             and isinstance(targets, tuple) and len(targets) == 2
             and all(isinstance(t, Target) for t in targets)
             and {t.competition for t in targets} == set(LEAGUES), 'CAPTURE_SCOPE_REJECTED')
    plans = []
    for target in sorted(targets, key=lambda t: t.competition):
        _require(all(_id(v) for v in (target.fixture_id, target.home_id, target.away_id))
                 and target.home_id != target.away_id, 'TARGET_IDENTITY_REJECTED')
        kickoff = utc(target.kickoff)
        _require(kickoff.date() < observed.date(), 'TARGET_NOT_PRIOR_DAY')
        _, _, teams, first, last = _contract(target.contract_raw, target.contract_sha256, target.competition)
        _require(first <= kickoff.astimezone(ZoneInfo(LEAGUES[target.competition][1])).date() <= last,
                 'TARGET_SEASON_REJECTED')
        _require(target.home_id in teams and target.away_id in teams, 'TARGET_MAPPING_REJECTED')
        plans.append((target.competition, f'/fixtures/date/{kickoff.date()}',
                      INCLUDES+';scores;statistics.type', target))
    return plans


def _page(raw, code, start, end, finished):
    value = _json(raw)
    _require(isinstance(value, dict) and isinstance(value.get('data'), list)
             and 0 < len(value['data']) <= 50, 'SAVED_DATA_REJECTED')
    page = value.get('pagination')
    _require(isinstance(page, dict) and type(page.get('current_page')) is int
             and page['current_page'] == 1 and type(page.get('has_more')) is bool, 'PAGINATION_REJECTED')
    _require(page['has_more'] is False and page.get('next_page') in (None, ''), 'PAGINATION_INCOMPLETE')
    ids, eligible = set(), []
    for row in value['data']:
        _require(isinstance(row, dict) and _id(row.get('id')) and row['id'] not in ids
                 and type(row.get('league_id')) is int and row['league_id'] == LEAGUES[code][0]
                 and _id(row.get('season_id')) and _id(row.get('stage_id')), 'FIXTURE_IDENTITY_REJECTED')
        ids.add(row['id'])
        for field, expected in (('season', row['season_id']), ('stage', row['stage_id']),
                                ('group', row.get('group_id')), ('state', row.get('state_id'))):
            included = row.get(field)
            if included is not None:
                _require(isinstance(included, dict), 'INCLUDED_IDENTITY_REJECTED')
                if 'id' in included:
                    _require(_id(included['id']) and included['id'] == expected, 'INCLUDED_IDENTITY_REJECTED')
                if field == 'season' and 'league_id' in included:
                    _require(type(included['league_id']) is int and included['league_id'] == row['league_id'],
                             'INCLUDED_IDENTITY_REJECTED')
        kickoff = _time(row['starting_at'])
        _require(type(row.get('starting_at_timestamp')) is int and row['starting_at_timestamp'] == kickoff.timestamp()
                 and start <= kickoff.date() <= end, 'FIXTURE_TIME_REJECTED')
        people = row.get('participants')
        _require(isinstance(people, list) and len(people) == 2
                 and all(isinstance(p, dict) and _id(p.get('id')) and _text(p.get('name')) for p in people)
                 and people[0]['id'] != people[1]['id']
                 and {p['meta']['location'] for p in people} == {'home', 'away'}, 'PARTICIPANTS_REJECTED')
        state = row.get('state')
        _require(isinstance(state, dict) and _id(row.get('state_id')) and _text(state.get('short_name')),
                 'STATE_REJECTED')
        _require((row['state_id'] == 5) == (state['short_name'] == 'FT'), 'STATE_CONTRADICTION')
        if row['state_id'] == 5:
            _require(type(row.get('length')) is int and row['length'] == 90 and kickoff < finished,
                     'REGULATION_REJECTED')
            eligible.append(row['id'])
    _require(bool(eligible), 'NO_SUITABLE_FT_FIXTURE')
    return {'current_page': 1, 'has_more': False, 'next_page_present': False}, sorted(eligible)


def _secret_echo(raw, token):
    # Raw bytes may contain JSON-escaped or URL-encoded credential echoes.
    if token.encode() in raw or quote(token, safe='').encode() in raw:
        return True
    try:
        value = _json(raw)
    except Exception:
        return True  # malformed bodies are never archived: no safe echo inspection
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and (token in item or quote(token, safe='') in item):
            return True
        if isinstance(item, dict):
            pending.extend(item.keys()); pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _read_response(response, deadline):
    chunks, size = [], 0
    reader = getattr(response, 'read1', response.read)
    while size <= MAX_RESPONSE_BYTES:
        if time.monotonic() >= deadline:
            raise TimeoutError
        chunk = reader(min(65536, MAX_RESPONSE_BYTES+1-size))
        if time.monotonic() >= deadline:
            raise TimeoutError
        _require(isinstance(chunk, bytes), 'RESPONSE_TYPE_REJECTED')
        if not chunk:
            break
        chunks.append(chunk); size += len(chunk)
    return b''.join(chunks)


def _transport(request, timeout):
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def run_phase(store_path, *, phase, enabled=False, request_budget=0, token=None,
              start=None, end=None, targets=(), transport=None, clock=None):
    """Use a fresh private scratch store per separately authorized phase.

    Explicit budget is an execution input, NOT approval. A durable phase marker
    precedes transport; any attempt or uncertain write consumes this store.
    Never retry by changing stores without a separately authorized decision.
    Raw response bytes are base64-encoded losslessly in private response records;
    receipts contain no bodies, headers, next URLs or arbitrary exception text.
    """
    if enabled is not True:
        return {'state': 'DISABLED', 'provider_requests': 0, 'live': False}
    try:
        return _run(store_path, phase, request_budget, token, start, end, targets,
                    transport or _transport, clock or (lambda: datetime.now(timezone.utc)))
    except CaptureRejected:
        raise
    except Exception:
        raise CaptureRejected('CAPTURE_REJECTED_OR_WRITE_UNCERTAIN') from None


def _run(path, phase, budget, token, start, end, targets, transport, clock):
    _require(type(budget) is int and budget == MAX_REQUESTS, 'REQUEST_BUDGET_REJECTED')
    _require(isinstance(token, str) and 16 <= len(token) <= 4096
             and token.isascii() and not any(c.isspace() or not c.isprintable() for c in token), 'CREDENTIAL_REJECTED')
    observed = utc(clock())
    plans = _plans(phase, start, end, targets, observed)
    receipts, total = [], 0
    with private_store(path) as store:
        fcntl.flock(store.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _require(store.get(PHASE_RECORD) is None, 'PHASE_ALREADY_ATTEMPTED')
        with os.scandir(store.fd) as entries:
            _require(not any(True for _ in entries), 'FRESH_PRIVATE_STORE_REQUIRED')
        public_plans = []
        for code, endpoint, includes, target in plans:
            query = {'include': includes, 'filters': f'fixtureLeagues:{LEAGUES[code][0]}',
                     'timezone': 'UTC', 'per_page': '50', 'page': '1'}
            public_plans.append({'competition': code, 'endpoint': endpoint, 'query': query,
                'target': None if target is None else {'fixture_id': target.fixture_id,
                    'kickoff': utc(target.kickoff).isoformat(), 'home_id': target.home_id,
                    'away_id': target.away_id, 'contract_sha256': target.contract_sha256}})
        plan_hash = fingerprint(encoded(public_plans))
        store.put(PHASE_RECORD, {'version': 1, 'phase': phase, 'plan_sha256': plan_hash,
            'reserved_requests': budget, 'started_at': observed.isoformat(), 'plans': public_plans})
        for index, ((code, endpoint, _, target), spec) in enumerate(zip(plans, public_plans)):
            identifier = fingerprint(encoded([plan_hash, index]))
            started = utc(clock())
            _require(started >= observed, 'CLOCK_REJECTED')
            attempt = {'phase': phase, 'competition': code, 'endpoint': endpoint, 'query': spec['query'],
                       'request_started_at': started.isoformat(), 'request_number': index+1}
            store.put('attempt-'+identifier+'.json', attempt)
            request = Request(BASE+endpoint+'?'+urlencode(spec['query']),
                headers={'Authorization': token, 'User-Agent': 'ModelFC/1.0 (bounded evidence capture)'}, method='GET')
            status, raw, reason, completed = None, None, None, None
            try:
                deadline = time.monotonic()+TIMEOUT
                with transport(request, TIMEOUT) as response:
                    returned_status = response.getcode()
                    _require(type(returned_status) is int and 100 <= returned_status <= 599, 'HTTP_STATUS_REJECTED')
                    status = returned_status
                    raw = _read_response(response, deadline)
                completed = utc(clock())
                _require(completed >= started, 'CLOCK_REJECTED')
                _require(isinstance(raw, bytes), 'RESPONSE_TYPE_REJECTED')
                _require(len(raw) <= MAX_RESPONSE_BYTES, 'RESPONSE_TOO_LARGE')
                total += len(raw)
                _require(total <= MAX_PHASE_BYTES, 'PHASE_TOO_LARGE')
                # An echoed credential must never reach a stored record, even raw.
                _require(not _secret_echo(raw, token), 'UNSAFE_OR_MALFORMED_BODY')
                _require(status == 200, 'HTTP_REJECTED')
            except HTTPError as error:
                status = error.code if type(error.code) is int and 100 <= error.code <= 599 else None
                reason = 'HTTP_REJECTED'
                error.close()
            except CaptureRejected as error:
                reason = str(error) if str(error) in {
                    'HTTP_STATUS_REJECTED', 'CLOCK_REJECTED', 'RESPONSE_TYPE_REJECTED',
                    'RESPONSE_TOO_LARGE', 'PHASE_TOO_LARGE', 'UNSAFE_OR_MALFORMED_BODY',
                    'HTTP_REJECTED'} else 'TRANSPORT_REJECTED'
            except Exception:
                reason = 'TRANSPORT_REJECTED'
            receipt = {**attempt, 'response_completed_at': completed.isoformat() if completed else None,
                'http_status': status, 'response_sha256': None, 'byte_count': None,
                'bytes_observed': len(raw) if isinstance(raw, bytes) else None,
                'pagination': None, 'eligible_fixture_ids': [], 'state': 'REJECTED', 'reason': reason}
            if raw is not None and isinstance(raw, bytes) and len(raw) <= MAX_RESPONSE_BYTES and not _secret_echo(raw, token):
                digest = fingerprint(raw)
                store.put('response-'+digest+'.json', {'encoding': 'base64', 'bytes': base64.b64encode(raw).decode(),
                    'sha256': digest, 'byte_count': len(raw)})
                receipt.update(response_sha256=digest, byte_count=len(raw))
            if reason is None:
                try:
                    parsed = _json(raw)
                    pagination = parsed.get('pagination') if isinstance(parsed, dict) else None
                    if isinstance(pagination, dict):
                        receipt['pagination'] = {
                            'current_page': pagination.get('current_page') if type(pagination.get('current_page')) is int else None,
                            'has_more': pagination.get('has_more') if type(pagination.get('has_more')) is bool else None,
                            'next_page_present': bool(pagination.get('next_page'))}
                    first = start if target is None else utc(target.kickoff).date()
                    last = end if target is None else first
                    page, eligible = _page(raw, code, first, last, completed)
                    receipt.update(pagination=page, eligible_fixture_ids=eligible)
                    if target is not None:
                        records = normalize((SavedPage(raw, fingerprint(raw), completed),), competition=code,
                            contract_raw=target.contract_raw, contract_sha256=target.contract_sha256, as_of=completed)
                        selected = [r for r in records if r.fixture_id == target.fixture_id]
                        _require(len(selected) == 1 and selected[0].kickoff_utc == utc(target.kickoff).isoformat()
                            and (selected[0].home_provider_id, selected[0].away_provider_id) == (target.home_id, target.away_id),
                            'TARGET_IDENTITY_REJECTED')
                        _require(selected[0].status == 'FT' and selected[0].goals is not None
                                 and selected[0].corners is not None, 'TARGET_STATISTICS_MISSING')
                    receipt.update(state='SAVED', reason='SAVED_SAMPLE_ONLY')
                except CaptureRejected as error:
                    receipt['reason'] = str(error)
                except Exception:
                    receipt['reason'] = 'SAVED_SCHEMA_REJECTED'
            store.put('receipt-'+identifier+'.json', receipt)
            receipts.append(receipt)
            if receipt['state'] != 'SAVED':
                break
    return {'state': 'SAVED' if len(receipts) == 2 and all(r['state'] == 'SAVED' for r in receipts) else 'BLOCKED',
            'phase': phase, 'provider_requests': len(receipts), 'receipts': receipts,
            'validated': False, 'live': False, 'completeness': 'NOT_ESTABLISHED'}
