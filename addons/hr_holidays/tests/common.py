# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.addons.mail.tests.common import mail_new_test_user
from odoo.tests import common


class TestHrHolidaysCommon(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super(TestHrHolidaysCommon, cls).setUpClass()
        cls.env.user.tz = 'Europe/Brussels'
        cls.env.user.company_id.resource_calendar_id.tz = "Europe/Brussels"

        cls.company = cls.env['res.company'].create({'name': 'Test company'})
        cls.env.user.company_id = cls.company

        # The available time off types are the ones whose:
        # 1. Company is one of the selected companies.
        # 2. Company is false but whose country is one the countries of the selected companies.
        # 3. Company is false and country is false
        # Thus, a time off type is defined to be available for `Test company`
        # For example, the tour 'time_off_request_calendar_view' would succeed (false positive) without this leave type.
        # However, the tour won't create a time-off request (as expected)because no time-off type is available to be selected on the leave
        # This would cause the test case that uses the tour to fail.
        cls.env['hr.leave.type'].create({
            'name': 'Test Leave Type',
            'requires_allocation': False,
            'request_unit': 'day',
            'company_id': cls.company.id,
        })

        # Test users to use through the various tests
        cls.user_hruser = mail_new_test_user(cls.env, login='armande', groups='base.group_user,hr_holidays.group_hr_holidays_user')
        cls.user_hruser_id = cls.user_hruser.id

        cls.user_hrmanager = mail_new_test_user(cls.env, login='bastien', groups='base.group_user,hr_holidays.group_hr_holidays_manager')
        cls.user_hrmanager_id = cls.user_hrmanager.id
        cls.user_hrmanager.tz = 'Europe/Brussels'

        cls.user_responsible = mail_new_test_user(cls.env, login='Titus', groups='base.group_user,hr_holidays.group_hr_holidays_responsible')
        cls.user_responsible_id = cls.user_responsible.id
        cls.user_employee = mail_new_test_user(cls.env, login='enguerran', password='enguerran', groups='base.group_user')
        cls.user_employee_id = cls.user_employee.id

        # Hr Data
        Department = cls.env['hr.department'].with_context(tracking_disable=True)

        cls.hr_dept = Department.create({
            'name': 'Human Resources',
        })
        cls.rd_dept = Department.create({
            'name': 'Research and devlopment',
        })

        cls.employee_responsible = cls.env['hr.employee'].create({
            'name': 'David Employee',
            'user_id': cls.user_responsible_id,
            'department_id': cls.rd_dept.id,
        })

        cls.employee_emp = cls.env['hr.employee'].create({
            'name': 'David Employee',
            'user_id': cls.user_employee_id,
            'leave_manager_id': cls.user_responsible_id,
            'department_id': cls.rd_dept.id,
        })
        cls.employee_emp_id = cls.employee_emp.id

        cls.employee_hruser = cls.env['hr.employee'].create({
            'name': 'Armande HrUser',
            'user_id': cls.user_hruser_id,
            'department_id': cls.rd_dept.id,
        })
        cls.employee_hruser_id = cls.employee_hruser.id

        cls.employee_hrmanager = cls.env['hr.employee'].create({
            'name': 'Bastien HrManager',
            'user_id': cls.user_hrmanager_id,
            'department_id': cls.hr_dept.id,
            'parent_id': cls.employee_hruser_id,
        })
        cls.employee_hrmanager_id = cls.employee_hrmanager.id

        cls.rd_dept.write({'manager_id': cls.employee_hruser_id})
        cls.hours_per_day = cls.employee_emp.resource_id.calendar_id.hours_per_day or 8
