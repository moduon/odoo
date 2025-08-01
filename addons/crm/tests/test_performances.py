# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import random

from odoo.addons.crm.tests.test_crm_lead_assignment import TestLeadAssignCommon
from odoo.tests.common import tagged
from odoo.tools import mute_logger


@tagged('lead_assign', 'crm_performance', 'post_install', '-at_install')
class TestLeadAssignPerf(TestLeadAssignCommon):
    """ Test performances of lead assignment feature added in saas-14.2

    Assign process is a random process: randomizing teams leads to searching,
    assigning and de-duplicating leads in various order. As a lot of search
    are implied during assign process query counters may vary from run to run.
    "Heavy" performance test included here ranged from 6K to 6.3K queries. Either
    we set high counters maximum which makes those tests less useful. Either we
    avoid random if possible which is what we decided to do by setting the seed
    of random in tests.
    """

    def setUp(self):
        super().setUp()
        # patch registry to simulate a ready environment
        self.patch(self.env.registry, 'ready', True)
        # we don't use mock_mail_gateway thus want to mock smtp to test the stack
        self._mock_smtplib_connection()

    @mute_logger('odoo.models.unlink', 'odoo.addons.crm.models.crm_team', 'odoo.addons.crm.models.crm_team_member')
    def test_assign_perf_duplicates(self):
        """ Test assign process with duplicates on partner. Allow to ensure notably
        that de duplication is effectively performed. """
        # fix the seed and avoid randomness
        random.seed(1940)

        leads = self._create_leads_batch(
            lead_type='lead',
            user_ids=[False],
            partner_ids=[self.contact_1.id, self.contact_2.id, False, False, False],
            count=200
        )
        # commit probability and related fields
        leads.flush_recordset()
        self.assertInitialData()

        # assign probability to leads (bypass auto probability as purpose is not to test pls)
        leads = self.env['crm.lead'].search([('id', 'in', leads.ids)])  # ensure order
        for idx in range(0, 5):
            sliced_leads = leads[idx:len(leads):5]
            for lead in sliced_leads:
                lead.probability = (idx + 1) * 10 * ((int(lead.priority) + 1) / 2)
        # commit probability and related fields
        leads.flush_recordset()

        # randomness: at least 1 query, +3 for demo -> 955 + 5
        with self.with_user('user_sales_manager'):
            self.env.user._is_internal()  # warmup the cache to avoid inconsistency between community an enterprise
            with self.assertQueryCount(user_sales_manager=960):
                self.env['crm.team'].browse(self.sales_teams.ids)._action_assign_leads()

        # teams assign
        leads = self.env['crm.lead'].search([('id', 'in', leads.ids)])  # ensure order
        leads_st1 = leads.filtered_domain([('team_id', '=', self.sales_team_1.id)])
        leads_stc = leads.filtered_domain([('team_id', '=', self.sales_team_convert.id)])
        self.assertLessEqual(len(leads_st1), 128)
        self.assertLessEqual(len(leads_stc), 96)
        self.assertEqual(len(leads_st1) + len(leads_stc), len(leads))  # Make sure all lead are assigned

        # salespersons assign
        self.members.invalidate_model(['lead_month_count', 'lead_day_count'])
        self.assertMemberAssign(self.sales_team_1_m1, 2)  # 45 max on one month -> 2 daily
        self.assertMemberAssign(self.sales_team_1_m2, 1)  # 15 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_1_m3, 1)  # 15 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_convert_m1, 1)  # 30 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_convert_m2, 2)  # 60 max on one month -> 2 daily

    @mute_logger('odoo.models.unlink', 'odoo.addons.crm.models.crm_team', 'odoo.addons.crm.models.crm_team_member')
    def test_assign_perf_no_duplicates(self):
        # fix the seed and avoid randomness
        random.seed(1945)

        leads = self._create_leads_batch(
            lead_type='lead',
            user_ids=[False],
            partner_ids=[False],
            count=100
        )
        # commit probability and related fields
        leads.flush_recordset()
        self.assertInitialData()

        # assign probability to leads (bypass auto probability as purpose is not to test pls)
        leads = self.env['crm.lead'].search([('id', 'in', leads.ids)])  # ensure order
        for idx in range(0, 5):
            sliced_leads = leads[idx:len(leads):5]
            for lead in sliced_leads:
                lead.probability = (idx + 1) * 10 * ((int(lead.priority) + 1) / 2)
        # commit probability and related fields
        leads.flush_recordset()

        # randomness: at least 1 query, +1 for demo
        with self.with_user('user_sales_manager'):
            with self.assertQueryCount(user_sales_manager=552):
                self.env['crm.team'].browse(self.sales_teams.ids)._action_assign_leads()

        # teams assign
        leads = self.env['crm.lead'].search([('id', 'in', leads.ids)])  # ensure order
        leads_st1 = leads.filtered_domain([('team_id', '=', self.sales_team_1.id)])
        leads_stc = leads.filtered_domain([('team_id', '=', self.sales_team_convert.id)])
        self.assertEqual(len(leads_st1) + len(leads_stc), 100)

        # salespersons assign
        self.members.invalidate_model(['lead_month_count', 'lead_day_count'])
        self.assertMemberAssign(self.sales_team_1_m1, 2)  # 45 max on one month -> 2 daily
        self.assertMemberAssign(self.sales_team_1_m2, 1)  # 15 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_1_m3, 1)  # 15 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_convert_m1, 1)  # 30 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_convert_m2, 2)  # 60 max on one month -> 2 daily

    @mute_logger('odoo.models.unlink', 'odoo.addons.crm.models.crm_team', 'odoo.addons.crm.models.crm_team_member')
    def test_assign_perf_populated(self):
        """ Test assignment on a more high volume oriented test set in order to
        have more insights on query counts. """
        # fix the seed and avoid randomness
        random.seed(1871)

        # create leads enough to have interesting counters
        _lead_count, _email_dup_count, _partner_count = 600, 50, 150
        leads = self._create_leads_batch(
            lead_type='lead',
            user_ids=[False],
            partner_count=_partner_count,
            country_ids=[self.env.ref('base.be').id, self.env.ref('base.fr').id, False],
            count=_lead_count,
            email_dup_count=_email_dup_count)
        # commit probability and related fields
        leads.flush_recordset()
        self.assertInitialData()

        # assign for one month, aka a lot
        self.env.ref('crm.ir_cron_crm_lead_assign').write({'interval_type': 'days', 'interval_number': 30})
        # create a third team
        sales_team_3 = self.env['crm.team'].create({
            'name': 'Sales Team 3',
            'sequence': 15,
            'alias_name': False,
            'use_leads': True,
            'use_opportunities': True,
            'company_id': False,
            'user_id': False,
            'assignment_domain': [('country_id', '!=', False)],
        })
        sales_team_3_m1 = self.env['crm.team.member'].create({
            'user_id': self.user_sales_manager.id,
            'crm_team_id': sales_team_3.id,
            'assignment_max': 60,
            'assignment_domain': False,
        })
        sales_team_3_m2 = self.env['crm.team.member'].create({
            'user_id': self.user_sales_leads.id,
            'crm_team_id': sales_team_3.id,
            'assignment_max': 60,
            'assignment_domain': False,
        })
        sales_team_3_m3 = self.env['crm.team.member'].create({
            'user_id': self.user_sales_salesman.id,
            'crm_team_id': sales_team_3.id,
            'assignment_max': 15,
            'assignment_domain': [('probability', '>=', 10)],
        })
        sales_teams = self.sales_teams | sales_team_3
        self.assertEqual(sum(team.assignment_max for team in sales_teams), 300)
        self.assertEqual(len(leads), 650)

        # assign probability to leads (bypass auto probability as purpose is not to test pls)
        leads = self.env['crm.lead'].search([('id', 'in', leads.ids)])  # ensure order
        for idx in range(0, 5):
            sliced_leads = leads[idx:len(leads):5]
            for lead in sliced_leads:
                lead.probability = (idx + 1) * 10 * ((int(lead.priority) + 1) / 2)
        # commit probability and related fields
        leads.flush_recordset()

        # randomness: add 2 queries
        with self.with_user('user_sales_manager'):
            with self.assertQueryCount(user_sales_manager=5173):
                self.env['crm.team'].browse(sales_teams.ids)._action_assign_leads()

        # teams assign
        leads = self.env['crm.lead'].search([('id', 'in', leads.ids)])
        self.assertEqual(leads.team_id, sales_teams)
        self.assertEqual(leads.user_id, sales_teams.member_ids)

        # salespersons assign
        self.members.invalidate_model(['lead_month_count', 'lead_day_count'])
        self.assertMemberAssign(self.sales_team_1_m1, 2)  # 45 max on one month -> 2 daily
        self.assertMemberAssign(self.sales_team_1_m2, 1)  # 15 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_1_m3, 1)  # 15 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_convert_m1, 1)  # 30 max on one month -> 1 daily
        self.assertMemberAssign(self.sales_team_convert_m2, 2)  # 60 max on one month -> 2 daily
        self.assertMemberAssign(sales_team_3_m1, 2)  # 60 max on one month -> 2 daily
        self.assertMemberAssign(sales_team_3_m2, 2)  # 60 max on one month -> 2 daily
        self.assertMemberAssign(sales_team_3_m3, 1)  # 15 max on one month -> 1 daily
