import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AIProviderConfigTest(unittest.TestCase):
    def test_local_dialog_stdin_does_not_echo_secret_or_call_paid_model(self):
        import contextlib,io
        from scripts.configure_ai import main
        with tempfile.TemporaryDirectory() as folder:
            secret='offline-dialog-secret'
            payload=json.dumps({'api_key':secret,'model':'dialog-model'})
            stdout=io.StringIO()
            with patch('sys.stdin',io.StringIO(payload)),contextlib.redirect_stdout(stdout):
                main(['--root',folder,'--url','https://ai.opendoor.sbs','--configure-stdin'])
            self.assertNotIn(secret,stdout.getvalue())
            self.assertTrue(json.loads(stdout.getvalue())['key_configured'])

    def test_empty_or_control_key_rejected_before_authentication(self):
        from scripts.configure_ai import main
        for key in ('','\x16'):
            with patch('scripts.configure_ai.getpass.getpass',return_value=key),patch('scripts.configure_ai.available_models') as models:
                with self.assertRaises(ValueError):main(['--url','https://ai.opendoor.sbs'])
                models.assert_not_called()

    def test_windows_sid_reads_native_bytes_without_utf8_assumption(self):
        from backend.integrations.ozon.credential_store import _current_user_sid
        from types import SimpleNamespace
        with patch('backend.integrations.ozon.credential_store.subprocess.run',return_value=SimpleNamespace(stdout=b'"\xd7\xd3\xc8\xeb","S-1-5-21-1234-1001"\r\n')):
            self.assertEqual('S-1-5-21-1234-1001',_current_user_sid())
        with patch('backend.integrations.ozon.credential_store.subprocess.run',return_value=SimpleNamespace(stdout=None)):
            with self.assertRaises(OSError):_current_user_sid()
    def test_model_credentials_do_not_collide_with_ozon_shop_named_ai(self):
        from backend.integrations.ai.provider_config import configure, settings
        from scripts.workbench_stores import upsert_store
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            configure(root, {'base_url':'https://ai.opendoor.sbs','model':'configured-model','api_key':'test-only-key'})
            upsert_store(root, {'id':'ai','display_name':'Shop','client_id':'shop-client','api_key':'shop-key'})
            self.assertEqual('test-only-key', settings(root)['key'])

    def test_local_cli_hidden_input_does_not_print_key(self):
        import contextlib
        import io
        from scripts.configure_ai import main
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), \
             patch('getpass.getpass', return_value='test-only-key'), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main(['--root',folder,'--url','https://ai.opendoor.sbs','--model','configured-model']))
            self.assertNotIn('test-only-key', output.getvalue())

    def test_gateway_config_keeps_key_server_only_and_reloads_without_env(self):
        from backend.integrations.ai.provider_config import configure, settings, public_settings
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            value = configure(root, {'base_url':'https://ai.opendoor.sbs', 'model':'configured-model',
                                     'api_mode':'responses', 'api_key':'test-only-key'})
            self.assertNotIn('test-only-key', json.dumps(value))
            self.assertTrue(value['key_configured'])
            self.assertEqual('https://ai.opendoor.sbs/v1', settings(root)['base_url'])
            self.assertEqual('test-only-key', settings(root)['key'])
            self.assertNotIn('test-only-key', (root/'config/ai-provider.json').read_text(encoding='utf-8'))
            configure(root, {'base_url':'https://ai.opendoor.sbs/v1', 'model':'other-model', 'api_mode':'chat_completions'})
            self.assertEqual('test-only-key', settings(root)['key'])
            self.assertEqual('chat_completions', public_settings(root)['api_mode'])

    def test_gateway_config_keeps_multiple_selectable_text_models(self):
        from backend.integrations.ai.provider_config import configure, public_settings, settings
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            value = configure(root, {'base_url':'https://ai.opendoor.sbs', 'model':'text-a',
                                     'models':['text-a','text-b','text-c'], 'api_key':'test-only-key'})
            self.assertEqual(['text-a','text-b','text-c'], value['models'])
            self.assertEqual(['text-a','text-b','text-c'], public_settings(root)['models'])
            self.assertEqual('text-a', settings(root)['model'])
            self.assertNotIn('test-only-key', json.dumps(value))

    def test_gateway_config_rejects_image_models_for_text_generation(self):
        from backend.integrations.ai.provider_config import configure
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                configure(Path(folder), {'base_url':'https://ai.opendoor.sbs', 'model':'gpt-image-2',
                    'models':['gpt-image-2'], 'api_key':'test-only-key'})
            self.assertFalse((Path(folder)/'ozon-adapter/.env.ai').exists())

    def test_secure_dialog_saves_all_selected_models_without_echoing_key(self):
        import contextlib, io
        from scripts.configure_ai import main
        with tempfile.TemporaryDirectory() as folder:
            secret='offline-multi-model-secret'
            payload=json.dumps({'api_key':secret,'model':'text-a','models':['text-a','text-b','text-c']})
            stdout=io.StringIO()
            with patch('sys.stdin',io.StringIO(payload)),contextlib.redirect_stdout(stdout):
                main(['--root',folder,'--url','https://ai.opendoor.sbs','--configure-stdin'])
            result=json.loads(stdout.getvalue())
            self.assertEqual(['text-a','text-b','text-c'],result['models'])
            self.assertNotIn(secret,stdout.getvalue())

    def test_private_or_unapproved_endpoint_cannot_receive_key(self):
        from backend.integrations.ai.provider_config import configure
        with tempfile.TemporaryDirectory() as folder:
            for url in ('http://ai.opendoor.sbs', 'https://127.0.0.1/v1', 'https://other.invalid', 'https://ai.opendoor.sbs/v1?key=hidden'):
                with self.assertRaises(ValueError):
                    configure(Path(folder), {'base_url':url, 'model':'test', 'api_mode':'responses', 'api_key':'test-only-key'})
            self.assertFalse((Path(folder)/'ozon-adapter/.env.ai').exists())


if __name__ == '__main__': unittest.main()
