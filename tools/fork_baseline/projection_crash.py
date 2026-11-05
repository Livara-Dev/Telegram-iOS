"""Closed crash evidence for the synthetic Projection XCTest host only.

Never publish an .ips file: it can contain account names, paths, UUIDs, process
IDs, arguments, environment variables, and unrelated process information.
"""
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat

BUNDLE = 'org.livara.ayugram.tests.native-security.projection'
PROCESS = 'ProjectionTestHost'
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_REPORTS = 4
MAX_CANDIDATES = 64
EXCEPTIONS = frozenset({
    'EXC_BAD_ACCESS', 'EXC_BAD_INSTRUCTION', 'EXC_ARITHMETIC', 'EXC_EMULATION',
    'EXC_SOFTWARE', 'EXC_BREAKPOINT', 'EXC_SYSCALL', 'EXC_MACH_SYSCALL',
    'EXC_RPC_ALERT', 'EXC_CRASH', 'EXC_RESOURCE', 'EXC_GUARD', 'EXC_CORPSE_NOTIFY',
})
SIGNALS = frozenset({
    'SIGABRT', 'SIGSEGV', 'SIGBUS', 'SIGILL', 'SIGTRAP', 'SIGKILL', 'SIGTERM',
    'SIGFPE', 'SIGSYS', 'SIGQUIT', 'SIGXCPU', 'SIGXFSZ', 'SIGEMT', 'SIGHUP',
})
NAMESPACES = frozenset({
    'DYLD', 'SIGNAL', 'CODESIGNING', 'FRONTBOARD', 'RUNNINGBOARD', 'LIBSYSTEM',
    'SPRINGBOARD', 'PORT_GUARD', 'WATCHDOG', 'ENDPOINTSECURITY', 'ASSERTIOND',
    'TCC', 'WEBKIT',
})
INDICATORS = {
    'Library missing': 'missingLibrary',
    'Symbol missing': 'missingSymbol',
    'Code Signature Invalid': 'codeSignatureInvalid',
    'Abort trap: 6': 'abort',
    'Segmentation fault: 11': 'segmentationFault',
    'Killed: 9': 'killed',
    'Breakpoint trap: 5': 'breakpoint',
    'Watchdog timeout': 'watchdog',
}


def _objects(data):
    """Accept an ordinary report or Apple's metadata + report JSON stream."""
    text = data.decode('utf-8')
    decoder = json.JSONDecoder()
    values, offset = [], 0
    while text[offset:].strip():
        if len(values) == 2:
            raise ValueError('Unexpected crash document count')
        offset += len(text[offset:]) - len(text[offset:].lstrip())
        value, offset = decoder.raw_decode(text, offset)
        if not isinstance(value, dict):
            raise ValueError('Unexpected crash document shape')
        values.append(value)
    if not values:
        raise ValueError('Empty crash document')
    return values[:-1], values[-1]


def _identity_matches(metadata, report):
    identities, processes = [], []
    for value in [*metadata, report]:
        for field in ('bundleID', 'bundleIdentifier'):
            if field in value:
                identities.append(value[field])
        bundle_info = value.get('bundleInfo')
        if isinstance(bundle_info, dict) and 'CFBundleIdentifier' in bundle_info:
            identities.append(bundle_info['CFBundleIdentifier'])
        for field in ('procName', 'app_name'):
            if field in value:
                processes.append(value[field])
    return (bool(identities or processes) and
            all(value == BUNDLE for value in identities) and
            all(value == PROCESS for value in processes))


def _dyld_names(report, termination):
    # Only inspect named dyld diagnostic fields. Arbitrary crash strings, stacks
    # and memory dumps are neither searched nor copied into the result.
    fields = [termination.get('details')]
    asi = report.get('asi')
    if isinstance(asi, dict):
        fields.append(asi.get('dyld'))
    names = {}
    for field in fields:
        values = [field] if isinstance(field, str) else field if isinstance(field, list) else []
        for value in values[:16]:
            if not isinstance(value, str) or len(value) > 8192:
                continue
            for line in value.splitlines()[:16]:
                library = re.fullmatch(r'Library not loaded: ([^\s\x00]{1,1024})', line.strip())
                if library:
                    path = PurePosixPath(library.group(1))
                    name = path.name
                    framework = re.fullmatch(r'([A-Za-z][A-Za-z0-9_+.-]{0,127})\.framework', path.parent.name)
                    if (framework and framework.group(1) == name or
                            re.fullmatch(r'lib[A-Za-z0-9_+.-]{1,127}\.dylib', name)):
                        names.setdefault('missing_library', name)
                symbol = re.fullmatch(r'Symbol not found: ((?:_|\$s)[A-Za-z0-9_$]{1,191})', line.strip())
                if symbol:
                    names.setdefault('missing_symbol', symbol.group(1))
    return names


def summarize(data):
    """Return one safe summary, or None for an unrelated/malformed .ips."""
    try:
        metadata, report = _objects(data)
        if not _identity_matches(metadata, report):
            return None
        result = {'synthetic_host_identity_verified': True}
        exception = report.get('exception')
        if isinstance(exception, dict):
            if isinstance(exception.get('type'), str) and exception['type'] in EXCEPTIONS:
                result['exception_type'] = exception['type']
            if isinstance(exception.get('signal'), str) and exception['signal'] in SIGNALS:
                result['signal'] = exception['signal']
        termination = report.get('termination')
        if isinstance(termination, dict):
            namespace, code = termination.get('namespace'), termination.get('code')
            if isinstance(namespace, str) and namespace in NAMESPACES:
                result['termination_namespace'] = namespace
                if type(code) is int and -(2**63) <= code < 2**64:
                    result['termination_code'] = code
            indicator = termination.get('indicator')
            if isinstance(indicator, str) and indicator in INDICATORS:
                result['termination_indicator'] = INDICATORS[indicator]
            if namespace == 'DYLD':
                result.update(_dyld_names(report, termination))
        return result
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return None


def capture(started_at, reports_dir=None):
    """Read only recent bounded regular .ips files; never retain their bytes.

    ``started_at`` is the fixed Projection test invocation's wall-clock start.
    A root override exists for portable fixtures, not a user-facing CLI.
    """
    report = {'probe_only_not_test_result': True, 'state': 'unavailable', 'reports': []}
    if type(started_at) not in (int, float) or not math.isfinite(started_at) or started_at < 0:
        return report
    root = Path(reports_dir) if reports_dir is not None else Path.home() / 'Library/Logs/DiagnosticReports'
    try:
        if root.is_symlink():
            return report
        root = root.resolve(strict=True)
        if not root.is_dir():
            return report
        candidates = []
        for path in root.glob('*.ips'):
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_mtime >= started_at and 0 < info.st_size <= MAX_FILE_BYTES:
                candidates.append((info.st_mtime, path))
        candidates.sort(key=lambda item: item[0], reverse=True)
        report['state'] = 'available'
        for _, path in candidates[:MAX_CANDIDATES]:
            descriptor = None
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_mtime < started_at or not 0 < info.st_size <= MAX_FILE_BYTES:
                    continue
                data = os.read(descriptor, MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    continue
                value = summarize(data)
                if value is not None:
                    report['reports'].append(value)
                    if len(report['reports']) == MAX_REPORTS:
                        break
            except (OSError, ValueError, TypeError):
                continue
            finally:
                if descriptor is not None:
                    os.close(descriptor)
    except (OSError, ValueError, TypeError, RuntimeError):
        pass
    return report
