"""Run inside the worker; emits only store identity/scopes, never secret contents."""
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.warehouse.shopify_token import fetch_shopify_access_token

QUERY = '''query WorkerConnectionCheck {
  shop { id name myshopifyDomain }
  currentAppInstallation { accessScopes { handle } }
}'''


def main():
    domain = os.environ.get('SHOPIFY_SHOP_DOMAIN', '')
    version = os.environ.get('SHOPIFY_API_VERSION', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com', domain):
        raise ValueError('Invalid configured shop domain')
    if not re.fullmatch(r'20[0-9]{2}-(01|04|07|10)', version):
        raise ValueError('Missing API version')
    try:
        token = fetch_shopify_access_token()['token']
    except Exception as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__}))
        return 1
    response = requests.post(f'https://{domain}/admin/api/{version}/graphql.json',
        headers={'X-Shopify-Access-Token': token}, json={'query': QUERY},
        timeout=(10, 30), allow_redirects=False)
    if response.status_code != 200:
        print(json.dumps({'ok': False, 'http_status': response.status_code,
                          'error': 'Shopify connection rejected; response body suppressed'}))
        return 1
    body = response.json()
    if body.get('errors') or not body.get('data', {}).get('shop'):
        print(json.dumps({'ok': False, 'error': 'GraphQL returned errors; response details suppressed'}))
        return 1
    shop = body['data']['shop']
    if shop['myshopifyDomain'].lower() != domain:
        raise ValueError('Returned store does not match configured domain')
    scopes = sorted(s['handle'] for s in body['data']['currentAppInstallation']['accessScopes'])
    actual_version = response.headers.get('X-Shopify-API-Version')
    print(json.dumps({'ok': actual_version == version, 'shop': shop, 'scopes': scopes,
                      'requested_api_version': version, 'actual_api_version': actual_version,
                      'orders_scope': bool({'read_orders', 'write_orders'} & set(scopes)),
                      'all_orders_scope': 'read_all_orders' in scopes}))
    return 0 if actual_version == version else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__}))
        sys.exit(1)
