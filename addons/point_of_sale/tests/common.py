import logging

from random import randint
from datetime import datetime
from odoo import fields, tools
from odoo.fields import Command
from odoo.tests import Form
from odoo.addons.stock_account.tests.test_anglo_saxon_valuation_reconciliation_common import ValuationReconciliationTestCommon

_logger = logging.getLogger(__name__)

def archive_products(env):
    # Archive all existing product to avoid noise during the tours
    all_pos_product = env['product.template'].search([('available_in_pos', '=', True)])
    tip = env.ref('point_of_sale.product_product_tip').product_tmpl_id
    (all_pos_product - tip)._write({'active': False})


class CommonPosTest(ValuationReconciliationTestCommon):
    @classmethod
    def setUpClass(self):
        super().setUpClass()
        archive_products(self.env)

        self.env.user.group_ids += self.env.ref('point_of_sale.group_pos_manager')
        self.env.ref('base.EUR').active = True
        self.env.ref('base.USD').active = True

        self.create_res_partners(self)
        self.create_account_cash_rounding(self)
        self.create_pos_categories(self)
        self.create_account_taxes(self)
        self.create_product_templates(self)
        self.create_payment_methods(self)
        self.create_pos_configs(self)

    def create_pos_configs(self):
        sale_journal_eur = self.env['account.journal'].create({
            'name': 'PoS Sale EUR',
            'type': 'sale',
            'code': 'POSE',
            'company_id': self.company.id,
            'sequence': 12,
            'currency_id': self.env.ref('base.EUR').id,
        })
        self.pricelist_eur = self.env['product.pricelist'].create({
            'name': 'Test EUR Pricelist',
            'currency_id': self.env.ref('base.EUR').id
        })
        self.pos_config_eur = self.env['pos.config'].create({
            'name': 'PoS Config EUR',
            'journal_id': sale_journal_eur.id,
            'use_pricelist': True,
            'available_pricelist_ids': [(6, 0, self.pricelist_eur.ids)],
            'pricelist_id': self.pricelist_eur.id,
            'payment_method_ids': [(6, 0, self.bank_payment_method.ids)]
        })
        self.pos_config_usd = self.env['pos.config'].create({
            'name': 'PoS Config USD',
            'journal_id': self.company_data['default_journal_sale'].id,
            'invoice_journal_id': self.company_data['default_journal_sale'].id,
            'payment_method_ids': [
                (4, self.credit_payment_method.id),
                (4, self.bank_payment_method.id),
                (4, self.cash_payment_method.id),
            ]
        })

    def create_res_partners(self):
        self.partner_mobt = self.env['res.partner'].create({
            'name': 'MOBT',
        })
        self.partner_adgu = self.env['res.partner'].create({
            'name': 'ADGU',
        })
        self.partner_lowe = self.env['res.partner'].create({
            'name': 'LOWE',
        })
        self.partner_jcb = self.env['res.partner'].create({
            'name': 'JCB',
        })
        self.partner_moda = self.env['res.partner'].create({
            'name': 'MODA',
        })
        self.partner_stva = self.env['res.partner'].create({
            'name': 'STVA',
        })
        self.partner_manv = self.env['res.partner'].create({
            'name': 'MANV',
        })
        self.partner_vlst = self.env['res.partner'].create({
            'name': 'VLST',
        })

    def create_account_cash_rounding(self):
        self.account_cash_rounding_down = self.env['account.cash.rounding'].create({
            'name': 'Rounding down',
            'rounding': 0.05,
            'rounding_method': 'DOWN',
            'profit_account_id': self.company_data['default_account_revenue'].id,
            'loss_account_id': self.company_data['default_account_expense'].id,
        })
        self.account_cash_rounding_up = self.env['account.cash.rounding'].create({
            'name': 'Rounding up',
            'rounding': 0.05,
            'rounding_method': 'UP',
            'profit_account_id': self.company_data['default_account_revenue'].id,
            'loss_account_id': self.company_data['default_account_expense'].id,
        })
        self.account_cash_rounding_half = self.env['account.cash.rounding'].create({
            'name': 'Rounding half',
            'rounding': 0.05,
            'profit_account_id': self.company_data['default_account_revenue'].id,
            'loss_account_id': self.company_data['default_account_expense'].id,
        })

    def create_payment_methods(self):
        self.cash_payment_method = self.env['pos.payment.method'].create({
            'name': 'Cash',
            'receivable_account_id': self.company_data['default_account_receivable'].id,
            'journal_id': self.company_data['default_journal_cash'].id,
        })
        self.bank_payment_method = self.env['pos.payment.method'].create({
            'name': 'Bank',
            'journal_id': self.company_data['default_journal_bank'].id,
            'receivable_account_id': self.company_data['default_account_receivable'].id,
        })
        self.credit_payment_method = self.env['pos.payment.method'].create({
            'name': 'Credit',
            'receivable_account_id': self.company_data['default_account_receivable'].id,
            'split_transactions': True,
        })

    def create_pos_categories(self):
        self.cat_no_tax = self.env['pos.category'].create({
            'name': 'No tax',
            'sequence': 0,
        })
        self.cat_tax_five_incl = self.env['pos.category'].create({
            'name': 'Tax five incl',
            'sequence': 1,
        })
        self.cat_tax_ten_incl = self.env['pos.category'].create({
            'name': 'Tax ten incl',
            'sequence': 2,
        })
        self.cat_tax_fiften_incl = self.env['pos.category'].create({
            'name': 'Tax fifteen incl',
            'sequence': 3,
        })
        self.cat_tax_five_excl = self.env['pos.category'].create({
            'name': 'Tax five excl',
            'sequence': 4,
        })
        self.cat_tax_ten_excl = self.env['pos.category'].create({
            'name': 'Tax ten excl',
            'sequence': 5,
        })
        self.cat_tax_fiften_excl = self.env['pos.category'].create({
            'name': 'Tax fifteen excl',
            'sequence': 6,
        })

    def create_account_taxes(self):
        self.tax_five_incl = self.env['account.tax'].create({
            'name': 'Tax five incl',
            'amount': 5,
            'price_include_override': 'tax_included',
        })
        self.tax_ten_incl = self.env['account.tax'].create({
            'name': 'Tax ten incl',
            'amount': 10,
            'price_include_override': 'tax_included',
        })
        self.tax_fiften_incl = self.env['account.tax'].create({
            'name': 'Tax fifteen incl',
            'amount': 15,
            'price_include_override': 'tax_included',
        })
        self.tax_five_excl = self.env['account.tax'].create({
            'name': 'Tax five excl',
            'amount': 5,
        })
        self.tax_ten_excl = self.env['account.tax'].create({
            'name': 'Tax ten excl',
            'amount': 10,
        })
        self.tax_fiften_excl = self.env['account.tax'].create({
            'name': 'Tax fifteen excl',
            'amount': 15,
        })

    def create_product_templates(self):
        self.ten_dollars_no_tax = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Ten dollars no tax',
            'list_price': 10.0,
            'pos_categ_ids': [(6, 0, [self.cat_no_tax.id])],
            'taxes_id': [(5, 0)],
        })
        self.twenty_dollars_no_tax = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Twenty dollars no tax',
            'list_price': 20.0,
            'pos_categ_ids': [(6, 0, [self.cat_no_tax.id])],
            'taxes_id': [(5, 0)],
        })
        self.ten_dollars_with_5_incl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Ten dollars with 5 included',
            'list_price': 10.0,
            'taxes_id': [(6, 0, [self.tax_five_incl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_five_incl.id])],
        })
        self.twenty_dollars_with_5_incl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Twenty dollars with 5 included',
            'list_price': 20.0,
            'taxes_id': [(6, 0, [self.tax_five_incl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_five_incl.id])],
        })
        self.ten_dollars_with_10_incl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Ten dollars with 10 included',
            'list_price': 10.0,
            'taxes_id': [(6, 0, [self.tax_ten_incl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_ten_incl.id])],
        })
        self.twenty_dollars_with_10_incl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Twenty dollars with 10 included',
            'list_price': 20.0,
            'taxes_id': [(6, 0, [self.tax_ten_incl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_ten_incl.id])],
        })
        self.ten_dollars_with_15_incl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Ten dollars with 15 included',
            'list_price': 10.0,
            'taxes_id': [(6, 0, [self.tax_fiften_incl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_fiften_incl.id])],
        })
        self.twenty_dollars_with_15_incl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Twenty dollars with 15 included',
            'list_price': 20.0,
            'taxes_id': [(6, 0, [self.tax_fiften_incl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_fiften_incl.id])],
        })
        self.ten_dollars_with_5_excl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Ten dollars with 5 excluded',
            'list_price': 10.0,
            'taxes_id': [(6, 0, [self.tax_five_excl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_five_excl.id])],
        })
        self.twenty_dollars_with_5_excl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Twenty dollars with 5 excluded',
            'list_price': 20.0,
            'taxes_id': [(6, 0, [self.tax_five_excl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_five_excl.id])],
        })
        self.ten_dollars_with_10_excl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Ten dollars with 10 excluded',
            'list_price': 10.0,
            'taxes_id': [(6, 0, [self.tax_ten_excl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_ten_excl.id])],
        })
        self.twenty_dollars_with_10_excl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Twenty dollars with 10 excluded',
            'list_price': 20.0,
            'taxes_id': [(6, 0, [self.tax_ten_excl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_ten_excl.id])],
        })
        self.ten_dollars_with_15_excl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Ten dollars with 15 excluded',
            'list_price': 10.0,
            'taxes_id': [(6, 0, [self.tax_fiften_excl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_fiften_excl.id])],
        })
        self.twenty_dollars_with_15_excl = self.env['product.template'].create({
            'available_in_pos': True,
            'name': 'Twenty dollars with 15 excluded',
            'list_price': 20.0,
            'taxes_id': [(6, 0, [self.tax_fiften_excl.id])],
            'pos_categ_ids': [(6, 0, [self.cat_tax_fiften_excl.id])],
        })

    def create_backend_pos_order(self, data):
        pos_config = data.get('pos_config', self.pos_config_usd)
        order_data = data.get('order_data', {})
        line_product_ids = [line_data['product_id'] for line_data in data.get('line_data', [])]
        product_by_id = {p.id: p for p in self.env['product.product'].browse(line_product_ids)}
        refund = False

        if not pos_config.current_session_id:
            pos_config.open_ui()

        order = self.env['pos.order'].create({
            'amount_total': 0,
            'amount_paid': 0,
            'amount_tax': 0,
            'amount_return': 0,
            'date_order': fields.Datetime.to_string(fields.Datetime.now()),
            'company_id': self.env.company.id,
            'session_id': pos_config.current_session_id.id,
            'lines': [
                Command.create({
                    'price_unit': product_by_id[line_data['product_id']].lst_price,
                    'price_subtotal': product_by_id[line_data['product_id']].lst_price,
                    'tax_ids': [(6, 0, product_by_id[line_data['product_id']].taxes_id.ids)],
                    'price_subtotal_incl': 0,
                    **line_data,
                }) for line_data in data.get('line_data', [])
            ],
            **order_data,
        })

        # Re-trigger prices computation
        order.lines._onchange_amount_line_all()
        order._compute_prices()

        if data.get('payment_data'):
            payment_context = {"active_ids": order.ids, "active_id": order.id}
            for payment in data['payment_data']:
                make_payment = {'payment_method_id': payment['payment_method_id']}
                if payment.get('amount'):
                    make_payment['amount'] = payment['amount']
                order_payment = self.env['pos.make.payment'].with_context(**payment_context).create(make_payment)
                order_payment.with_context(**payment_context).check()

        if data.get('refund_data'):
            refund_action = order.refund()
            refund = self.env['pos.order'].browse(refund_action['res_id'])
            payment_context = {"active_ids": refund.ids, "active_id": refund.id}

            if data.get('order_data') and data['order_data'].get('to_invoice', False):
                refund.to_invoice = True

            for refund_data in data['refund_data']:
                make_refund = {'payment_method_id': refund_data['payment_method_id']}
                if refund_data.get('amount'):
                    make_refund['amount'] = refund_data['amount']
                refund_payment = self.env['pos.make.payment'].with_context(**payment_context).create(make_refund)
                refund_payment.with_context(**payment_context).check()

        return order, refund

    def compute_tax(self, product, price, qty=1, taxes=None, pos_config=None):
        config = pos_config or self.pos_config_usd
        if not taxes:
            taxes = product.taxes_id.filtered(lambda t: t.company_id.id == self.env.company.id)
        currency = config.currency_id
        res = taxes.compute_all(price, currency, qty, product=product)
        untax = res['total_excluded']
        return untax, sum(tax.get('amount', 0.0) for tax in res['taxes'])


