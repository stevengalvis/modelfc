"""Offline v4 bridge input; a digest binding is not collector authentication.

The trusted caller must authenticate the retained capture and supply its
independently reviewed snapshot digest. This module performs no host reads.
"""
import copy
import re

import owner_context_v4

EXPECTED = owner_context_v4.EXPECTED


def amend_dependencies(deps, evidence):
    if type(evidence) is not dict or set(evidence) != {
        'snapshot', 'reviewed_snapshot_sha256'
    }:
        raise ValueError('OWNER_BINDING_SCHEMA')
    expected = evidence['reviewed_snapshot_sha256']
    if type(expected) is not str or re.fullmatch('[0-9a-f]{64}', expected) is None:
        raise ValueError('OWNER_BINDING_DIGEST')
    snapshot = copy.deepcopy(evidence['snapshot'])
    digest = owner_context_v4.validate(snapshot, deps['dependencies'])
    if digest != expected:
        raise ValueError('OWNER_BINDING_MISMATCH')
    # Validate and bind before changing even the caller's disposable plan.
    return owner_context_v4.amend_dependencies(deps, snapshot)
