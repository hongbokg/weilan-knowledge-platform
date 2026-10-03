import unittest
from credential_policy import contains_credentials as check

class CredentialsTests(unittest.TestCase):
    def test_subjects_and_network_addresses_are_public(self):
        for text in ['审计报告 财务报表 营业执照 身份证','摄像头 IP 192.22.101.23 MAC aa:bb:cc:dd:ee:ff','密码：未设置','| 账号 | 密码 |\n|---|---|\n|admin|未填写|']:
            self.assertFalse(check(text),text)
    def test_real_passwords_and_table_columns_are_restricted(self):
        for text in ['密码：abc123','密码：!@#$','{"api_key":"ExampleSecret123"}','<table><tr><td>密码</td><td>abc123</td></tr></table>',
                     '|设备|密码|IP|\n|---|---|---|\n|摄像头|p@ss123|192.22.101.23|',
                     '<table><tr><td>设备</td><td>密码</td></tr><tr><td>摄像头</td><td>abc123</td></tr></table>',
                     '|设备|管理员密码（初始）|\n|---|---|\n|摄像头|p@ss123|']:
            self.assertTrue(check(text),text)
    def test_empty_password_field_does_not_taint_following_table(self):
        text='<table><tr><td>密码</td><td>未填写</td></tr></table><table><tr><td>价格</td><td>100</td></tr></table>'
        self.assertFalse(check(text))
        self.assertFalse(check('|密码|未填写|\n|IP|192.22.101.23|'))

if __name__=='__main__':unittest.main()
