import unittest
from unittest.mock import MagicMock, Mock, patch

from agent.warehouse.shopify_bulk import BulkClient
from agent.warehouse.shopify_token import (
    fetch_shopify_access_token,
    invalidate_shopify_token,
    shopify_access_token,
)


class FetchShopifyAccessTokenTests(unittest.TestCase):
    def tearDown(self):
        invalidate_shopify_token()

    def test_client_credentials_exchange(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'access_token': 'shpat_synthetic', 'expires_in': 86399}
        with patch.dict('os.environ', {'SHOPIFY_SHOP_DOMAIN': 'test.myshopify.com',
                        'SHOPIFY_CLIENT_ID': 'id', 'SHOPIFY_CLIENT_SECRET': 'secret'}), \
                patch('agent.warehouse.shopify_token.requests.post', return_value=response) as post:
            granted = fetch_shopify_access_token()
        self.assertEqual(granted['token'], 'shpat_synthetic')
        self.assertEqual(granted['expires_in'], 86399)
        body = post.call_args.kwargs['data']
        self.assertEqual(body['grant_type'], 'client_credentials')
        self.assertNotIn('shpat_synthetic', str(post.call_args))

    def test_rejected_exchange_sanitized(self):
        response = MagicMock(status_code=401, text='secret leak')
        with patch.dict('os.environ', {'SHOPIFY_SHOP_DOMAIN': 'test.myshopify.com',
                        'SHOPIFY_CLIENT_ID': 'id', 'SHOPIFY_CLIENT_SECRET': 'secret'}), \
                patch('agent.warehouse.shopify_token.requests.post', return_value=response):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_shopify_access_token()
        self.assertNotIn('secret leak', str(ctx.exception))

    def test_missing_expires_in_rejected(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'access_token': 'shpat_synthetic'}
        with patch.dict('os.environ', {'SHOPIFY_SHOP_DOMAIN': 'test.myshopify.com',
                        'SHOPIFY_CLIENT_ID': 'id', 'SHOPIFY_CLIENT_SECRET': 'secret'}), \
                patch('agent.warehouse.shopify_token.requests.post', return_value=response):
            with self.assertRaises(RuntimeError):
                fetch_shopify_access_token()

    def test_static_token_fallback(self):
        with patch.dict('os.environ', {'SHOPIFY_SHOP_DOMAIN': 'test.myshopify.com',
                        'SHOPIFY_ADMIN_ACCESS_TOKEN': 'shpat_legacy'}):
            granted = fetch_shopify_access_token()
        self.assertEqual(granted, {'token': 'shpat_legacy', 'expires_in': None})

    def test_no_credentials_rejected(self):
        with patch.dict('os.environ', {'SHOPIFY_SHOP_DOMAIN': 'test.myshopify.com'}, clear=False):
            import os
            os.environ.pop('SHOPIFY_CLIENT_ID', None)
            os.environ.pop('SHOPIFY_CLIENT_SECRET', None)
            os.environ.pop('SHOPIFY_ADMIN_ACCESS_TOKEN', None)
            with self.assertRaises(RuntimeError):
                fetch_shopify_access_token()


