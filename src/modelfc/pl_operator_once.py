"""Disabled operator primitives: two identity/metadata requests, then reviewed capture.

No scheduler, automatic mapping, qualification, publication or retry. Preparation
receipts live in the existing private acquisition store; accounting stays in the
existing shared prospective control. Public functions expose no clock or endpoint.
"""
import base64
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from zoneinfo import ZoneInfo

from modelfc import guarded_acquisition as acquisition
from modelfc.acquisition_storage import AcquisitionRejected, encoded, fingerprint, private_store, shared_runner
from modelfc.corner_prospective import _load, _save, _RequestBudgetGuard
from modelfc.corner_prospective_budget import validate_calendar_control
from modelfc.providers.oddspapi import BASE_URL, USER_AGENT, _NoRedirect
from modelfc.providers.oddspapi_saved_response import _json

MAX_BYTES = 8 * 1024 * 1024
LONDON = ZoneInfo('Europe/London')


def _now():
    return datetime.now(timezone.utc)


def _require(ok, reason='EPL_PREPARATION_REJECTED'):
    if not ok:
        raise AcquisitionRejected(reason)


def _authorization(path):
    raw = acquisition._local_bytes(path, 8192, private=True)
    a = _json(raw)
    _require(type(a) is dict and set(a) == {'version', 'state_dir', 'private_dir',
        'selected_day', 'issued_at', 'expires_at', 'quota_observed_at',
        'quota_remaining', 'quota_floor', 'max_requests'})
    _require(type(a['version']) is int and a['version'] == 1
             and type(a['max_requests']) is int and a['max_requests'] == 2)
    at = _now()
    issued, expiry, observed = (acquisition._timestamp(a[k]) for k in
                               ('issued_at', 'expires_at', 'quota_observed_at'))
    day = date.fromisoformat(a['selected_day'])
    _require(day.isoformat() == a['selected_day'] and day == at.astimezone(LONDON).date()+timedelta(days=1))
    _require(issued <= at < expiry and timedelta(0) < expiry-issued <= timedelta(hours=6)
             and timedelta(0) <= at-observed <= timedelta(hours=1))
    _require(all(type(a[k]) is int and a[k] >= 1 for k in ('quota_remaining', 'quota_floor'))
             and a['quota_remaining']-2 >= a['quota_floor'])
    _require(all(type(a[k]) is str and Path(a[k]).is_absolute() for k in ('state_dir','private_dir')))
    return a, fingerprint(raw)


class _PreparationClient:
    def __init__(self):
        self.key = os.environ.get('ODDSPAPI_API_KEY', '').strip()
        _require(bool(self.key), 'CREDENTIAL_MISSING')
        self.opener = build_opener(_NoRedirect())

    def retrieve(self, kind, day, guard):
        # Deliberately no odds, account, caller URL, pagination or retry route.
        if kind == 'FIXTURE_DISCOVERY':
            endpoint = 'fixtures'
            params = {'tournamentId':17, 'statusId':0, 'language':'en',
                      'from':_midnight(day), 'to':_midnight(day+timedelta(days=1))}
        elif kind == 'MARKET_METADATA':
            endpoint, params = 'markets', {'language':'en'}
        else:
            raise AcquisitionRejected('REQUEST_REJECTED')
        guard.before_request(kind)
        started = _now()
        try:
            request = Request(BASE_URL+'/'+endpoint+'?'+urlencode(dict(params, apiKey=self.key)),
                              headers={'User-Agent':USER_AGENT,'Accept':'application/json'})
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(MAX_BYTES+1)
                _require(response.status == 200 and 0 < len(raw) <= MAX_BYTES)
            return raw, started, _now()
        except (HTTPError, URLError, OSError):
            raise AcquisitionRejected('EPL_PREPARATION_TRANSPORT_REJECTED') from None
        finally:
            guard.after_request()


