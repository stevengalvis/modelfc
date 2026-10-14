"""Render a disabled proposal to stdout using separately verified host identities."""
import argparse
from pathlib import Path


def runtime_id(value):
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**32 - 1:
        raise argparse.ArgumentTypeError('a verified non-root numeric runtime identity is required')
    return str(int(value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-uid', type=runtime_id, required=True)
    parser.add_argument('--runtime-gid', type=runtime_id, required=True)
    args = parser.parse_args()
    template = Path(__file__).resolve().parents[2] / 'deploy/proposals/modelfc-morning-refresh.service.in'
    print(template.read_text().replace('@RUNTIME_UID@', args.runtime_uid)
          .replace('@RUNTIME_GID@', args.runtime_gid), end='')


if __name__ == '__main__':
    main()
