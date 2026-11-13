import base64
import importlib.util
import json
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/pod-image-selection/scripts/inspect_sellersprite_har.py'
SPEC = importlib.util.spec_from_file_location('inspect_sellersprite_har', SCRIPT)
har = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(har)


def entry(url='https://e.sellersprite.com/v2/extension/competitor-lookup/JP?asins=B07H57Q2X8&tk=SECRET_QUERY&extra=SECRET_EXTRA'):
    return {'request': {'method': 'GET', 'url': url,
                        'headers': [{'name': 'Auth-Token', 'value': 'SECRET_HEADER'},
                                    {'name': 'Cookie', 'value': 'SECRET_COOKIE'}],
                        'cookies': [{'name': 'session', 'value': 'SECRET_COOKIE'}]},
            'response': {'status': 200, 'headers': [{'name': 'Set-Cookie', 'value': 'SECRET_SET_COOKIE'}],
                         'content': {'text': json.dumps({'code': 'OK', 'token': 'SECRET_ROOT',
                            'data': {'total': 1, 'email': 'SECRET_EMAIL', 'items': [
                                {'asin': 'B07H57Q2X8', 'parent': 'B000000001', 'units': 176,
                                 'image': 'https://m.media-amazon.com/image.jpg?auth=SECRET_IMAGE',
                                 'user': 'SECRET_USER', 'title': 'SECRET_TITLE'}]}})}}}


class SellerSpriteHarTests(unittest.TestCase):
    def test_only_business_fields_and_header_names_survive(self):
        result = har.inspect_har({'log': {'entries': [entry()]}})
        encoded = json.dumps(result)
        for secret in ['SECRET_QUERY', 'SECRET_EXTRA', 'SECRET_HEADER', 'SECRET_COOKIE',
                       'SECRET_SET_COOKIE', 'SECRET_ROOT', 'SECRET_EMAIL', 'SECRET_IMAGE', 'SECRET_USER', 'SECRET_TITLE']:
            self.assertNotIn(secret, encoded)
        record = result['requests'][0]
        self.assertIn('auth-token', record['request_header_names'])
        self.assertEqual(record['response']['sample_items'][0]['units'], 176)
        self.assertEqual(record['response']['sample_items'][0]['image_host'], 'm.media-amazon.com')

    def test_unrelated_and_signin_traffic_is_excluded(self):
        urls = ['https://e.sellersprite.com/v2/extension/signin?password=SECRET',
                'https://evil.example/v2/extension/competitor-lookup/US',
                'https://www.sellersprite.com/unrelated',
                'https://e.sellersprite.com/v2/extension/competitor-lookup/quick-view/US?asins=B07H57Q2X8']
        result = har.inspect_har({'log': {'entries': [entry(url) for url in urls]}})
        self.assertEqual(result['matching_requests'], 1)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_base64_and_missing_responses_remain_distinguishable(self):
        record = entry()
        record['response']['content']['text'] = base64.b64encode(record['response']['content']['text'].encode()).decode()
        record['response']['content']['encoding'] = 'base64'
        result = har.inspect_har({'log': {'entries': [record]}})
        self.assertEqual(result['requests'][0]['response']['business_code'], 'OK')
        record['response']['content'] = {}
        result = har.inspect_har({'log': {'entries': [record]}})
        self.assertFalse(result['requests'][0]['response']['body_available'])

    def test_post_credentials_and_unstructured_error_bodies_are_not_copied(self):
        record = entry('https://e.sellersprite.com/v2/extension/competitor-lookup/US')
        record['request']['method'] = 'POST'
        record['request']['postData'] = {'text': json.dumps({'q': 'pet house', 'token': 'SECRET'})}
        record['response']['content'] = {'text': '<html>SECRET_ERROR</html>'}
        result = har.inspect_har({'log': {'entries': [record]}})
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertFalse(result['requests'][0]['response']['json_available'])


if __name__ == '__main__':
    unittest.main()
