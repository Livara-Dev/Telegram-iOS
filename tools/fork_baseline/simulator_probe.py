"""Bounded launch diagnostics for the synthetic projection test host only."""
import json
from pathlib import Path
import re
import subprocess

BUNDLE = 'org.livara.ayugram.tests.local-text-filter'


def capture(root, secrets):
    root = Path(root)
    destination = root / 'build/fork-evidence'
    report = {'probe_only_not_test_result': True, 'bundle': BUNDLE, 'commands': {}}
    try:
        if (root / 'build').is_symlink() or destination.is_symlink():
            return
        destination.mkdir(parents=True, exist_ok=True)
        def command(name, args):
            try:
                result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False)
                value = result.stdout[:65536].decode('utf-8', errors='replace')
                cleaned = value
                for secret in secrets:
                    if secret:
                        cleaned = cleaned.replace(secret, '[REDACTED]')
                for prefix in sorted({str(root), str(root.resolve()), str(Path.home())}, key=len, reverse=True):
                    cleaned = cleaned.replace(prefix, '[PRIVATE_PATH]')
                report['commands'][name] = {'exit_code': result.returncode, 'output': cleaned}
                return result.returncode, value
            except (OSError, subprocess.SubprocessError):
                report['commands'][name] = {'state': 'unavailable'}
                return None, ''
        code, data = command('devices', ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'])
        if code == 0:
            devices = json.loads(data).get('devices', {})
            candidates = [d for runtime, values in devices.items() if runtime.endswith('iOS-27-0')
                          for d in values if d.get('name') == 'BAZEL_TEST_iPhone 17_27.0' and d.get('state') == 'Booted']
            # Keep only the selected synthetic device, not the complete personal list.
            report['commands']['devices']['output'] = 'matching_booted_devices=' + str(len(candidates))
            if len(candidates) == 1 and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', candidates[0].get('udid', '')):
                identifier = candidates[0]['udid']
                code, container = command('installed_host', ['xcrun', 'simctl', 'get_app_container', identifier, BUNDLE, 'app'])
                container = container.strip()
                if code == 0 and container and '\0' not in container and Path(container).is_absolute() and Path(container).suffix == '.app' and Path(container).is_dir() and not Path(container).is_symlink():
                    command('installed_signature', ['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', container])
                    # Launch only the no-account test host, after XCTest has failed.
                    command('standalone_host_launch', ['xcrun', 'simctl', 'launch', '--terminate-running-process', identifier, BUNDLE])
        with (destination / 'projection-launch-probe.json').open('x') as file:
            json.dump(report, file, indent=2)
            file.write('\n')
    except (OSError, ValueError, TypeError, KeyError):
        # Diagnostics cannot replace the original compiler/test failure.
        return
