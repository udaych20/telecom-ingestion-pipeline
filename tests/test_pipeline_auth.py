import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from azure.core.credentials import AccessToken
from pipeline_auth import CachedCredential, check_cosmos_authentication


class AuthenticationTests(unittest.TestCase):
    def test_ten_workers_share_one_token_and_refresh(self):
        source = Mock()
        source.get_token.return_value = AccessToken("test", int(time.time()) + 3600)
        credential = CachedCredential(source)
        barrier = threading.Barrier(10)
        def request(_):
            barrier.wait(timeout=5)
            return credential.get_token("scope")
        with ThreadPoolExecutor(max_workers=10) as executor:
            self.assertEqual(len(list(executor.map(request, range(10)))), 10)
        self.assertEqual(source.get_token.call_count, 1)
        credential._tokens[(("scope",), "{}")] = AccessToken("expired", 0)
        credential.get_token("scope")
        self.assertEqual(source.get_token.call_count, 2)
        credential.get_token("scope", claims="challenge")
        credential.get_token("other-scope")
        self.assertEqual(source.get_token.call_count, 4)

    def test_failed_refresh_is_not_cached(self):
        source = Mock()
        source.get_token.side_effect = [RuntimeError("CLI failed"), AccessToken("test", int(time.time()) + 3600)]
        credential = CachedCredential(source)
        with self.assertRaisesRegex(RuntimeError, "before starting workers"):
            check_cosmos_authentication(credential)
        check_cosmos_authentication(credential)
        self.assertEqual(source.get_token.call_count, 2)
