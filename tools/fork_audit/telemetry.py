#!/usr/bin/env python3
"""Tripwire for reviewed optional-telemetry sinks, not a network privacy proof."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(
    r'Api\.functions\.(?:help\.saveAppLog|messages\.reportReadMetrics|messages\.reportMusicListen|phone\.saveCallDebug|phone\.saveCallLog)\s*\('
    r'|\bAppCenter\.start\s*\('
    r'|\b(?:MusicListenTracker|MessageReadMetricsTracker|NetworkStatsContext)\s*\('
    r'|\bmanagedNetworkTelemetryReports\s*\(\s*postbox\s*:'
)


def violations(root):
    failures = []
    for path in sorted((root / 'submodules').glob('*/Sources/**/*.swift')):
        # The report function declaration is retained for low-level formatter
        # tests, but production invocation is not allowed.
        source = path.read_text()
        source = re.sub(r'func managedNetworkTelemetryReports\(', 'func retainedReporterDeclaration(', source)
        if FORBIDDEN.search(source):
            failures.append(str(path.relative_to(root)))
    build = root / 'submodules/TelegramUI/BUILD'
    if build.exists() and '@appcenter_sdk' in build.read_text():
        failures.append(str(build.relative_to(root)))
    return failures


if __name__ == '__main__':
    paths = violations(ROOT)
    if paths:
        print('Blocked optional telemetry source paths: ' + ', '.join(paths), file=sys.stderr)
        raise SystemExit(1)
    print('PASS: reviewed automatic telemetry sinks/collectors absent; not a runtime network guarantee.')
