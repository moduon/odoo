# Part of Odoo. See LICENSE file for full copyright and licensing details.

import datetime
import logging
from collections import defaultdict, namedtuple, OrderedDict
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import float_is_zero
from odoo.tools.misc import split_every

_logger = logging.getLogger(__name__)


class ProcurementException(Exception):
    """An exception raised by ProcurementGroup `run` containing all the faulty
    procurements.
    """
    def __init__(self, procurement_exceptions):
        """:param procurement_exceptions: a list of tuples containing the faulty
        procurement and their error messages
        :type procurement_exceptions: list
        """
        self.procurement_exceptions = procurement_exceptions


class StockRule(models.Model):
    """ A rule describe what a procurement should do; produce, buy, move, ... """
    _name = 'stock.rule'
    _description = "Stock Rule"
    _order = "sequence, id"
    _check_company_auto = True

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        if 'company_id' in fields and not res['company_id']:
            res['company_id'] = self.env.company.id
        return res

    name = fields.Char(
        'Name', required=True, translate=True,
        help="This field will fill the packing origin and the name of its moves")
    active = fields.Boolean(
        'Active', default=True,
        help="If unchecked, it will allow you to hide the rule without removing it.")
    group_propagation_option = fields.Selection([
        ('none', 'Leave Empty'),
        ('propagate', 'Propagate'),
        ('fixed', 'Fixed')], string="Propagation of Procurement Group", default='propagate')
    group_id = fields.Many2one('procurement.group', 'Fixed Procurement Group')
    action = fields.Selection(
        selection=[('pull', 'Pull From'), ('push', 'Push To'), ('pull_push', 'Pull & Push')], string='Action',
        default='pull', required=True, index=True)
    sequence = fields.Integer('Sequence', default=20)
    company_id = fields.Many2one('res.company', 'Company',
        default=lambda self: self.env.company,
        domain="[('id', '=?', route_company_id)]")
    location_dest_id = fields.Many2one('stock.location', 'Destination Location', required=True, check_company=True, index=True)
    location_src_id = fields.Many2one('stock.location', 'Source Location', check_company=True, index=True)
    location_dest_from_rule = fields.Boolean(
        "Destination location origin from rule", default=False,
        help="When set to True the destination location of the stock.move will be the rule."
        "Otherwise, it takes it from the picking type.")
    route_id = fields.Many2one('stock.route', 'Route', required=True, ondelete='cascade', index=True)
    route_company_id = fields.Many2one(related='route_id.company_id', string='Route Company')
    procure_method = fields.Selection([
        ('make_to_stock', 'Take From Stock'),
        ('make_to_order', 'Trigger Another Rule'),
        ('mts_else_mto', 'Take From Stock, if unavailable, Trigger Another Rule')], string='Supply Method', default='make_to_stock', required=True,
        help="Take From Stock: the products will be taken from the available stock of the source location.\n"
             "Trigger Another Rule: the system will try to find a stock rule to bring the products in the source location. The available stock will be ignored.\n"
             "Take From Stock, if Unavailable, Trigger Another Rule: the products will be taken from the available stock of the source location."
             "If there is no stock available, the system will try to find a  rule to bring the products in the source location.")
    route_sequence = fields.Integer('Route Sequence', related='route_id.sequence', store=True, compute_sudo=True)
    picking_type_id = fields.Many2one(
        'stock.picking.type', 'Operation Type',
        required=True, check_company=True,
        domain="[('code', 'in', picking_type_code_domain)] if picking_type_code_domain else []")
    picking_type_code_domain = fields.Json(compute='_compute_picking_type_code_domain')
    delay = fields.Integer('Lead Time', default=0, help="The expected date of the created transfer will be computed based on this lead time.")
    partner_address_id = fields.Many2one(
        'res.partner', 'Partner Address',
        check_company=True,
        help="Address where goods should be delivered. Optional.")
    propagate_cancel = fields.Boolean(
        'Cancel Next Move', default=False,
        help="When ticked, if the move created by this rule is cancelled, the next move will be cancelled too.")
    propagate_carrier = fields.Boolean(
        'Propagation of carrier', default=False,
        help="When ticked, carrier of shipment will be propagated.")
    warehouse_id = fields.Many2one('stock.warehouse', 'Warehouse', check_company=True, index=True)
    propagate_warehouse_id = fields.Many2one(
        'stock.warehouse', 'Warehouse to Propagate',
        help="The warehouse to propagate on the created move/procurement, which can be different of the warehouse this rule is for (e.g for resupplying rules from another warehouse)")
    auto = fields.Selection([
        ('manual', 'Manual Operation'),
        ('transparent', 'Automatic No Step Added')], string='Automatic Move',
        default='manual', required=True,
        help="The 'Manual Operation' value will create a stock move after the current one. "
             "With 'Automatic No Step Added', the location is replaced in the original move.")
    rule_message = fields.Html(compute='_compute_action_message')
    push_domain = fields.Char('Push Applicability')

    def copy_data(self, default=None):
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        if 'name' not in default:
            for rule, vals in zip(self, vals_list):
                vals['name'] = _("%s (copy)", rule.name)
        return vals_list

    @api.constrains('company_id')
    def _check_company_consistency(self):
        for rule in self:
            route = rule.route_id
            if route.company_id and rule.company_id.id != route.company_id.id:
                raise ValidationError(_(
                    "Rule %(rule)s belongs to %(rule_company)s while the route belongs to %(route_company)s.",
                    rule=rule.display_name,
                    rule_company=rule.company_id.display_name,
                    route_company=route.company_id.display_name,
                ))

    @api.onchange('picking_type_id')
    def _onchange_picking_type(self):
        """ Modify locations to the default picking type's locations source and
        destination.
        Enable the delay alert if the picking type is a delivery
        """
        self.location_src_id = self.picking_type_id.default_location_src_id.id
        self.location_dest_id = self.picking_type_id.default_location_dest_id.id

    @api.onchange('route_id', 'company_id')
    def _onchange_route(self):
        """ Ensure that the rule's company is the same than the route's company. """
        if self.route_id.company_id:
            self.company_id = self.route_id.company_id
        if self.picking_type_id.warehouse_id.company_id != self.route_id.company_id:
            self.picking_type_id = False

    def _get_message_values(self):
        """ Return the source, destination and picking_type applied on a stock
        rule. The purpose of this function is to avoid code duplication in
        _get_message_dict functions since it often requires those data.
        """
        source = self.location_src_id and self.location_src_id.display_name or _('Source Location')
        destination = self.location_dest_id and self.location_dest_id.display_name or _('Destination Location')
        direct_destination = self.picking_type_id and self.picking_type_id.default_location_dest_id != self.location_dest_id and self.picking_type_id.default_location_dest_id.display_name
        operation = self.picking_type_id and self.picking_type_id.name or _('Operation Type')
        return source, destination, direct_destination, operation

    def _get_message_dict(self):
        """ Return a dict with the different possible message used for the
        rule message. It should return one message for each stock.rule action
        (except push and pull). This function is override in mrp and
        purchase_stock in order to complete the dictionary.
        """
        message_dict = {}
        source, destination, direct_destination, operation = self._get_message_values()
        if self.action in ('push', 'pull', 'pull_push'):
            suffix = ""
            if self.action in ('pull', 'pull_push') and direct_destination and not self.location_dest_from_rule:
                suffix = _("<br>The products will be moved towards <b>%(destination)s</b>, <br/> as specified from <b>%(operation)s</b> destination.", destination=direct_destination, operation=operation)
            if self.procure_method == 'make_to_order' and self.location_src_id:
                suffix += _("<br>A need is created in <b>%s</b> and a rule will be triggered to fulfill it.", source)
            if self.procure_method == 'mts_else_mto' and self.location_src_id:
                suffix += _("<br>If the products are not available in <b>%s</b>, a rule will be triggered to bring the missing quantity in this location.", source)
            message_dict = {
                'pull': _(
                    'When products are needed in <b>%(destination)s</b>, <br> <b>%(operation)s</b> are created from <b>%(source_location)s</b> to fulfill the need. %(suffix)s',
                    destination=destination,
                    operation=operation,
                    source_location=source,
                    suffix=suffix,
                ),
                'push': _(
                    'When products arrive in <b>%(source_location)s</b>, <br> <b>%(operation)s</b> are created to send them to <b>%(destination)s</b>.',
                    source_location=source,
                    operation=operation,
                    destination=destination,
                ),
            }
        return message_dict

    @api.depends('action', 'location_dest_id', 'location_src_id', 'picking_type_id', 'procure_method', 'location_dest_from_rule')
    def _compute_action_message(self):
        """ Generate dynamicaly a message that describe the rule purpose to the
        end user.
        """
        action_rules = self.filtered(lambda rule: rule.action)
        for rule in action_rules:
            message_dict = rule._get_message_dict()
            message = message_dict.get(rule.action) and message_dict[rule.action] or ""
            if rule.action == 'pull_push':
                message = message_dict['pull'] + "<br/><br/>" + message_dict['push']
            rule.rule_message = message
        (self - action_rules).rule_message = None

    @api.depends('action')
    def _compute_picking_type_code_domain(self):
        self.picking_type_code_domain = []

    def _run_push(self, move):
        """ Apply a push rule on a move.
        If the rule is 'no step added' it will modify the destination location
        on the move.
        If the rule is 'manual operation' it will generate a new move in order
        to complete the section define by the rule.
        Care this function is not call by method run. It is called explicitely
        in stock_move.py inside the method _push_apply
        """
        self.ensure_one()
        new_date = fields.Datetime.to_string(move.date + relativedelta(days=self.delay))
        if self.auto == 'transparent':
            old_dest_location = move.location_dest_id
            move.write({'date': new_date, 'location_dest_id': self.location_dest_id.id})
            # make sure the location_dest_id is consistent with the move line location dest
            if move.move_line_ids:
                move.move_line_ids.location_dest_id = move.location_dest_id._get_putaway_strategy(move.product_id) or move.location_dest_id

            # avoid looping if a push rule is not well configured; otherwise call again push_apply to see if a next step is defined
            if self.location_dest_id != old_dest_location:
                # TDE FIXME: should probably be done in the move model IMO
                return move._push_apply()[:1]
        else:
            new_move_vals = self._push_prepare_move_copy_values(move, new_date)
            new_move = move.sudo().copy(new_move_vals)
            # when no more push we should reach final destination
            if new_move._skip_push():
                new_move.write({'location_dest_id': new_move.location_final_id.id})
            if new_move._should_bypass_reservation():
                new_move.write({'procure_method': 'make_to_stock'})
            if not new_move.location_id.should_bypass_reservation():
                move.write({'move_dest_ids': [(4, new_move.id)]})
            return new_move

    def _push_prepare_move_copy_values(self, move_to_copy, new_date):
        company_id = self.company_id.id
        copied_quantity = move_to_copy.quantity
        final_location_id = False
        if move_to_copy.location_final_id and not move_to_copy.location_dest_id._child_of(move_to_copy.location_final_id):
            final_location_id = move_to_copy.location_final_id.id
        if move_to_copy.product_uom.compare(move_to_copy.product_uom_qty, 0) < 0:
            copied_quantity = move_to_copy.product_uom_qty
        if not company_id:
            company_id = self.sudo().warehouse_id and self.sudo().warehouse_id.company_id.id or self.sudo().picking_type_id.warehouse_id.company_id.id
        new_move_vals = {
            'product_uom_qty': copied_quantity,
            'origin': move_to_copy.origin or move_to_copy.picking_id.name or "/",
            'location_id': move_to_copy.location_dest_id.id,
            'location_dest_id': self.location_dest_id.id,
            'location_final_id': final_location_id,
            'rule_id': self.id,
            'date': new_date,
            'date_deadline': move_to_copy.date_deadline,
            'company_id': company_id,
            'picking_id': False,
            'picking_type_id': self.picking_type_id.id,
            'propagate_cancel': self.propagate_cancel,
            'warehouse_id': self.warehouse_id.id,
            'procure_method': 'make_to_order',
        }
        return new_move_vals

    @api.model
    def _run_pull(self, procurements):
        moves_values_by_company = defaultdict(list)

        # To handle the `mts_else_mto` procure method, we do a preliminary loop to
        # isolate the products we would need to read the forecasted quantity,
        # in order to to batch the read. We also make a sanitary check on the
        # `location_src_id` field.
        for procurement, rule in procurements:
            if not rule.location_src_id:
                msg = _('No source location defined on stock rule: %s!', rule.name)
                raise ProcurementException([(procurement, msg)])

        # Prepare the move values, adapt the `procure_method` if needed.
        procurements = sorted(procurements, key=lambda proc: proc[0].product_uom.compare(proc[0].product_qty, 0.0) > 0)
        for procurement, rule in procurements:
            procure_method = rule.procure_method
            if rule.procure_method == 'mts_else_mto':
                procure_method = 'make_to_stock'

            move_values = rule._get_stock_move_values(*procurement)
            move_values['procure_method'] = procure_method
            moves_values_by_company[procurement.company_id.id].append(move_values)

        for company_id, moves_values in moves_values_by_company.items():
            # create the move as SUPERUSER because the current user may not have the rights to do it (mto product launched by a sale for example)
            moves = self.env['stock.move'].sudo().with_company(company_id).create(moves_values)
            # Since action_confirm launch following procurement_group we should activate it.
            moves._action_confirm()
        return True

    def _get_custom_move_fields(self):
        """ The purpose of this method is to be override in order to easily add
        fields from procurement 'values' argument to move data.
        """
        return []

    def _get_stock_move_values(self, product_id, product_qty, product_uom, location_dest_id, name, origin, company_id, values):
        ''' Returns a dictionary of values that will be used to create a stock move from a procurement.
        This function assumes that the given procurement has a rule (action == 'pull' or 'pull_push') set on it.

        :rtype: dictionary
        '''
        group_id = False
        if self.group_propagation_option == 'propagate':
            group_id = values.get('group_id', False) and values['group_id'].id
        elif self.group_propagation_option == 'fixed':
            group_id = self.group_id.id

        date_scheduled = fields.Datetime.to_string(
            fields.Datetime.from_string(values['date_planned']) - relativedelta(days=self.delay or 0)
        )
        date_deadline = values.get('date_deadline') and (fields.Datetime.to_datetime(values['date_deadline']) - relativedelta(days=self.delay or 0)) or False
        partner = self.partner_address_id or (values.get('group_id', False) and values['group_id'].partner_id)
        # it is possible that we've already got some move done, so check for the done qty and create
        # a new move with the correct qty
        qty_left = product_qty

        move_dest_ids = values.get('move_dest_ids') and [(4, x.id) for x in values['move_dest_ids']] or []

        # when create chained moves for inter-warehouse transfers, set the warehouses as partners
        if not partner and move_dest_ids:
            move_dest = values['move_dest_ids']
            if location_dest_id == company_id.internal_transit_location_id:
                partners = move_dest.location_dest_id.warehouse_id.partner_id
                if len(partners) == 1:
                    partner = partners
                move_dest.partner_id = self.location_src_id.warehouse_id.partner_id or self.company_id.partner_id

        # If the quantity is negative the move should be considered as a refund
        if product_uom.compare(product_qty, 0.0) < 0:
            values['to_refund'] = True

        move_values = {
            'company_id': self.company_id.id or self.location_src_id.company_id.id or self.location_dest_id.company_id.id or company_id.id,
            'product_id': product_id.id,
            'product_uom': product_uom.id,
            'product_uom_qty': qty_left,
            'partner_id': partner.id if partner else False,
            'location_id': self.location_src_id.id,
            'location_final_id': location_dest_id.id,
            'move_dest_ids': move_dest_ids,
            'rule_id': self.id,
            'procure_method': self.procure_method,
            'origin': origin,
            'picking_type_id': self.picking_type_id.id,
            'group_id': group_id,
            'procurement_values': self._serialize_procurement_values(values),
            'route_ids': [(4, route.id) for route in values.get('route_ids', [])],
            'never_product_template_attribute_value_ids': values.get('never_product_template_attribute_value_ids'),
            'warehouse_id': self.warehouse_id.id,
            'date': date_scheduled,
            'date_deadline': False if self.group_propagation_option == 'fixed' else date_deadline,
            'propagate_cancel': self.propagate_cancel,
            'priority': values.get('priority', "0"),
            'orderpoint_id': values.get('orderpoint_id') and values['orderpoint_id'].id,
        }
        if self.location_dest_from_rule:
            move_values['location_dest_id'] = self.location_dest_id.id
        for field in self._get_custom_move_fields():
            if field in values:
                move_values[field] = values.get(field)
        return move_values

    def _serialize_procurement_values(self, values):
        """Helper method to serialize procurement values for storage.

        This method handles the serialization of different types of values:
        - BaseModel instances are converted to their IDs
        - Datetime and Date fields are converted to strings
        - Other values are kept as is

        :param values: Dictionary of procurement values
        :return: Dictionary with serialized values
        """
        serialized = {}
        for key, value in values.items():
            if isinstance(value, models.BaseModel):
                serialized[key] = value.ids
            elif isinstance(value, (datetime.datetime, datetime.date)):
                serialized[key] = value.isoformat()
            elif isinstance(value, (fields.Datetime, fields.Date)):
                serialized[key] = fields.Datetime.to_string(value) if isinstance(value, fields.Datetime) else fields.Date.to_string(value)
            else:
                serialized[key] = value
        return serialized

    def _get_lead_days(self, product, **values):
        """Returns the cumulative delay and its description encountered by a
        procurement going through the rules in `self`.

        :param product: the product of the procurement
        :type product: :class:`~odoo.addons.product.models.product.ProductProduct`
        :return: the cumulative delay and cumulative delay's description
        :rtype: tuple[defaultdict(float), list[str, str]]
        """
        _ = self.env._
        delays = defaultdict(float)
        delay = sum(self.filtered(lambda r: r.action in ['pull', 'pull_push']).mapped('delay'))
        delays['total_delay'] += delay
        global_visibility_days = self.env.context.get('global_visibility_days', self.env['ir.config_parameter'].sudo().get_param('stock.visibility_days', 0))
        if global_visibility_days:
            delays['total_delay'] += int(global_visibility_days)
        if self.env.context.get('bypass_delay_description'):
            delay_description = []
        else:
            delay_description = [
                (_('Delay on %s', rule.name), _('+ %d day(s)', rule.delay))
                for rule in self
                if rule.action in ['pull', 'pull_push'] and rule.delay
            ]
        if global_visibility_days:
            delay_description.append((_('Time Horizon'), _('+ %d day(s)', int(global_visibility_days))))
        return delays, delay_description


