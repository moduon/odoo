# Part of Odoo. See LICENSE file for full copyright and licensing details.
import datetime

from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger

from .test_common import TestHttpBase
from .test_webjson import CSRF_USER_HEADERS


@tagged('post_install', '-at_install')
class TestHttpGreeting(TestHttpBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.jackoneill = new_test_user(cls.env, 'jackoneill', context={'lang': 'en_US'})

    def test_greeting0_matrix(self):
        test_matrix = [
            # path, database, login, expected_code, expected_pattern
            ('/test_http/greeting', False, None, 200, r"Tek'ma'te"),
            ('/test_http/greeting', True, None, 200, r"Tek'ma'te"),
            ('/test_http/greeting', True, 'public', 200, r"Tek'ma'te"),
            ('/test_http/greeting', True, 'jackoneill', 200, r"Tek'ma'te"),
            ('/test_http/greeting-none', False, None, 200, r"Tek'ma'te"),
            ('/test_http/greeting-none', True, None, 200, r"Tek'ma'te"),
            ('/test_http/greeting-none', True, 'public', 200, r"Tek'ma'te"),
            ('/test_http/greeting-none', True, 'jackoneill', 200, r"Tek'ma'te"),
            ('/test_http/greeting-public', False, None, 404, r"Not Found"),
            ('/test_http/greeting-public', True, None, 200, r"Tek'ma'te"),
            ('/test_http/greeting-public', True, 'public', 200, r"Tek'ma'te"),
            ('/test_http/greeting-public', True, 'jackoneill', 200, r"Tek'ma'te"),
            ('/test_http/greeting-user', False, None, 404, r"Not Found"),
            ('/test_http/greeting-user', True, None, 303, r".*/web/login.*"),
            ('/test_http/greeting-user', True, 'public', 303, r".*/web/login.*"),
            ('/test_http/greeting-user', True, 'jackoneill', 200, r"Tek'ma'te"),
            ('/test_http/greeting-bearer', False, None, 404, r"Not Found"),
            ('/test_http/greeting-bearer', True, None, 401, r".*Unauthorized.*"),
            ('/test_http/greeting-bearer', True, 'public', 401, r".*Unauthorized.*"),
            ('/test_http/greeting-bearer', True, 'jackoneill', 200, r"Tek'ma'te"),
        ]

        for path, withdb, login, expected_code, expected_pattern in test_matrix:
            with self.subTest(path=path, withdb=withdb, login=login):
                if withdb:
                    if login == 'public':
                        self.authenticate(None, None)
                    elif login:
                        self.authenticate(login, login)
                    res = self.db_url_open(path, allow_redirects=False, headers=CSRF_USER_HEADERS)
                else:
                    res = self.nodb_url_open(path, allow_redirects=False)

                self.assertEqual(res.status_code, expected_code)
                self.assertRegex(res.text, expected_pattern)

                if withdb and login:
                    self.logout(keep_db=False)

    def test_greeting0_matrix_bearer(self):
        self.authenticate(None, None)
        joe = new_test_user(self.env, 'joe', context={'lang': 'en_US'})
        joe = joe.with_user(joe)
        key_expiration = datetime.datetime.now() + datetime.timedelta(days=0.5)
        key = joe.env['res.users.apikeys']._generate('rpc', 'test', key_expiration)

        for path, authorization, expected_code, expected_pattern in [
            ('/test_http/greeting-bearer', None, 401, r".*Unauthorized.*"),
            ('/test_http/greeting-bearer', 'invalid', 401, r".*Unauthorized.*"),
            ('/test_http/greeting-bearer', "Bearer invalidkey2345", 401, r".Unauthorized"),
            ('/test_http/greeting-bearer', f"Bearer {key}", 200, r"Tek'ma'te.*=joe"),
            ('/test_http/greeting-user', f"Bearer {key}", 303, r".*/web/login.*"),
        ]:
            with self.subTest(path=path, authorization=authorization):
                headers = {"Authorization": authorization} if authorization else None
                res = self.db_url_open(path, headers=headers, allow_redirects=False)

                self.assertRegex(res.text, expected_pattern)
                self.assertEqual(res.status_code, expected_code)
                self.logout()

        with self.subTest("jackoneill with joe's token"):
            self.authenticate("jackoneill", "jackoneill")
            with mute_logger('odoo.http'):
                res = self.db_url_open(
                    '/test_http/greeting-bearer',
                    headers={"Authorization": f"Bearer {key}"},
                )
            self.assertEqual(res.status_code, 403)
            self.assertRegex(res.text, r".*does not match the used apikey")
            self.logout(keep_db=False)

        with self.subTest("joe with no Sec- headers"):
            self.authenticate("jackoneill", "jackoneill")
            with mute_logger('odoo.http'):
                res = self.db_url_open('/test_http/greeting-bearer')
            self.assertEqual(res.status_code, 401)
            self.assertRegex(res.text, r".*Authorization.*headers")
            self.logout(keep_db=False)

    def test_greeting1_headers_nodb(self):
        res = self.nodb_url_open('/test_http/greeting')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get('Content-Type'), 'text/html; charset=utf-8')
        self.assertEqual(res.text, "Tek'ma'te")

    def test_greeting2_headers_db(self):
        self.authenticate('jackoneill', 'jackoneill')
        res = self.db_url_open('/test_http/greeting')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get('Content-Type'), 'text/html; charset=utf-8')
        self.assertEqual(res.text, "Tek'ma'te")
