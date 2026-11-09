"""Inactive one-call composition. No CLI, key reader, target discovery or retries.

Trusted caller supplies explicit approved manifest/bytes and a transport factory;
only that factory may obtain credentials, after readiness and durable reservation.
The injected boundaries are trusted controller code, never provider-selected.
"""
from .canonical_admission import Receipt, canonical, digest
from .one_file_adapter import prepare, bind_first_reservation, validate_candidate, review_patch
from .one_file_transport import parse_response, transport_observation


def run_once(manifest, source, tests, harness, *, gate, target, transport_factory,
             readiness=None, runtime=None):
    if readiness is None or runtime is None:
        from .one_file_sandbox import verify_readiness, test_candidate
        readiness = readiness or verify_readiness
        runtime = runtime or test_candidate
    prepared = prepare(manifest, source, tests, harness)
    reservation = None
    transport = None
    result = {'status': 'blocked', 'phase': 'readiness', 'patch': None}
    bound_container = None
    try:
        if readiness(manifest, target) is not True:
            raise ValueError('readiness_unverified')
        result['phase'] = 'admission'
        gate.register(manifest, source, tests, harness)
        reservation = gate.reserve(manifest, source, tests, harness)
        bind_first_reservation(manifest, prepared, reservation, source, tests, harness)
        result.update(reservation=reservation, wire_hash=prepared.wire_hash,
                      manifest_identity=manifest.identity, status='unknown')
        result['phase'] = 'provider'
        transport = transport_factory(manifest, source, tests, harness, reservation)
        raw = transport()
        result['phase'] = 'response'
        candidate, usage = parse_response(manifest, raw)
        result.update(response_hash=digest(raw), usage=usage)
        result['phase'] = 'candidate'
        candidate_hash = validate_candidate(manifest, source, tests, harness, candidate)
        result['candidate_hash'] = candidate_hash
        result['phase'] = 'sandbox'

        def record_identity(container_id):
            nonlocal bound_container
            if bound_container is not None:
                raise ValueError('duplicate_container')
            if gate.bind_container(manifest, 1, reservation['sandbox_name'], container_id) is not True:
                raise ValueError('container_binding_unverified')
            bound_container = container_id
            return True

        observed = runtime(manifest, source, tests, harness, candidate, reservation, target,
                           identity_recorder=record_identity)
        result['runtime'] = observed
        if (type(observed) is not dict or observed.get('status') not in ('passed', 'tests_failed')
                or observed.get('cleanup_verified') is not True
                or bound_container is None or observed.get('container_id') != bound_container
                or observed.get('sandbox_name') != reservation['sandbox_name']
                or observed.get('image_id') != manifest.image_id):
            raise ValueError('runtime_unverified')
        failed = observed.get('failed')
        if (type(failed) is not list or any(type(name) is not str for name in failed)
                or len(set(failed)) != len(failed) or not set(failed).issubset(manifest.test_names)
                or (observed['status'] == 'passed') != (not failed)):
            raise ValueError('test_evidence_unverified')
        execution = observed.get('diagnostic')
        cleanup = observed.get('cleanup_evidence')
        if type(execution) is not dict or not execution or type(cleanup) is not dict or not cleanup:
            raise ValueError('missing_runtime_evidence')
        receipt = Receipt(
            'pass' if not failed else 'test_failure', digest(raw), candidate_hash,
            bound_container, reservation['sandbox_name'], manifest.image_id,
            tuple((name, name not in failed) for name in manifest.test_names),
            usage['input_tokens'], usage['output_tokens'], len(raw), len(candidate),
            digest(canonical(execution).encode()), digest(canonical(cleanup).encode()), True)
        result['phase'] = 'receipt'
        gate.record(manifest, 1, receipt)
        result['status'] = receipt.outcome
        # Only independently verified tests + complete cleanup + durable receipt
        # produce the proposed patch. No application, commit or publication.
        if not failed:
            result['patch'] = review_patch(manifest, source, tests, harness, candidate)
        result['phase'] = 'complete'
    except BaseException:
        # Missing evidence remains reserved; no invented receipt and no retry.
        result['status'] = 'unknown' if reservation is not None else 'blocked'
        result['patch'] = None
    finally:
        if transport is not None:
            result['transport'] = transport_observation(transport)
            try:
                transport.close()
            except BaseException:
                result.update(status='unknown', patch=None)
    return result