class TestPoSCommon(ValuationReconciliationTestCommon):
    """ Set common values for different special test cases.

    The idea is to set up common values here for the tests
    and implement different special scenarios by inheriting
    this class.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('point_of_sale.group_pos_manager')

        cls.company_data['company'].write({
            'point_of_sale_update_stock_quantities': 'real',
            'country_id': cls.env['res.country'].create({
                'name': 'PoS Land',
                'code': 'WOW',
            }),
        })

        # Set basic defaults
        cls.account_tax_return_journal = cls.company_data['default_tax_return_journal']
        cls.sales_account = cls.company_data['default_account_revenue']
        cls.invoice_journal = cls.company_data['default_journal_sale']
        cls.receivable_account = cls.company_data['default_account_receivable']
        cls.tax_received_account = cls.company_data['default_account_tax_sale']
        cls.company.account_default_pos_receivable_account_id = cls.env['account.account'].create({
            'code': 'X1012.POS',
            'name': 'Debtors - (POS)',
            'reconcile': True,
            'account_type': 'asset_receivable',
        })
        cls.pos_receivable_account = cls.company.account_default_pos_receivable_account_id
        cls.pos_receivable_cash = cls.copy_account(cls.company.account_default_pos_receivable_account_id, {'name': 'POS Receivable Cash'})
        cls.pos_receivable_bank = cls.copy_account(cls.company.account_default_pos_receivable_account_id, {'name': 'POS Receivable Bank'})
        cls.outstanding_bank = cls.copy_account(cls.inbound_payment_method_line.payment_account_id, {'name': 'Outstanding Bank'})
        cls.c1_receivable = cls.copy_account(cls.receivable_account, {'name': 'Customer 1 Receivable'})
        cls.other_receivable_account = cls.env['account.account'].create({
            'name': 'Other Receivable',
            'code': 'RCV00',
            'account_type': 'asset_receivable',
            'internal_group': 'asset',
            'reconcile': True,
        })

        # company_currency can be different from `base.USD` depending on the localization installed
        cls.company_currency = cls.company.currency_id
        # other_currency is a currency different from the company_currency
        # sometimes company_currency is different from USD, so handle appropriately.
        cls.other_currency = cls.setup_other_currency("EUR", rounding=0.001)

        cls.currency_pricelist = cls.env['product.pricelist'].create({
            'name': 'Public Pricelist',
            'currency_id': cls.company_currency.id,
        })
        # Set Point of Sale configurations
        # basic_config
        #   - derived from 'point_of_sale.pos_config_main' with added invoice_journal_id and credit payment method.
        # other_currency_config
        #   - pos.config set to have currency different from company currency.
        cls.basic_config = cls._create_basic_config()
        cls.other_currency_config = cls._create_other_currency_config()

        # Set product categories
        # categ_basic
        #   - just the plain 'product.product_category_services'
        # categ_anglo
        #   - product category with fifo and real_time valuations
        #   - used for checking anglo saxon accounting behavior
        cls.categ_basic = cls.env.ref('product.product_category_services')
        cls.env.company.anglo_saxon_accounting = True
        cls.categ_anglo = cls._create_categ_anglo()

        # other basics
        cls.sale_account = cls.company.income_account_id
        cls.other_sale_account = cls.env['account.account'].search([
            ('company_ids', '=', cls.company.id),
            ('account_type', '=', 'income'),
            ('id', '!=', cls.sale_account.id)
        ], limit=1)

        # Set customers
        cls.customer = cls.env['res.partner'].create({'name': 'Customer 1', 'property_account_receivable_id': cls.c1_receivable.id})
        cls.other_customer = cls.env['res.partner'].create({'name': 'Other Customer', 'property_account_receivable_id': cls.other_receivable_account.id})

        # Set taxes
        # cls.taxes => dict
        #   keys: 'tax7', 'tax10'(price_include=True), 'tax_group_7_10'
        cls.taxes = cls._create_taxes()

        cls.stock_location_components = cls.env["stock.location"].create({
            'name': 'Shelf 1',
            'location_id': cls.company_data['default_warehouse'].lot_stock_id.id,
        })


    #####################
    ## private methods ##
    #####################

    @classmethod
    def _create_basic_config(cls):
        config = cls.env['pos.config'].create({
            'name': 'PoS Shop Test',
            'invoice_journal_id': cls.invoice_journal.id,
            'available_pricelist_ids': cls.currency_pricelist.ids,
            'pricelist_id': cls.currency_pricelist.id,
        })
        cls.company_data['default_journal_cash'].pos_payment_method_ids.unlink()
        cls.cash_pm1 = config.payment_method_ids.filtered(lambda c: c.journal_id.type == 'cash')
        if cls.cash_pm1:
            cls.cash_pm1.write({'receivable_account_id': cls.pos_receivable_cash.id})
        else:
            cls.cash_pm1 = cls.env['pos.payment.method'].create({
                'name': 'Cash',
                'journal_id': cls.company_data['default_journal_cash'].id,
                'receivable_account_id': cls.pos_receivable_cash.id,
                'company_id': cls.env.company.id,
            })
        cls.bank_pm1 = cls.env['pos.payment.method'].create({
            'name': 'Bank',
            'journal_id': cls.company_data['default_journal_bank'].id,
            'receivable_account_id': cls.pos_receivable_bank.id,
            'outstanding_account_id': cls.outstanding_bank.id,
            'company_id': cls.env.company.id,
        })
        cls.cash_split_pm1 = cls.cash_pm1.copy(default={
            'name': 'Split (Cash) PM',
            'split_transactions': True,
            'journal_id': cls.env['account.journal'].create({
                                'name': "Cash",
                                'code': "CSH %s" % config.id,
                                'type': 'cash',
                            }).id
        })
        cls.bank_split_pm1 = cls.bank_pm1.copy(default={
            'name': 'Split (Bank) PM',
            'split_transactions': True,
        })
        cls.pay_later_pm = cls.env['pos.payment.method'].create({'name': 'Pay Later', 'split_transactions': True})
        config.write({'payment_method_ids': [(4, cls.cash_split_pm1.id), (4, cls.bank_split_pm1.id), (4, cls.cash_pm1.id), (4, cls.bank_pm1.id), (4, cls.pay_later_pm.id)]})
        return config

    @classmethod
    def _create_other_currency_config(cls):
        (cls.other_currency.rate_ids | cls.company_currency.rate_ids).unlink()
        cls.env['res.currency.rate'].create({
            'rate': 0.5,
            'currency_id': cls.other_currency.id,
            'name': datetime.today().date(),
        })
        other_cash_journal = cls.env['account.journal'].create({
            'name': 'Cash Other',
            'type': 'cash',
            'company_id': cls.company.id,
            'code': 'CSHO',
            'sequence': 10,
            'currency_id': cls.other_currency.id
        })
        other_invoice_journal = cls.env['account.journal'].create({
            'name': 'Customer Invoice Other',
            'type': 'sale',
            'company_id': cls.company.id,
            'code': 'INVO',
            'sequence': 11,
            'currency_id': cls.other_currency.id
        })
        other_sales_journal = cls.env['account.journal'].create({
            'name':'PoS Sale Other',
            'type': 'sale',
            'code': 'POSO',
            'company_id': cls.company.id,
            'sequence': 12,
            'currency_id': cls.other_currency.id
        })
        other_bank_journal = cls.env['account.journal'].create({
            'name': 'Bank Other',
            'type': 'bank',
            'company_id': cls.company.id,
            'code': 'BNKO',
            'sequence': 13,
            'currency_id': cls.other_currency.id
        })
        other_pricelist = cls.env['product.pricelist'].create({
            'name': 'Public Pricelist Other',
            'currency_id': cls.other_currency.id,
        })
        cls.cash_pm2 = cls.env['pos.payment.method'].create({
            'name': 'Cash Other',
            'journal_id': other_cash_journal.id,
            'receivable_account_id': cls.pos_receivable_cash.id,
        })
        cls.bank_pm2 = cls.env['pos.payment.method'].create({
            'name': 'Bank Other',
            'journal_id': other_bank_journal.id,
            'receivable_account_id': cls.pos_receivable_bank.id,
            'outstanding_account_id': cls.outstanding_bank.id,
        })

        config = cls.env['pos.config'].create({
            'name': 'Shop Other',
            'invoice_journal_id': other_invoice_journal.id,
            'journal_id': other_sales_journal.id,
            'use_pricelist': True,
            'available_pricelist_ids': other_pricelist.ids,
            'pricelist_id': other_pricelist.id,
            'payment_method_ids': [cls.cash_pm2.id, cls.bank_pm2.id],
        })
        return config

    @classmethod
    def _create_categ_anglo(cls):
        return cls.env['product.category'].create({
            'name': 'Anglo',
            'parent_id': False,
            'property_cost_method': 'fifo',
            'property_valuation': 'real_time',
            'property_stock_account_input_categ_id': cls.company_data['default_account_stock_in'].id,
            'property_stock_account_output_categ_id': cls.company_data['default_account_stock_out'].id,
            'property_stock_valuation_account_id': cls.company_data['default_account_stock_valuation'].copy().id
        })

    @classmethod
    def _create_taxes(cls):
        """ Create taxes

        tax7: 7%, excluded in product price
        tax10: 10%, included in product price
        tax21: 21%, included in product price
        """
        def create_tag(name):
            return cls.env['account.account.tag'].create({
                'name': name,
                'applicability': 'taxes',
                'country_id': cls.env.company.account_fiscal_country_id.id
            })

        cls.tax_tag_invoice_base = create_tag('Invoice Base tag')
        cls.tax_tag_invoice_tax = create_tag('Invoice Tax tag')
        cls.tax_tag_refund_base = create_tag('Refund Base tag')
        cls.tax_tag_refund_tax = create_tag('Refund Tax tag')

        def create_tax(percentage, price_include_override='tax_excluded', include_base_amount=False):
            return cls.env['account.tax'].create({
                'name': f'Tax {percentage}%',
                'amount': percentage,
                'price_include_override': price_include_override,
                'amount_type': 'percent',
                'include_base_amount': include_base_amount,
                'invoice_repartition_line_ids': [
                    (0, 0, {
                        'repartition_type': 'base',
                        'tag_ids': [(6, 0, cls.tax_tag_invoice_base.ids)],
                    }),
                    (0, 0, {
                        'repartition_type': 'tax',
                        'account_id': cls.tax_received_account.id,
                        'tag_ids': [(6, 0, cls.tax_tag_invoice_tax.ids)],
                    }),
                ],
                'refund_repartition_line_ids': [
                    (0, 0, {
                        'repartition_type': 'base',
                        'tag_ids': [(6, 0, cls.tax_tag_refund_base.ids)],
                    }),
                    (0, 0, {
                        'repartition_type': 'tax',
                        'account_id': cls.tax_received_account.id,
                        'tag_ids': [(6, 0, cls.tax_tag_refund_tax.ids)],
                    }),
                ],
            })

        def create_tax_fixed(amount, price_include_override='tax_excluded', include_base_amount=False):
            return cls.env['account.tax'].create({
                'name': f'Tax fixed amount {amount}',
                'amount': amount,
                'price_include_override': price_include_override,
                'include_base_amount': include_base_amount,
                'amount_type': 'fixed',
                'invoice_repartition_line_ids': [
                    (0, 0, {
                        'repartition_type': 'base',
                        'tag_ids': [(6, 0, cls.tax_tag_invoice_base.ids)],
                    }),
                    (0, 0, {
                        'repartition_type': 'tax',
                        'account_id': cls.tax_received_account.id,
                        'tag_ids': [(6, 0, cls.tax_tag_invoice_tax.ids)],
                    }),
                ],
                'refund_repartition_line_ids': [
                    (0, 0, {
                        'repartition_type': 'base',
                        'tag_ids': [(6, 0, cls.tax_tag_refund_base.ids)],
                    }),
                    (0, 0, {
                        'repartition_type': 'tax',
                        'account_id': cls.tax_received_account.id,
                        'tag_ids': [(6, 0, cls.tax_tag_refund_tax.ids)],
                    }),
                ],
            })

        tax_fixed006 = create_tax_fixed(0.06, price_include_override='tax_included', include_base_amount=True)
        tax_fixed012 = create_tax_fixed(0.12, price_include_override='tax_included', include_base_amount=True)
        tax7 = create_tax(7, price_include_override='tax_excluded')
        tax8 = create_tax(8, include_base_amount=True)
        tax9 = create_tax(9)
        tax10 = create_tax(10, price_include_override='tax_included')
        tax21 = create_tax(21, price_include_override='tax_included')


        tax_group_7_10 = tax7.copy()
        with Form(tax_group_7_10) as tax:
            tax.name = 'Tax 7+10%'
            tax.amount_type = 'group'
            tax.children_tax_ids.add(tax7)
            tax.children_tax_ids.add(tax10)

        return {
            'tax7': tax7,
            'tax8': tax8,
            'tax9': tax9,
            'tax10': tax10,
            'tax21': tax21,
            'tax_fixed006': tax_fixed006,
            'tax_fixed012': tax_fixed012,
            'tax_group_7_10': tax_group_7_10
        }

    ####################
    ## public methods ##
    ####################

    def create_random_uid(self):
        return ('%05d-%03d-%04d' % (randint(1, 99999), randint(1, 999), randint(1, 9999)))

    def create_ui_order_data(self, pos_order_lines_ui_args, customer=False, is_invoiced=False, payments=None, uuid=None):
        """ Mocks the order_data generated by the pos ui.

        This is useful in making orders in an open pos session without making tours.
        Its functionality is tested in test_pos_create_ui_order_data.py.

        Before use, make sure that self is set with:
            1. pricelist -> the pricelist of the current session
            2. currency -> currency of the current session
            3. pos_session -> the current session, equivalent to config.current_session_id
            4. cash_pm -> first cash payment method in the current session
            5. config -> the active pos.config

        The above values should be set when `self.open_new_session` is called.

        :param list(tuple) pos_order_lines_ui_args: pairs of `ordered product` and `quantity`
        or triplet of `ordered product`, `quantity` and discount
        :param list(tuple) payments: pair of `payment_method` and `amount`
        """
        default_fiscal_position = self.config.default_fiscal_position_id
        fiscal_position = customer.property_account_position_id if customer else default_fiscal_position

        def normalize_order_line_param(param):
            if isinstance(param, dict):
                return param

            assert len(param) >= 2
            return {
                'product': param[0],
                'quantity': param[1],
                'discount': 0.0 if len(param) == 2 else param[2],
            }

        def create_order_line(product, quantity, **kwargs):
            price_unit = self.pricelist._get_product_price(product, quantity)
            tax_ids = fiscal_position.map_tax(product.taxes_id.filtered_domain(self.env['account.tax']._check_company_domain(self.env.company)))
            discount = kwargs.get('discount', 0.0)
            price_unit_after_discount = price_unit * (1 - discount / 100.0)
            tax_values = (
                tax_ids.compute_all(price_unit_after_discount, self.currency, quantity)
                if tax_ids
                else {
                    'total_excluded': price_unit * quantity,
                    'total_included': price_unit * quantity,
                }
            )
            return (0, 0, {
                **kwargs,
                'id': randint(1, 1000000),
                'pack_lot_ids': [],
                'price_unit': price_unit,
                'product_id': product.id,
                'price_subtotal': tax_values['total_excluded'],
                'price_subtotal_incl': tax_values['total_included'],
                'qty': quantity,
                'tax_ids': [(6, 0, tax_ids.ids)]
            })

        def create_payment(payment_method, amount):
            return (0, 0, {
                'amount': amount,
                'name': fields.Datetime.now(),
                'payment_method_id': payment_method.id,
            })

        uuid = uuid or self.create_random_uid()

        # 1. generate the order lines
        order_lines = [
            create_order_line(**normalize_order_line_param(param))
            for param in pos_order_lines_ui_args
        ]

        # 2. generate the payments
        total_amount_incl = sum(line[2]['price_subtotal_incl'] for line in order_lines)
        if payments is None:
            default_cash_pm = self.config.payment_method_ids.filtered(lambda pm: pm.is_cash_count and not pm.split_transactions)[:1]
            if not default_cash_pm:
                raise Exception('There should be a cash payment method set in the pos.config.')
            payments = [create_payment(default_cash_pm, total_amount_incl)]
        else:
            payments = [
                create_payment(pm, amount)
                for pm, amount in payments
            ]

        # 3. complete the fields of the order_data
        total_amount_base = sum(line[2]['price_subtotal'] for line in order_lines)
        return {
            'amount_paid': sum(payment[2]['amount'] for payment in payments),
            'amount_return': 0,
            'amount_tax': total_amount_incl - total_amount_base,
            'amount_total': total_amount_incl,
            'date_order': fields.Datetime.to_string(fields.Datetime.now()),
            'fiscal_position_id': fiscal_position.id,
            'pricelist_id': self.config.pricelist_id.id,
            'name': 'Order %s' % uuid,
            'last_order_preparation_change': '{}',
            'lines': order_lines,
            'partner_id': customer and customer.id,
            'session_id': self.pos_session.id,
            'payment_ids': payments,
            'uuid': uuid,
            'user_id': self.env.uid,
            'to_invoice': is_invoiced,
        }

    @classmethod
    def create_product(cls, name, category, lst_price, standard_price=None, tax_ids=None, sale_account=None):
        product = cls.env['product.product'].create({
            'is_storable': True,
            'available_in_pos': True,
            'taxes_id': [(5, 0, 0)] if not tax_ids else [(6, 0, tax_ids)],
            'name': name,
            'categ_id': category.id,
            'lst_price': lst_price,
            'standard_price': standard_price if standard_price else 0.0,
            'company_id': cls.env.company.id,
        })
        if sale_account:
            product.property_account_income_id = sale_account
        return product

    @classmethod
    def adjust_inventory(cls, products, quantities):
        """ Adjust inventory of the given products
        """
        for product, qty in zip(products, quantities):
            cls.env['stock.quant'].with_context(inventory_mode=True).create({
                'product_id': product.id,
                'inventory_quantity': qty,
                'location_id': cls.stock_location_components.id,
            }).action_apply_inventory()

    def open_new_session(self, opening_cash=0):
        """ Used to open new pos session in each configuration.

        - The idea is to properly set values that are constant
          and commonly used in an open pos session.
        - Calling this method is also a prerequisite for using
          `self.create_ui_order_data` function.

        Fields:
            * config : the pos.config currently being used.
                Its value is set at `self.setUp` of the inheriting
                test class.
            * pos_session : the current_session_id of config
            * currency : currency of the current pos.session
            * pricelist : the default pricelist of the session
        """
        self.config.open_ui()
        self.pos_session = self.config.current_session_id
        self.currency = self.pos_session.currency_id
        self.pricelist = self.pos_session.config_id.pricelist_id
        self.pos_session.set_opening_control(opening_cash, None)
        return self.pos_session

    def _run_test(self, args):
        pos_session = self._start_pos_session(args['payment_methods'], args.get('opening_cash', 0))
        _logger.info('DONE: Start session.')
        orders_map = self._create_orders(args['orders'])
        _logger.info('DONE: Orders created.')
        before_closing_cb = args.get('before_closing_cb')
        if before_closing_cb:
            before_closing_cb()
            _logger.info('DONE: Call of before_closing_cb.')
        self._check_invoice_journal_entries(pos_session, orders_map, expected_values=args['journal_entries_before_closing'])
        _logger.info('DONE: Checks for journal entries before closing the session.')
        cash_payment_method = pos_session.payment_method_ids.filtered('is_cash_count')[:1]
        total_cash_payment = sum(pos_session.mapped('order_ids.payment_ids').filtered(lambda payment: payment.payment_method_id.id == cash_payment_method.id).mapped('amount'))
        pos_session.post_closing_cash_details(total_cash_payment)
        pos_session.close_session_from_ui()
        after_closing_cb = args.get('after_closing_cb')
        if after_closing_cb:
            after_closing_cb()
            _logger.info('DONE: Call of after_closing_cb.')
        self._check_session_journal_entries(pos_session, expected_values=args['journal_entries_after_closing'])
        _logger.info('DONE: Checks for journal entries after closing the session.')

    def _start_pos_session(self, payment_methods, opening_cash):
        self.config.write({'payment_method_ids': [(6, 0, payment_methods.ids)]})
        pos_session = self.open_new_session(opening_cash)
        self.assertEqual(self.config.payment_method_ids.ids, pos_session.payment_method_ids.ids, msg='Payment methods in the config should be the same as the session.')
        return pos_session

    def _create_orders(self, order_data_params):
        '''Returns a dict mapping uuid to its created pos.order record.'''
        result = {}
        order_data = [self.create_ui_order_data(**params) for params in order_data_params]
        order_ids = [order['id'] for order in self.env['pos.order'].sync_from_ui(order_data)['pos.order']]
        for order_id in self.env["pos.order"].browse(order_ids):
            result[order_id.uuid] = order_id
        return result

    def _check_invoice_journal_entries(self, pos_session, orders_map, expected_values):
        '''Checks the invoice, together with the payments, from each invoiced order.'''
        currency_rounding = pos_session.currency_id.rounding

        for uid in orders_map:
            order = orders_map[uid]
            if not order.is_invoiced:
                continue
            invoice = order.account_move
            # allow not checking the invoice since pos is not creating the invoices
            if expected_values[uid].get('invoice'):
                self._assert_account_move(invoice, expected_values[uid]['invoice'])
                _logger.info('DONE: Check of invoice for order %s.', uid)

            for pos_payment in order.payment_ids:
                if pos_payment.payment_method_id == self.pay_later_pm:
                    # Skip the pay later payments since there are no journal entries
                    # for them when invoicing.
                    continue

                # This predicate is used to match the pos_payment's journal entry to the
                # list of payments specified in the 'payments' field of the `_run_test`
                # args.
                def predicate(args):
                    payment_method, amount = args
                    first = payment_method == pos_payment.payment_method_id
                    second = tools.float_is_zero(pos_payment.amount - amount, precision_rounding=currency_rounding)
                    return first and second

                self._find_then_assert_values(pos_payment.account_move_id, expected_values[uid]['payments'], predicate)
                _logger.info('DONE: Check of invoice payment (%s, %s) for order %s.', pos_payment.payment_method_id.name, pos_payment.amount, uid)

    def _check_session_journal_entries(self, pos_session, expected_values):
        '''Checks the journal entries after closing the session excluding entries checked in `_check_invoice_journal_entries`.'''
        currency_rounding = pos_session.currency_id.rounding

        # check expected session journal entry
        self._assert_account_move(pos_session.move_id, expected_values['session_journal_entry'])
        _logger.info("DONE: Check of the session's account move.")

        # check expected cash journal entries
        for statement_line in pos_session.statement_line_ids:
            def statement_line_predicate(args):
                return tools.float_is_zero(statement_line.amount - args[0], precision_rounding=currency_rounding)
            self._find_then_assert_values(statement_line.move_id, expected_values['cash_statement'], statement_line_predicate)
        _logger.info("DONE: Check of cash statement lines.")

        # check expected bank payments
        for bank_payment in pos_session.bank_payment_ids:
            def bank_payment_predicate(args):
                return tools.float_is_zero(bank_payment.amount - args[0], precision_rounding=currency_rounding)
            self._find_then_assert_values(bank_payment.move_id, expected_values['bank_payments'], bank_payment_predicate)
        _logger.info("DONE: Check of bank account payments.")

    def _find_then_assert_values(self, account_move, source_of_expected_vals, predicate):
        expected_move_vals = next(move_vals for args, move_vals in source_of_expected_vals if predicate(args))
        self._assert_account_move(account_move, expected_move_vals)

    def _assert_account_move(self, account_move, expected_account_move_vals):
        if expected_account_move_vals:
            # We allow partial checks of the lines of the account move if `line_ids_predicate` is specified.
            # This means that only those that satisfy the predicate are compared to the expected account move line_ids.
            line_ids_predicate = expected_account_move_vals.pop('line_ids_predicate', lambda _: True)
            line_ids = expected_account_move_vals.pop('line_ids')
            reconciliation_statuses = []
            for line in line_ids:
                partially_reconciled = line.pop('partially_reconciled', False)
                if partially_reconciled is True:
                    reconciliation_statuses.append('partially_reconciled')
                else:
                    reconciliation_statuses.append('fully_reconciled' if line.get('reconciled') else 'not_reconciled')
            account_move_line_ids = account_move.line_ids.filtered(line_ids_predicate)
            self.assertRecordValues(account_move_line_ids, line_ids)
            self.assertRecordValues(account_move, [expected_account_move_vals])

            # Check reconciliation status
            for line, reconciliation_status in zip(account_move_line_ids, reconciliation_statuses):
                # See 'account_move_line._compute_amount_residual'  for more explanation
                if reconciliation_status == 'fully_reconciled':
                    if line.matching_number:
                        self.assertTrue(line.full_reconcile_id)
                    self.assertAlmostEqual(line.amount_residual, 0)
                elif reconciliation_status == 'partially_reconciled':
                    self.assertFalse(line.full_reconcile_id)
                    if line.reconciled:
                        self.assertAlmostEqual(line.amount_residual, 0)
                    else:
                        self.assertGreater(abs(line.amount_residual), 0)
                elif reconciliation_status == 'not_reconciled':
                    self.assertFalse(line.full_reconcile_id)
                    self.assertFalse(line.reconciled)
        else:
            # if the expected_account_move_vals is falsy, the account_move should be falsy.
            self.assertFalse(account_move)

    def make_payment(self, order, payment_method, amount):
        """ Make payment for the order using the given payment method.
        """
        payment_context = {"active_id": order.id, "active_ids": order.ids}
        return self.env['pos.make.payment'].with_context(**payment_context).create({
            'amount': amount,
            'payment_method_id': payment_method.id,
        }).check()