def _midnight(day):
    return datetime.combine(day, datetime.min.time(), LONDON).astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def prepare(*, authorization_path=None, enabled=False, authorization_sha256=None):
    """Retain raw identity/metadata evidence only; never declare it reviewed/fresh."""
    result = {'status':'DISABLED', 'provider_requests':0}
    if enabled is not True:
        return result
    try:
        acquisition.require_runtime()
        a, pin = _authorization(authorization_path)
        _require(authorization_sha256 is None or authorization_sha256==pin,'AUTHORIZATION_CHANGED')
        day = date.fromisoformat(a['selected_day'])
        with shared_runner(a['state_dir']) as control_path, private_store(a['private_dir']) as store:
            control = _load(control_path)
            validate_calendar_control(control)
            # Do not roll over or repair control/old uncertain claims here.
            _require(control['period']['start'] <= _now().date().isoformat() < control['period']['end'])
            _require(control['period']['allowance']-control['period']['reserved']-2 >= a['quota_floor'],
                     'EPL_SHARED_RESERVE_FLOOR')
            key = acquisition._name('eplprepclaim', {'competition':'E0','day':day.isoformat()})
            _require(store.get(key) is None, 'EPL_PREPARATION_ALREADY_CLAIMED')
            client = _PreparationClient()
            guard = _RequestBudgetGuard(control_path, control, result,
                allowed_kinds=('FIXTURE_DISCOVERY','MARKET_METADATA'), invocation_limit=2)
            def check():
                _require(_authorization(authorization_path) == (a,pin), 'AUTHORIZATION_CHANGED')
                _require(control['period']['start'] <= _now().date().isoformat() < control['period']['end'],
                         'PERIOD_EXPIRED')
            guard.calendar_check = check
            check()
            # Claim before any reservation/write that could have an uncertain outcome.
            store.put(key, {'authorization_sha256':pin,'claimed_at':_now().isoformat()})
            guard.reserve(2)
            receipts = []
            for kind in ('FIXTURE_DISCOVERY','MARKET_METADATA'):
                raw, started, completed = client.retrieve(kind, day, guard)
                _require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES
                         and started <= completed <= _now())
                receipt = {'kind':kind, 'payload_base64':base64.b64encode(raw).decode('ascii'),
                           'payload_sha256':fingerprint(raw), 'started_at':started.isoformat(),
                           'retrieved_at':completed.isoformat(), 'authorization_sha256':pin}
                name = acquisition._name('eplprepresponse', {'claim':key,'kind':kind})
                store.put(name, receipt)
                _require(store.get(name) == receipt)
                # Retain even malformed response bytes; no successful preparation on error.
                _require(type(_json(raw)) is list)
                receipts.append({'name':name,'sha256':fingerprint(encoded(receipt))})
            check()
            result.update(status='RETAINED_UNREVIEWED', receipts=receipts,
                          authorization_sha256=pin, publication_allowed=False)
            store.put(acquisition._name('eplprepcomplete',key), result)
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        # Never print transport URLs, bodies, keys or arbitrary exception strings.
        result.update(status='HOLD', reason='PREPARATION_INCOMPLETE_OR_REJECTED')
    return result


def capture(*, enabled=False, state_dir=None, private_dir=None, calendar_path=None,
            metadata_path=None, data_config_path=None, authorization_path=None, epl_freeze=None,
            authorization_sha256=None):
    """Explicit operator handoff after separate review of authentic preparation.

    epl_freeze is the existing trusted saved-input contract; it must contain an
    independently approved qualification. Caller cannot supply a clock/client.
    """
    if enabled is not True:
        return {'status':'DISABLED','provider_requests':0}
    return acquisition.run_once(state_dir=state_dir, private_dir=private_dir,
        calendar_path=calendar_path, metadata_path=metadata_path, data_config_path=data_config_path,
        authorization_path=authorization_path, epl_freeze=epl_freeze,
        retain_handoff=True, epl_one_shot=True, authorization_sha256=authorization_sha256)