class ShopifyAccessTokenCacheTests(unittest.TestCase):
    def setUp(self):
        invalidate_shopify_token()

    def tearDown(self):
        invalidate_shopify_token()

    def test_cached_until_expiry_margin(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'access_token': 'shpat_a', 'expires_in': 86399}
        env = {'SHOPIFY_SHOP_DOMAIN': 'test.myshopify.com',
               'SHOPIFY_CLIENT_ID': 'id', 'SHOPIFY_CLIENT_SECRET': 'secret'}
        with patch.dict('os.environ', env), \
                patch('agent.warehouse.shopify_token.requests.post', return_value=response) as post:
            self.assertEqual(shopify_access_token(), 'shpat_a')
            self.assertEqual(shopify_access_token(), 'shpat_a')
            self.assertEqual(post.call_count, 1)

    def test_invalidate_forces_refetch(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'access_token': 'shpat_a', 'expires_in': 86399}
        env = {'SHOPIFY_SHOP_DOMAIN': 'test.myshopify.com',
               'SHOPIFY_CLIENT_ID': 'id', 'SHOPIFY_CLIENT_SECRET': 'secret'}
        with patch.dict('os.environ', env), \
                patch('agent.warehouse.shopify_token.requests.post', return_value=response) as post:
            shopify_access_token()
            invalidate_shopify_token()
            shopify_access_token()
            self.assertEqual(post.call_count, 2)

    def test_static_token_never_expires(self):
        env = {'SHOPIFY_SHOP_DOMAIN': 'test.myshopify.com',
               'SHOPIFY_ADMIN_ACCESS_TOKEN': 'shpat_legacy'}
        with patch.dict('os.environ', env):
            self.assertEqual(shopify_access_token(), 'shpat_legacy')
            self.assertEqual(shopify_access_token(), 'shpat_legacy')


class BulkClientTokenProviderTests(unittest.TestCase):
    def test_provider_resolved_per_request(self):
        tokens = iter(['shpat_first', 'shpat_second'])
        client = BulkClient('test.myshopify.com', lambda: next(tokens))
        response = MagicMock(status_code=200, headers={'X-Shopify-API-Version': '2026-04'})
        response.json.return_value = {'data': {'shop': {'id': 'gid://shopify/Shop/1',
                                                        'myshopifyDomain': 'test.myshopify.com'}}}
        with patch('agent.warehouse.shopify_bulk.requests.post', return_value=response) as post:
            client.verify_shop('gid://shopify/Shop/1')
        self.assertEqual(post.call_args.kwargs['headers']['X-Shopify-Access-Token'], 'shpat_first')

    def test_401_with_provider_retries_once_with_new_token(self):
        tokens = iter(['shpat_stale', 'shpat_fresh'])
        client = BulkClient('test.myshopify.com', lambda: next(tokens))
        rejected = MagicMock(status_code=401)
        accepted = MagicMock(status_code=200, headers={'X-Shopify-API-Version': '2026-04'})
        accepted.json.return_value = {'data': {'shop': {'id': 'gid://shopify/Shop/1',
                                                        'myshopifyDomain': 'test.myshopify.com'}}}
        with patch('agent.warehouse.shopify_bulk.requests.post', side_effect=[rejected, accepted]) as post:
            result = client.verify_shop('gid://shopify/Shop/1')
        self.assertEqual(result, 'gid://shopify/Shop/1')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[1].kwargs['headers']['X-Shopify-Access-Token'], 'shpat_fresh')

    def test_401_with_provider_twice_raises(self):
        tokens = iter(['shpat_stale', 'shpat_stale2'])
        client = BulkClient('test.myshopify.com', lambda: next(tokens))
        rejected = MagicMock(status_code=401)
        with patch('agent.warehouse.shopify_bulk.requests.post', side_effect=[rejected, rejected]):
            with self.assertRaises(RuntimeError):
                client.verify_shop('gid://shopify/Shop/1')

    def test_401_with_static_token_no_retry(self):
        client = BulkClient('test.myshopify.com', 'shpat_static')
        rejected = MagicMock(status_code=401)
        with patch('agent.warehouse.shopify_bulk.requests.post', return_value=rejected) as post:
            with self.assertRaises(RuntimeError):
                client.verify_shop('gid://shopify/Shop/1')
        self.assertEqual(post.call_count, 1)

    def test_error_carries_status(self):
        client = BulkClient('test.myshopify.com', 'shpat_static')
        rejected = MagicMock(status_code=403)
        with patch('agent.warehouse.shopify_bulk.requests.post', return_value=rejected):
            with self.assertRaises(RuntimeError) as ctx:
                client.verify_shop('gid://shopify/Shop/1')
        self.assertEqual(ctx.exception.status, 403)

    def test_static_token_stripped_and_validated(self):
        with self.assertRaises(RuntimeError):
            BulkClient('test.myshopify.com', '   ')


if __name__ == '__main__':
    unittest.main()
