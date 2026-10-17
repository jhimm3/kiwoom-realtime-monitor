"""Installed root-owned SSH session entry; fixed actions and existing operator fence.

No candidate code runs on the host. Only the verified active app's fixed
container-only importer receives bounded stdin. OAuth never enters HTTP/logs.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, '/usr/local/libexec/kiwoom-nas')
import nas_operator as op


def main():
    try:
        op.require(os.geteuid() == 0 and len(sys.argv) == 2 and sys.argv[1] in ('prepare', 'import'), 'invalid_session_action')
        config = op.load_config()
        op.require(os.environ.get('SUDO_UID') == str(config['allowed_uid']), 'operator_identity_required')
        data = None
        if sys.argv[1] == 'import':
            data = sys.stdin.buffer.read(128 * 1024 + 1)
            op.require(0 < len(data) <= 128 * 1024, 'session_payload_size')
        with op.Tree(config['private'], owners=(0,), protected=True) as private:
            operator = op.Operator(config, private)
            with operator.fence() as store:
                active = op.identifier(store.json('active.json')['release_id'])
                manifest, unused = operator.source(active)
                operator.publish(store, manifest)
                script = '/app/source-runtime/releases/' + active + '/scripts/nas_chatgpt_session.py'
                op.require('scripts/nas_chatgpt_session.py' in manifest['files'], 'session_import_source_not_deployed')
                result = subprocess.run([config['docker'], '--host', 'unix:///var/run/docker.sock',
                    'exec', '-i', config['server_id'], 'python', '-I', script, sys.argv[1]],
                    env=op.CLEAN_ENV, cwd='/', input=data, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=45)
                op.require(result.returncode == 0 and len(result.stdout) <= 8192, 'session_import_failed')
                value = json.loads(result.stdout)
                op.require(type(value) is dict and set(value) <= {'host_id', 'empty', 'imported', 'client_id'}
                           and type(value.get('host_id')) is str and value['host_id'].startswith('urn:uuid:'),
                           'invalid_session_ack')
                print(json.dumps(value))
        return 0
    except Exception as error:
        print(json.dumps({'error_code': str(error) if isinstance(error, op.Rejected) else 'session_import_failed'}))
        return 1


if __name__ == '__main__': raise SystemExit(main())
