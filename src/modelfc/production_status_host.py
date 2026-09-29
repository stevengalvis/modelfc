"""Fixed, read-only systemd probes for the operator status command."""

import subprocess


UNITS = {
    "refresh_timer": "modelfc-corner-refresh.timer",
    "prospective_timer": "modelfc-corner-prospective.timer",
    "read_only_api": "modelfc-corner-api.service",
}


def probe_services() -> dict[str, dict[str, bool | None]]:
    result = {}
    for label, unit in UNITS.items():
        try:
            process = subprocess.run(
                ["/usr/bin/systemctl", "show", unit,
                 "--property=LoadState,ActiveState,UnitFileState", "--no-pager"],
                check=True, capture_output=True, text=True, timeout=5,
            )
            if len(process.stdout) > 4096:
                raise ValueError("unexpected systemd output")
            lines = process.stdout.splitlines()
            fields = dict(line.split("=", 1) for line in lines)
            if len(lines) != 3 or set(fields) != {"LoadState", "ActiveState", "UnitFileState"}:
                raise ValueError("unexpected systemd fields")
            result[label] = {
                "active": fields["ActiveState"] == "active" if fields["LoadState"] == "loaded" else False,
                "enabled": fields["UnitFileState"] == "enabled" if fields["LoadState"] == "loaded" else False,
            }
        except (OSError, ValueError, subprocess.SubprocessError):
            result[label] = {"active": None, "enabled": None}
    return result
