# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.tests import new_test_user, tagged
from odoo.addons.im_livechat.tests.common import TestImLivechatCommon


@tagged("-at_install", "post_install")
class TestImLivechatSessionHistory(TestImLivechatCommon):
    def test_session_history_navigation_back_and_forth(self):
        operator = new_test_user(self.env, login="operator", groups="base.group_user,im_livechat.im_livechat_group_manager")
        self.env["mail.presence"]._update_presence(operator)
        self.livechat_channel.user_ids |= operator
        self.authenticate(None, None)
        data = self.make_jsonrpc_request("/im_livechat/get_session", {
            "channel_id": self.livechat_channel.id,
            "previous_operator_id": operator.partner_id.id
        })
        channel = self.env["discuss.channel"].browse(data["channel_id"])
        channel.with_user(operator).message_post(body="Hello, how can I help you?")
        self.start_tour("/odoo", "im_livechat_history_back_and_forth_tour", login="operator")
