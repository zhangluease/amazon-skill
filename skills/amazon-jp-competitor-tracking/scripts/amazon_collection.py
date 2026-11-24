"""HTTP-first Amazon quotes with a bounded, persistent browser fallback."""
import json
from pathlib import Path
import re
import subprocess

from amazon_product import AmazonError, AmazonProductClient
from competitor_core import write_json


class AmazonQuoteClient:
    def __init__(self, state_dir, *, interval=1, mode='headless', node='node', http=None, runner=None):
        self.directory = Path(state_dir)
        self.mode, self.node, self.runner = mode, node, runner or subprocess.run
        self.http, self.http_setup_error = http, None
        if self.http is None:
            try:
                self.http = AmazonProductClient(interval=interval, cookie_file=self.directory / 'amazon-http-cookies.txt')
            except AmazonError as error:
                self.http_setup_error = str(error)
        self.verification_asins = []
        self.browser_offers = 0

    def fetch(self, asin):
        if not re.fullmatch(r'[A-Z0-9]{10}', asin):
            raise ValueError('INVALID_ASIN')
        gate = self.directory / 'amazon-browser-status.json'
        try:
            paused = json.loads(gate.read_text())
        except (OSError, ValueError):
            paused = {}
        if paused.get('status') == 'verification_required':
            self.verification_asins.append(asin)
            return {'status': 'failed', 'category': paused.get('category', 'AMAZON_HUMAN_VERIFICATION_REQUIRED'),
                    'endpoint': 'https://www.amazon.co.jp/dp/' + asin, 'not_attempted': True,
                    'requires_visible_browser': paused.get('category') != 'AMAZON_HTTP_429', 'browser_started': False,
                    'delivery_destination': {'country_code': 'JP', 'verified': False}}
        result = self.http.fetch(asin) if self.http is not None else {
            'status': 'failed', 'category': self.http_setup_error, 'stage': 'http_session_setup',
            'delivery_destination': {'country_code': 'JP', 'verified': False},
        }
        if result.get('category') == 'AMAZON_HTTP_429':
            # A rate limit is a stop signal, not a reason to issue another browser request.
            write_json(gate, {'status': 'verification_required', 'category': 'AMAZON_HTTP_429'})
            self.verification_asins.append(asin)
            result['requires_visible_browser'] = False
            return result
        # Absent reference price/discount can be legitimate, and do not justify extra requests.
        if self.mode == 'off' or result.get('status') == 'ok' and result.get('data', {}).get('price') is not None:
            return result
        try:
            worker = self.runner([self.node, str(Path(__file__).with_name('amazon_browser.cjs')),
                                  asin, str(self.directory), 'headless'],
                                 capture_output=True, text=True, timeout=600)
            browser = json.loads(worker.stdout)
            if not isinstance(browser, dict):
                raise ValueError()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            browser = {'status': 'failed', 'category': 'AMAZON_BROWSER_WORKER_FAILED'}
        if browser.get('status') == 'ok':
            if (browser.get('data', {}).get('asin') != asin
                    or browser.get('delivery_destination', {}).get('verified') is not True):
                browser = {'status': 'failed', 'category': 'AMAZON_PRODUCT_NOT_CONFIRMED'}
            else:
                self.browser_offers += 1
                browser['http_attempt'] = {key: result[key] for key in ('status', 'category', 'stage') if key in result}
                return browser
        if browser.get('status') == 'verification_required':
            self.verification_asins.append(asin)
        result['browser_fallback'] = browser
        return result
