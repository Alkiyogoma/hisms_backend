"""Exercise update.sh with temporary files and simulated production commands.

No server, database, package installer or service manager is contacted.
Run: python3 -m unittest discover -s deployment/production -p 'test_update.py'
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


FAKE_COMMAND = r'''
import json, os, pathlib, shutil, sys
command = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['DEPLOY_TEST_ROOT'])
with (root / 'commands.jsonl').open('a') as stream:
    stream.write(json.dumps([command, *args]) + '\n')
failure = os.environ.get('DEPLOY_TEST_FAILURE', '')
if command == 'id':
    print(os.environ.get('DEPLOY_TEST_UID', '0'))
elif command == 'git':
    if 'rev-parse' in args:
        changed = (root / 'pulled').exists() and not os.environ.get('DEPLOY_TEST_UNCHANGED')
        print('new456' if changed else 'old123')
    elif 'branch' in args:
        print('' if failure == 'branch' else 'production')
    elif 'pull' in args:
        if failure == 'git':
            print('Simulated Git failure', file=sys.stderr); sys.exit(12)
        (root / 'pulled').touch()
        print('Git fast-forward complete')
elif command == 'rsync':
    shutil.copytree(args[-2], args[-1], dirs_exist_ok=True)
    print('Upload copy progress: 100%')
elif command == 'sudo':
    target = pathlib.Path(args[2]).name
    if target == 'pg_dump':
        print('Simulated database backup')
        if failure == 'backup': sys.exit(13)
    elif target == 'pip':
        print('Installing requirements')
        if failure == 'pip': sys.exit(14)
    elif target == 'python':
        print(' '.join(args[3:]))
        if failure == 'migrate' and 'migrate' in args: sys.exit(15)
        if failure == 'uploads' and 'collectstatic' in args:
            for file in pathlib.Path(os.environ['DEPLOY_TEST_MEDIA']).glob('*'):
                if file.is_file(): file.unlink()
elif command == 'systemctl':
    if args[0] == 'restart': (root / 'restarted').touch()
    if args[0] == 'is-active' and failure == 'service' and args[-1] == 'hodari-celery': sys.exit(3)
    if args[0] == 'status': print('Simulated service failure details')
elif command == 'curl':
    if failure == 'connection': sys.exit(7)
    print('503' if failure == 'http' else '200', end='')
'''


class ProductionUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hodari-deploy-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'app'
        self.venv = self.root / 'venv'
        self.backups = self.root / 'backups'
        self.bin = self.root / 'bin'
        (self.app / '.git').mkdir(parents=True)
        (self.venv / 'bin').mkdir(parents=True)
        self.bin.mkdir()
        self.media = self.app / 'media'
        self.media.mkdir()
        (self.media / 'student-photo.txt').write_text('Preserve this upload')
        (self.app / '.env').write_text('POSTGRES_DB=hisms_test\nPASSWORD=never-print-this-secret\n')
        for name in ('python', 'pip'):
            path = self.venv / 'bin' / name
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
        for name in ('id', 'git', 'rsync', 'sudo', 'chown', 'pg_dump', 'systemctl', 'curl'):
            path = self.bin / name
            path.write_text(f'#!{sys.executable}\n' + FAKE_COMMAND)
            path.chmod(0o755)
        source = Path(__file__).with_name('update.sh').read_text()
        for key, value in (('APP', self.app), ('VENV', self.venv), ('BACKUP_DIR', self.backups)):
            lines = source.splitlines()
            source = '\n'.join(f'{key}="{value}"' if line.startswith(key + '=') else line for line in lines) + '\n'
        self.script = self.root / 'update.sh'
        self.script.write_text(source)

    def run_update(self, **extra_env):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                   DEPLOY_TEST_ROOT=str(self.root), DEPLOY_TEST_MEDIA=str(self.media))
        env.update(extra_env)
        return subprocess.run(['bash', str(self.script)], env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)

    def test_missing_optional_media_root_uses_default_and_prints_all_steps(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(result.stdout.startswith('\nHODARI PRODUCTION DEPLOYMENT'))
        for number in range(1, 9):
            self.assertIn(f'==> {number}/8 ', result.stdout)
        self.assertIn('DEPLOYMENT SUCCESSFUL', result.stdout)
        self.assertIn('old123 -> new456', result.stdout)
        self.assertIn('hodari-celery-beat: active', result.stdout)
        self.assertIn('HTTP 200', result.stdout)
        self.assertTrue((self.backups / 'media' / 'student-photo.txt').exists())
        logs = list(self.backups.glob('deploy_*.log'))
        self.assertEqual(len(logs), 1)
        self.assertIn('DEPLOYMENT SUCCESSFUL', logs[0].read_text())
        self.assertNotIn('never-print-this-secret', result.stdout + logs[0].read_text())
        commands = [json.loads(line) for line in (self.root / 'commands.jsonl').read_text().splitlines()]
        self.assertTrue(any('pull' in command and '--ff-only' in command for command in commands))

    def test_no_new_commits_still_reports_refresh_and_restarts(self):
        result = self.run_update(DEPLOY_TEST_UNCHANGED='1')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('No new commits', result.stdout)
        self.assertTrue((self.root / 'restarted').exists())

    def test_quoted_exported_media_path_with_spaces_and_crlf(self):
        media = self.root / 'uploaded files'
        media.mkdir()
        (self.app / '.env').write_bytes(f'POSTGRES_DB="hisms_test"\r\nexport MEDIA_ROOT="{media}" # outside code\r\n'.encode())
        result = self.run_update(DEPLOY_TEST_MEDIA=str(media))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f'Uploads: {media}', result.stdout)

    def test_missing_database_setting_has_visible_error_before_deployment(self):
        (self.app / '.env').write_text('# POSTGRES_DB is deliberately missing\n')
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('POSTGRES_DB is missing', result.stdout)
        self.assertIn('DEPLOYMENT FAILED', result.stdout)
        self.assertFalse((self.root / 'pulled').exists())

    def test_missing_configuration_is_reported_after_banner(self):
        (self.app / '.env').unlink()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HODARI PRODUCTION DEPLOYMENT', result.stdout)
        self.assertIn('Configuration file not found', result.stdout)

    def test_requires_root_with_clear_invocation(self):
        result = self.run_update(DEPLOY_TEST_UID='1000')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Run this script with sudo bash', result.stdout)

    def test_step_failures_stop_before_restart_and_never_report_success(self):
        for failure, step in (('branch', 'Preparing'), ('backup', '2/8'), ('git', '3/8'),
                              ('pip', '5/8'), ('migrate', '6/8'), ('uploads', '7/8')):
            with self.subTest(failure=failure):
                marker = self.root / 'restarted'
                if marker.exists(): marker.unlink()
                result = self.run_update(DEPLOY_TEST_FAILURE=failure)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('DEPLOYMENT FAILED during: ' + step, result.stdout)
                self.assertNotIn('DEPLOYMENT SUCCESSFUL', result.stdout)
                self.assertFalse(marker.exists())

    def test_service_and_http_failures_are_not_masked(self):
        for failure in ('service', 'http', 'connection'):
            with self.subTest(failure=failure):
                result = self.run_update(DEPLOY_TEST_FAILURE=failure)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('DEPLOYMENT FAILED during: 8/8', result.stdout)
                self.assertNotIn('DEPLOYMENT SUCCESSFUL', result.stdout)


if __name__ == '__main__':
    unittest.main()
