# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    l10n_in_hsn_code = fields.Char(string="HSN/SAC Code", compute="_compute_l10n_in_hsn_code", store=True, readonly=False, copy=False)

    @api.depends('product_id')
    def _compute_l10n_in_hsn_code(self):
        indian_lines = self.filtered(lambda line: line.company_id.account_fiscal_country_id.code == 'IN')
        (self - indian_lines).l10n_in_hsn_code = False
        for line in indian_lines:
            if line.product_id:
                line.l10n_in_hsn_code = line.product_id.l10n_in_hsn_code

    @api.model
    def _load_pos_data_fields(self, config):
        params = super()._load_pos_data_fields(config)
        if self.env.company.country_id.code == 'IN':
            params += ['l10n_in_hsn_code']
        return params

    def _prepare_base_line_for_taxes_computation(self):
        res = super()._prepare_base_line_for_taxes_computation()
        if self.company_id.l10n_in_is_gst_registered:
            res.update({
                'l10n_in_hsn_code': self.l10n_in_hsn_code,
            })
        return res
