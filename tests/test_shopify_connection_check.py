import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from infra.scripts.check_shopify_connection import main


class ShopifyConnectionCheckTests(unittest.TestCase):
    def invoke(self, response):
        output = io.StringIO()
        with patch.dict('os.environ', {'SHOPIFY_SHOP_DOMAIN': 'sobrecodigo.myshopify.com',
                        'SHOPIFY_API_VERSION': '2026-04', 'SHOPIFY_ADMIN_ACCESS_TOKEN': 'synthetic-secret'}), \
                patch('infra.scripts.check_shopify_connection.requests.post', return_value=response) as post, \
                contextlib.redirect_stdout(output):
            status = main()
        self.assertNotIn('synthetic-secret', output.getvalue())
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        return status, json.loads(output.getvalue())

    def test_rejected_token_does_not_log_response(self):
        response = Mock(status_code=401, text='synthetic-secret')
        status, result = self.invoke(response)
        self.assertEqual(status, 1)
        self.assertEqual(result['http_status'], 401)

    def test_valid_response_and_scopes(self):
        response = Mock(status_code=200, headers={'X-Shopify-API-Version': '2026-04'})
        response.json.return_value = {'data': {
            'shop': {'id': 'synthetic', 'name': 'Synthetic', 'myshopifyDomain': 'sobrecodigo.myshopify.com'},
            'currentAppInstallation': {'accessScopes': [{'handle': 'read_orders'}]}}}
        status, result = self.invoke(response)
        self.assertEqual(status, 0)
        self.assertTrue(result['orders_scope'])
        self.assertFalse(result['all_orders_scope'])

    def test_graphql_error_body_suppressed(self):
        response = Mock(status_code=200)
        response.json.return_value = {'errors': [{'message': 'synthetic-secret'}]}
        self.assertEqual(self.invoke(response)[0], 1)


if __name__ == '__main__':
    unittest.main()
