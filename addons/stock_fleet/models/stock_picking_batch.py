# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import fields, models, api


class StockPickingBatch(models.Model):
    _inherit = 'stock.picking.batch'

    vehicle_id = fields.Many2one('fleet.vehicle', string="Vehicle")
    vehicle_category_id = fields.Many2one(
        'fleet.vehicle.model.category', string="Vehicle Category",
        compute='_compute_vehicle_category_id', store=True, readonly=False)
    dock_id = fields.Many2one('stock.location', string="Dock Location", domain="[('warehouse_id', '=', warehouse_id), ('is_a_dock', '=', True)]",
                              compute='_compute_dock_id', store=True, readonly=False)
    vehicle_weight_capacity = fields.Float(string="Vehcilce Payload Capacity",
                              related='vehicle_category_id.weight_capacity')
    weight_uom_name = fields.Char(string='Weight unit of measure label', compute='_compute_weight_uom_name')
    vehicle_volume_capacity = fields.Float(string="Max Volume (m³)",
                              related='vehicle_category_id.volume_capacity')
    volume_uom_name = fields.Char(string='Volume unit of measure label', compute='_compute_volume_uom_name')
    driver_id = fields.Many2one(
        'res.partner', compute="_compute_driver_id", string="Driver", store=True, readonly=False)
    used_weight_percentage = fields.Float(
        string="Weight %", compute='_compute_capacity_percentage')
    used_volume_percentage = fields.Float(
        string="Volume %", compute='_compute_capacity_percentage')
    end_date = fields.Datetime('End Date', compute='_compute_end_date', store=True)
    has_dispatch_management = fields.Boolean(string="Dispatch Management", related='picking_type_id.dispatch_management')

    # Compute
    @api.depends('scheduled_date')
    def _compute_end_date(self):
        for batch in self:
            if not batch.end_date or (batch.scheduled_date and batch.end_date < batch.scheduled_date):
                batch.end_date = batch.scheduled_date + timedelta(hours=1) if batch.scheduled_date else False

    @api.depends('vehicle_id')
    def _compute_vehicle_category_id(self):
        for rec in self:
            rec.vehicle_category_id = rec.vehicle_id.category_id

    @api.depends('picking_ids', 'picking_ids.location_id', 'picking_ids.location_dest_id')
    def _compute_dock_id(self):
        for batch in self:
            if batch.picking_ids:
                if len(batch.picking_ids.location_id) == 1 and batch.picking_ids.location_id.is_a_dock:
                    batch.dock_id = batch.picking_ids.location_id

    def _compute_weight_uom_name(self):
        self.weight_uom_name = self.env['product.template']._get_weight_uom_name_from_ir_config_parameter()

    def _compute_volume_uom_name(self):
        self.volume_uom_name = self.env['product.template']._get_volume_uom_name_from_ir_config_parameter()

    @api.depends('vehicle_id')
    def _compute_driver_id(self):
        for rec in self:
            rec.driver_id = rec.vehicle_id.driver_id

    @api.depends('estimated_shipping_weight', 'vehicle_category_id.weight_capacity',
                 'estimated_shipping_volume', 'vehicle_category_id.volume_capacity')
    def _compute_capacity_percentage(self):
        self.used_weight_percentage = False
        self.used_volume_percentage = False
        for batch in self:
            if batch.vehicle_weight_capacity:
                batch.used_weight_percentage = 100 * (batch.estimated_shipping_weight / batch.vehicle_weight_capacity)
            if batch.vehicle_volume_capacity:
                batch.used_volume_percentage = 100 * (batch.estimated_shipping_volume / batch.vehicle_volume_capacity)

    # CRUD

    @api.model_create_multi
    def create(self, vals_list):
        batches = super().create(vals_list)
        batches.order_on_zip()
        batches.filtered(lambda b: b.dock_id)._set_moves_destination_to_dock()
        return batches

    def write(self, vals):
        res = super().write(vals)
        if 'dock_id' in vals:
            self._set_moves_destination_to_dock()
        return res

    # Public actions
    def order_on_zip(self):
        sorted_records = self.picking_ids.sorted(lambda p: p.zip or "")
        for idx, record in enumerate(sorted_records):
            record.batch_sequence = idx

    # Private buisness logic
    def _set_moves_destination_to_dock(self):
        for batch in self:
            if not batch.dock_id:
                batch.picking_ids._reset_location()
            elif batch.picking_type_id.code in ["internal", "incoming"]:
                batch.picking_ids.move_ids.write({'location_dest_id': batch.dock_id.id})
            else:
                batch.picking_ids.move_ids.write({'location_id': batch.dock_id.id})

    def _get_merged_batch_vals(self):
        self.ensure_one()
        vals = super()._get_merged_batch_vals()
        vals.update({
            'vehicle_id': self.vehicle_id.id,
            'dock_id': self.dock_id.id,
        })
        return vals