class ProcurementGroup(models.Model):
    """
    The procurement group class is used to group products together
    when computing procurements. (tasks, physical products, ...)

    The goal is that when you have one sales order of several products
    and the products are pulled from the same or several location(s), to keep
    having the moves grouped into pickings that represent the sales order.

    Used in: sales order (to group delivery order lines like the so), pull/push
    rules (to pack like the delivery order), on orderpoints (e.g. for wave picking
    all the similar products together).

    Grouping is made only if the source and the destination is the same.
    Suppose you have 4 lines on a picking from Output where 2 lines will need
    to come from Input (crossdock) and 2 lines coming from Stock -> Output As
    the four will have the same group ids from the SO, the move from input will
    have a stock.picking with 2 grouped lines and the move from stock will have
    2 grouped lines also.

    The name is usually the name of the original document (sales order) or a
    sequence computed if created manually.
    """
    _name = 'procurement.group'
    _description = 'Procurement Group'
    _order = "id desc"

    Procurement = namedtuple('Procurement', ['product_id', 'product_qty',
        'product_uom', 'location_id', 'name', 'origin', 'company_id', 'values'])
    partner_id = fields.Many2one('res.partner', 'Partner')
    name = fields.Char(
        'Reference',
        default=lambda self: self.env['ir.sequence'].next_by_code('procurement.group') or '',
        required=True)
    move_type = fields.Selection(
        [('direct', 'Partial'), ('one', 'All at once')],
        string='Delivery Type'
    )
    stock_move_ids = fields.One2many('stock.move', 'group_id', string="Related Stock Moves")

    @api.model
    def _skip_procurement(self, procurement):
        return procurement.product_id.type != "consu" or float_is_zero(
            procurement.product_qty, precision_rounding=procurement.product_uom.rounding
        )

    @api.model
    def run(self, procurements, raise_user_error=True):
        """Fulfil `procurements` with the help of stock rules.

        Procurements are needs of products at a certain location. To fulfil
        these needs, we need to create some sort of documents (`stock.move`
        by default, but extensions of `_run_` methods allow to create every
        type of documents).

        :param procurements: the description of the procurement
        :type procurements: list of `~odoo.addons.stock.models.stock_rule.ProcurementGroup.Procurement`
        :param raise_user_error: will raise either an UserError or a ProcurementException
        :type raise_user_error: boolan, optional
        :raises UserError: if `raise_user_error` is True and a procurement isn't fulfillable
        :raises ProcurementException: if `raise_user_error` is False and a procurement isn't fulfillable
        """

        def raise_exception(procurement_errors):
            if raise_user_error:
                dummy, errors = zip(*procurement_errors)
                raise UserError('\n'.join(errors))
            else:
                raise ProcurementException(procurement_errors)
        actions_to_run = defaultdict(list)
        procurement_errors = []
        for procurement in procurements:
            procurement.values.setdefault('company_id', procurement.location_id.company_id)
            procurement.values.setdefault('priority', '0')
            procurement.values.setdefault('date_planned', procurement.values.get('date_planned', False) or fields.Datetime.now())
            if self._skip_procurement(procurement):
                continue
            rule = self._get_rule(procurement.product_id, procurement.location_id, procurement.values)
            if not rule:
                error = _('No rule has been found to replenish "%(product)s" in "%(location)s".\nVerify the routes configuration on the product.',
                    product=procurement.product_id.display_name, location=procurement.location_id.display_name)
                procurement_errors.append((procurement, error))
            else:
                action = 'pull' if rule.action == 'pull_push' else rule.action
                actions_to_run[action].append((procurement, rule))

        if procurement_errors:
            raise_exception(procurement_errors)

        for action, procurements in actions_to_run.items():
            if hasattr(self.env['stock.rule'], '_run_%s' % action):
                try:
                    getattr(self.env['stock.rule'], '_run_%s' % action)(procurements)
                except ProcurementException as e:
                    procurement_errors += e.procurement_exceptions
            else:
                _logger.error("The method _run_%s doesn't exist on the procurement rules" % action)

        if procurement_errors:
            raise_exception(procurement_errors)
        return True

    @api.model
    def _search_rule_for_warehouses(self, route_ids, packaging_uom_id, product_id, warehouse_ids, domain):
        domain = Domain(domain)
        if warehouse_ids:
            domain &= Domain('warehouse_id', 'in', [False, *warehouse_ids.ids])
        valid_route_ids = set()
        if route_ids:
            valid_route_ids |= set(route_ids.ids)
        if packaging_uom_id:
            packaging_routes = packaging_uom_id.package_type_id.route_ids
            valid_route_ids |= set(packaging_routes.ids)
        valid_route_ids |= set((product_id.route_ids | product_id.categ_id.total_route_ids).ids)
        if warehouse_ids:
            valid_route_ids |= set(warehouse_ids.route_ids.ids)
        if valid_route_ids:
            domain &= Domain('route_id', 'in', list(valid_route_ids))
        res = self.env["stock.rule"]._read_group(
            domain,
            groupby=["location_dest_id", "warehouse_id", "route_id"],
            aggregates=["id:recordset"],
            order="route_sequence:min, sequence:min",
        )
        rule_dict = defaultdict(OrderedDict)
        for group in res:
            rule_dict[group[0].id, group[2].id][group[1].id] = group[3].sorted(lambda rule: (rule.route_sequence, rule.sequence))[0]
        return rule_dict

    def _search_rule(self, route_ids, packaging_uom_id, product_id, warehouse_id, domain):
        """ First find a rule among the ones defined on the procurement
        group, then try on the routes defined for the product, finally fallback
        on the default behavior
        """
        Rule = self.env['stock.rule']
        res = self.env['stock.rule']
        domain = Domain(domain)
        if warehouse_id:
            domain &= Domain('warehouse_id', 'in', [False, warehouse_id.id])
        domain = domain.optimize(Rule)
        if route_ids:
            res = Rule.search(Domain('route_id', 'in', route_ids.ids) & domain, order='route_sequence, sequence', limit=1)
        if not res and packaging_uom_id:
            packaging_routes = packaging_uom_id.package_type_id.route_ids
            if packaging_routes:
                res = Rule.search(Domain('route_id', 'in', packaging_routes.ids) & domain, order='route_sequence, sequence', limit=1)
        if not res:
            product_routes = product_id.route_ids | product_id.categ_id.total_route_ids
            if product_routes:
                res = Rule.search(Domain('route_id', 'in', product_routes.ids) & domain, order='route_sequence, sequence', limit=1)
        if not res and warehouse_id:
            warehouse_routes = warehouse_id.route_ids
            if warehouse_routes:
                res = Rule.search(Domain('route_id', 'in', warehouse_routes.ids) & domain, order='route_sequence, sequence', limit=1)
        return res

    @api.model
    def _get_rule(self, product_id, location_id, values):
        """ Find a pull rule for the location_id, fallback on the parent
        locations if it could not be found.
        """
        result = self.env['stock.rule']
        if not location_id:
            return result
        locations = location_id
        # Get the location hierarchy, starting from location_id up to its root location.
        while locations[-1].location_id:
            locations |= locations[-1].location_id
        domain = self._get_rule_domain(locations, values)
        # Get a mapping (location_id, route_id) -> warehouse_id -> rule_id
        rule_dict = self._search_rule_for_warehouses(
            values.get("route_ids", False),
            values.get("packaging_uom_id", False),
            product_id,
            values.get("warehouse_id", locations.warehouse_id),
            domain,
        )

        def extract_rule(rule_dict, route_ids, warehouse_id, location_dest_id):
            rule = self.env['stock.rule']
            for route_id in sorted(route_ids, key=lambda r: r.sequence):
                sub_dict = rule_dict.get((location_dest_id.id, route_id.id))
                if not sub_dict:
                    continue
                if not warehouse_id:
                    rule = sub_dict[next(iter(sub_dict))]
                else:
                    rule = sub_dict.get(warehouse_id.id)
                    rule = rule or sub_dict[False]
                if rule:
                    break
            return rule

        def get_rule_for_routes(rule_dict, route_ids, packaging_uom_id, product_id, warehouse_id, location_dest_id):
            res = self.env['stock.rule']
            if route_ids:
                res = extract_rule(rule_dict, route_ids, warehouse_id, location_dest_id)
            if not res and packaging_uom_id:
                res = extract_rule(rule_dict, packaging_uom_id.package_type_id.route_ids, warehouse_id, location_dest_id)
            if not res:
                res = extract_rule(rule_dict, product_id.route_ids | product_id.categ_id.total_route_ids, warehouse_id, location_dest_id)
            if not res and warehouse_id:
                res = extract_rule(rule_dict, warehouse_id.route_ids, warehouse_id, location_dest_id)
            return res

        location = location_id
        # Go through the location hierarchy again, this time breaking at the first valid stock.rule found
        # in rules_by_location.
        inter_comp_location_checked = False
        while (not result) and location:
            candidate_locations = location
            if not inter_comp_location_checked and self._check_intercomp_location(location):
                # Add the intercomp location to candidate_locations as the intercomp domain was added
                # above in the call to _get_rule_domain.
                inter_comp_location = self.env.ref('stock.stock_location_customers', raise_if_not_found=False)
                candidate_locations |= inter_comp_location
                inter_comp_location_checked = True
            for candidate_location in candidate_locations:
                result = get_rule_for_routes(
                    rule_dict,
                    values.get("route_ids", self.env['stock.route']),
                    values.get("packaging_uom_id", self.env['uom.uom']),
                    product_id,
                    values.get("warehouse_id", candidate_location.warehouse_id),
                    candidate_location,
                )
                if result:
                    break
            else:
                location = location.location_id
        return result

    @api.model
    def _check_intercomp_location(self, locations):
        if locations.filtered(lambda location: location.usage == 'transit'):
            inter_comp_location = self.env.ref('stock.stock_location_inter_company', raise_if_not_found=False)
            return inter_comp_location and inter_comp_location.id in locations.ids

    @api.model
    def _get_rule_domain(self, locations, values):
        location_ids = locations.ids
        # If the method is called to find rules towards the Inter-company location, also add the 'Customer' location in the domain.
        # This is to avoid having to duplicate every rules that deliver to Customer to have the Inter-company part.
        if self._check_intercomp_location(locations):
            location_ids.append(self.env.ref('stock.stock_location_customers', raise_if_not_found=False).id)
        domain = Domain('location_dest_id', 'in', location_ids) & Domain('action', '!=', 'push')
        # In case the method is called by the superuser, we need to restrict the rules to the
        # ones of the company. This is not useful as a regular user since there is a record
        # rule to filter out the rules based on the company.
        if self.env.su and values.get('company_id'):
            company_ids = set(values.get('company_id').ids)
            if values.get('route_ids'):
                company_ids |= set(values['route_ids'].company_id.ids)
            domain_company = ['|', ('company_id', '=', False), ('company_id', 'child_of', list(company_ids))]
            domain &= Domain(domain_company)
        return domain

    @api.model
    def _get_push_rule(self, product_id, location_dest_id, values):
        """ Find a push rule for the location_dest_id, with a fallback to the parent locations if none could be found.
        """
        found_rule = self.env['stock.rule']
        location = location_dest_id
        while (not found_rule) and location:
            domain = Domain('location_src_id', '=', location.id) & Domain('action', 'in', ('push', 'pull_push'))
            if dom := values.get('domain'):
                domain &= Domain(dom)
            found_rule = self._search_rule(values.get('route_ids'), values.get('packaging_uom_id'), product_id, values.get('warehouse_id'), domain)
            location = location.location_id
        return found_rule

    @api.model
    def _get_moves_to_assign_domain(self, company_id):
        return Domain([
            ('company_id', '=?', company_id),
            ('state', 'in', ['confirmed', 'partially_available']),
            ('product_uom_qty', '!=', 0.0),
            '|',
                ('reservation_date', '<=', fields.Date.today()),
                ('picking_type_id.reservation_method', '=', 'at_confirm'),
        ])

    @api.model
    def _run_scheduler_tasks(self, use_new_cursor=False, company_id=False):
        if use_new_cursor:
            self.env['ir.cron']._commit_progress(remaining=self._get_scheduler_tasks_to_do())

        # Minimum stock rules
        domain = self._get_orderpoint_domain(company_id=company_id)
        orderpoints = self.env['stock.warehouse.orderpoint'].search(domain)
        orderpoints.sudo()._compute_qty_to_order_computed()
        orderpoints.sudo()._procure_orderpoint_confirm(use_new_cursor=use_new_cursor, company_id=company_id, raise_user_error=False)

        if use_new_cursor:
            self.env['ir.cron']._commit_progress(1)

        # Search all confirmed stock_moves and try to assign them
        domain = self._get_moves_to_assign_domain(company_id)
        moves_to_assign = self.env['stock.move'].search(domain, limit=None,
            order='reservation_date, priority desc, date asc, id asc')
        for moves_chunk in split_every(1000, moves_to_assign.ids):
            self.env['stock.move'].browse(moves_chunk).sudo()._action_assign()
            if use_new_cursor:
                self.env.cr.commit()
                _logger.info("A batch of %d moves are assigned and committed", len(moves_chunk))

        if use_new_cursor:
            self.env['ir.cron']._commit_progress(1)

        # Merge duplicated quants
        self.env['stock.quant']._quant_tasks()

        if use_new_cursor:
            self.env['ir.cron']._commit_progress(1)

    @api.model
    def _get_scheduler_tasks_to_do(self):
        """ Number of task to be executed by the stock scheduler. This number will be given in log
        message to know how many tasks succeeded."""
        return 3

    @api.model
    def run_scheduler(self, use_new_cursor=False, company_id=False):
        """ Call the scheduler in order to check the running procurements (super method), to check the minimum stock rules
        and the availability of moves. This function is intended to be run for all the companies at the same time, so
        we run functions as SUPERUSER to avoid intercompanies and access rights issues. """
        try:
            self._run_scheduler_tasks(use_new_cursor=use_new_cursor, company_id=company_id)
        except Exception:
            _logger.error("Error during stock scheduler", exc_info=True)
            raise
        return {}

    @api.model
    def _get_orderpoint_domain(self, company_id=False):
        domain = [('trigger', '=', 'auto'), ('product_id.active', '=', True)]
        if company_id:
            domain += [('company_id', '=', company_id)]
        return domain
