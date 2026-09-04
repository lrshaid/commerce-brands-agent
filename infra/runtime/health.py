"""Small host-side health probe. Logs only technical health, never secrets/data."""
import json
import shutil
import subprocess


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=30, check=True).stdout


def main():
    problems = []
    mem = {}
    with open('/proc/meminfo') as handle:
        for line in handle:
            key, value = line.split(':', 1)
            mem[key] = int(value.strip().split()[0])
    memory_used = 1 - mem['MemAvailable'] / mem['MemTotal']
    if memory_used > .90:
        problems.append('Host memory above 90%')
    disk = shutil.disk_usage('/var/lib/commerce')
    if disk.used / disk.total > .80:
        problems.append('Metadata disk above 80%')
    try:
        ids = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=commerce']).split()
        if len(ids) != 4:
            problems.append(f'Expected 4 containers; found {len(ids)}')
        if ids:
            for container in json.loads(run(['docker', 'inspect', *ids])):
                state = container['State']
                if not state['Running'] or state.get('OOMKilled') or state.get('Health', {}).get('Status') == 'unhealthy':
                    problems.append(f"Unhealthy container: {container['Name']}")
        heartbeat_check = (
            'from dagster import DagsterInstance; import time; '
            'h=DagsterInstance.get().get_daemon_heartbeats(); '
            'assert h and all(time.time()-v.timestamp < 180 and not v.errors for v in h.values()), "stale daemon"'
        )
        run(['docker-compose', '-f', '/opt/commerce/compose.yaml', 'exec', '-T', 'daemon', 'python', '-c', heartbeat_check])
    except Exception as exc:
        problems.append(type(exc).__name__ + ': container/daemon check failed')
    payload = json.dumps({'ok': not problems, 'problems': problems, 'memory_used_percent': round(memory_used * 100, 1)})
    subprocess.run([
        'gcloud', 'logging', 'write', 'commerce-platform-health', payload,
        '--payload-type=json', '--severity=' + ('ERROR' if problems else 'INFO'),
        '--project=commerce-agents-dev', '--quiet',
    ], check=True, timeout=30)


if __name__ == '__main__':
    main()
