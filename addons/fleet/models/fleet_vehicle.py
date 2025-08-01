# Part of Odoo. See LICENSE file for full copyright and licensing details.

from collections import defaultdict
from dateutil.relativedelta import relativedelta
from datetime import datetime

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.addons.fleet.models.fleet_vehicle_model import FUEL_TYPES


#Some fields don't have the exact same name
MODEL_FIELDS_TO_VEHICLE = {
    'transmission': 'transmission', 'model_year': 'model_year', 'electric_assistance': 'electric_assistance',
    'color': 'color', 'seats': 'seats', 'doors': 'doors', 'trailer_hook': 'trailer_hook', 'default_co2': 'co2',
    'co2_standard': 'co2_standard', 'default_fuel_type': 'fuel_type', 'power': 'power', 'horsepower': 'horsepower',
    'horsepower_tax': 'horsepower_tax', 'category_id': 'category_id', 'vehicle_range': 'vehicle_range',
    'power_unit': 'power_unit', 'range_unit': 'range_unit',
}


class FleetVehicle(models.Model):
    _name = 'fleet.vehicle'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'avatar.mixin']
    _description = 'Vehicle'
    _order = 'license_plate asc, acquisition_date asc'
    _rec_names_search = ['name', 'driver_id.name']

    def _get_default_state(self):
        state = self.env.ref('fleet.fleet_vehicle_state_new_request', raise_if_not_found=False)
        return state if state and state.id else False

    def _get_year_selection(self):
        current_year = datetime.now().year
        return [(str(i), i) for i in range(1970, current_year + 1)]

    name = fields.Char(compute="_compute_vehicle_name", store=True)
    description = fields.Html("Vehicle Description")
    active = fields.Boolean('Active', default=True, tracking=True)
    manager_id = fields.Many2one(
        'res.users', 'Fleet Manager',
        domain=lambda self: [('all_group_ids', 'in', self.env.ref('fleet.fleet_group_manager').id), ('company_id', 'in', self.env.companies.ids)],
    )
    company_id = fields.Many2one(
        'res.company', 'Company',
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id')
    country_id = fields.Many2one('res.country', related='company_id.country_id')
    country_code = fields.Char(related='country_id.code', depends=['country_id'])
    license_plate = fields.Char(tracking=True,
        help='License plate number of the vehicle (i = plate number for a car)')
    vin_sn = fields.Char('Chassis Number', help='Unique number written on the vehicle motor (VIN/SN number)', tracking=True, copy=False)
    trailer_hook = fields.Boolean(default=False, string='Trailer Hitch',
        compute='_compute_trailer_hook', store=True, readonly=False,
        help="A trailer hitch is a device attached to a vehicle's chassis for towing purposes, \
            such as pulling trailers, boats, or other vehicles.")
    driver_id = fields.Many2one('res.partner', 'Driver', tracking=True, help='Driver address of the vehicle', copy=False)
    future_driver_id = fields.Many2one('res.partner', 'Future Driver', tracking=True, help='Next Driver Address of the vehicle', copy=False, check_company=True)
    model_id = fields.Many2one('fleet.vehicle.model', 'Model',
        tracking=True, required=True)
    brand_id = fields.Many2one('fleet.vehicle.model.brand', 'Brand', related="model_id.brand_id", store=True, readonly=False)
    log_drivers = fields.One2many('fleet.vehicle.assignation.log', 'vehicle_id', string='Assignment Logs')
    log_services = fields.One2many('fleet.vehicle.log.services', 'vehicle_id', 'Services Logs')
    log_contracts = fields.One2many('fleet.vehicle.log.contract', 'vehicle_id', 'Contracts')
    contract_count = fields.Integer(compute="_compute_count_all", string='Contract Count')
    service_count = fields.Integer(compute="_compute_count_all", string='Services')
    odometer_count = fields.Integer(compute="_compute_count_all", string='Odometer')
    history_count = fields.Integer(compute="_compute_count_all", string="Drivers History Count")
    next_assignation_date = fields.Date('Assignment Date', help='This is the date at which the car will be available, if not set it means available instantly')
    order_date = fields.Date('Order Date')
    acquisition_date = fields.Date('Registration Date', required=False,
        default=fields.Date.today, tracking=True,
        help='Date of vehicle registration')
    write_off_date = fields.Date('Cancellation Date', tracking=True, help="Date when the vehicle's license plate has been cancelled/removed.")
    contract_date_start = fields.Date(string="First Contract Date", default=fields.Date.today, tracking=True)
    color = fields.Char(help='Color of the vehicle', compute='_compute_color', store=True, readonly=False)
    state_id = fields.Many2one('fleet.vehicle.state', 'State',
        default=_get_default_state, group_expand='_read_group_expand_full',
        tracking=True,
        help='Current state of the vehicle', ondelete="set null")
    location = fields.Char(help='Location of the vehicle (garage, ...)')
    seats = fields.Integer('Seating Capacity', help='Number of seats of the vehicle',
        compute='_compute_seats', store=True, readonly=False)
    model_year = fields.Selection(selection='_get_year_selection', string='Model Year',
        help='Year of the model', compute='_compute_model_year', store=True, readonly=False)
    doors = fields.Integer('Number of Doors', help='Number of doors of the vehicle',
        compute='_compute_doors', store=True, readonly=False)
    tag_ids = fields.Many2many('fleet.vehicle.tag', 'fleet_vehicle_vehicle_tag_rel', 'vehicle_tag_id', 'tag_id', 'Tags', copy=False)
    odometer = fields.Float(compute='_get_odometer', inverse='_set_odometer', string='Last Odometer',
        help='Odometer measure of the vehicle at the moment of this log')
    odometer_unit = fields.Selection([
        ('kilometers', 'km'),
        ('miles', 'mi')
        ], 'Odometer Unit', default='kilometers', required=True)
    transmission = fields.Selection(
        [('manual', 'Manual'), ('automatic', 'Automatic')], 'Transmission',
        compute='_compute_transmission', store=True, readonly=False)
    fuel_type = fields.Selection(FUEL_TYPES, 'Fuel Type', compute='_compute_fuel_type', store=True, readonly=False)
    power_unit = fields.Selection([
        ('power', 'kW'),
        ('horsepower', 'Horsepower')
        ], 'Power Unit', default='power', required=True)
    horsepower = fields.Float(compute='_compute_horsepower', store=True, readonly=False)
    horsepower_tax = fields.Float('Horsepower Taxation', compute='_compute_horsepower_tax', store=True, readonly=False)
    power = fields.Float('Power', help='Power in kW of the vehicle',
        compute='_compute_power', store=True, readonly=False)
    co2 = fields.Float('CO₂ Emissions', help='CO2 emissions of the vehicle', compute='_compute_co2',
        store=True, readonly=False, tracking=True, aggregator=None)
    co2_emission_unit = fields.Selection([('g/km', 'g/km'), ('g/mi', 'g/mi')], compute='_compute_co2_emission_unit',
        store=True, default="g/km", required=True)
    co2_standard = fields.Char('Emission Standard', compute='_compute_co2_standard', store=True, readonly=False,
        help="Emission Standard specifies the regulatory test procedure \
            or guideline under which a vehicle's emissions are measured.")
    category_id = fields.Many2one('fleet.vehicle.model.category', 'Category', compute='_compute_category', store=True, readonly=False)
    image_128 = fields.Image(related='model_id.image_128', readonly=True)
    contract_renewal_due_soon = fields.Boolean(compute='_compute_contract_reminder', search='_search_contract_renewal_due_soon',
        string='Has Contracts to renew')
    contract_renewal_overdue = fields.Boolean(compute='_compute_contract_reminder', search='_search_get_overdue_contract_reminder',
        string='Has Contracts Overdue')
    contract_state = fields.Selection(
        [('futur', 'Incoming'),
         ('open', 'In Progress'),
         ('expired', 'Expired'),
         ('closed', 'Closed')
        ], string='Last Contract State', compute='_compute_contract_reminder', required=False)
    car_value = fields.Float(string="Catalog Value (VAT Incl.)", tracking=True)
    net_car_value = fields.Float(string="Purchase Value")
    residual_value = fields.Float()
    plan_to_change_car = fields.Boolean(tracking=True)
    plan_to_change_bike = fields.Boolean(tracking=True)
    vehicle_type = fields.Selection(related='model_id.vehicle_type')
    frame_type = fields.Selection([('diamant', 'Diamant'), ('trapez', 'Trapez'), ('wave', 'Wave')], string="Bike Frame Type")
    electric_assistance = fields.Boolean(compute='_compute_electric_assistance', store=True, readonly=False)
    frame_size = fields.Float()
    service_activity = fields.Selection([
        ('none', 'None'),
        ('overdue', 'Overdue'),
        ('today', 'Today'),
    ], compute='_compute_service_activity')
    vehicle_properties = fields.Properties('Properties', definition='model_id.vehicle_properties_definition', copy=True)
    vehicle_range = fields.Integer(string="Range")
    range_unit = fields.Selection([('km', 'km'), ('mi', 'mi')],
        compute='_compute_range_unit', store=True, readonly=False, default="km", required=True)

    @api.depends('log_services')
    def _compute_service_activity(self):
        for vehicle in self:
            activities_state = set(state for state in vehicle.log_services.mapped('activity_state') if state and state != 'planned')
            vehicle.service_activity = sorted(activities_state)[0] if activities_state else 'none'

    def _load_fields_from_model(self, fields_to_load):
        '''
        Copies the desired fields from the models to the vehicles
        '''
        model_values = dict()
        for vehicle in self.filtered('model_id'):
            if vehicle.model_id.id in model_values:
                write_vals = model_values[vehicle.model_id.id]
            else:
                # Update only the desired fields from the model, only when the model has a truthy value.
                write_vals = \
                    {
                        vehicle_field: vehicle.model_id[model_field] for model_field, vehicle_field in MODEL_FIELDS_TO_VEHICLE.items()
                        if vehicle_field in fields_to_load and vehicle.model_id[model_field]
                    }
                model_values[vehicle.model_id.id] = write_vals
            vehicle.update(write_vals)

    @api.depends('model_id')
    def _compute_category(self):
        self._load_fields_from_model(['category_id'])

    @api.depends('model_id')
    def _compute_range_unit(self):
        self._load_fields_from_model(['range_unit'])

    @api.depends('model_id')
    def _compute_trailer_hook(self):
        self._load_fields_from_model(['trailer_hook'])

    @api.depends('model_id')
    def _compute_vehicle_range(self):
        self._load_fields_from_model(['vehicle_range'])

    @api.depends('model_id')
    def _compute_electric_assistance(self):
        self._load_fields_from_model(['electric_assistance'])

    @api.depends('model_id')
    def _compute_co2_standard(self):
        self._load_fields_from_model(['co2_standard'])

    @api.depends('model_id')
    def _compute_co2(self):
        self._load_fields_from_model(['co2'])

    @api.depends('model_id')
    def _compute_power(self):
        self._load_fields_from_model(['power'])

    @api.depends('model_id')
    def _compute_horsepower(self):
        self._load_fields_from_model(['horsepower'])

    @api.depends('model_id')
    def _compute_horsepower_tax(self):
        self._load_fields_from_model(['horsepower_tax'])

    @api.depends('model_id')
    def _compute_fuel_type(self):
        self._load_fields_from_model(['fuel_type'])

    @api.depends('model_id')
    def _compute_transmission(self):
        self._load_fields_from_model(['transmission'])

    @api.depends('model_id')
    def _compute_doors(self):
        self._load_fields_from_model(['doors'])

    @api.depends('model_id')
    def _compute_model_year(self):
        self._load_fields_from_model(['model_year'])

    @api.depends('model_id')
    def _compute_seats(self):
        self._load_fields_from_model(['seats'])

    @api.depends('model_id')
    def _compute_color(self):
        self._load_fields_from_model(['color'])

    @api.depends('model_id.brand_id.name', 'model_id.name', 'license_plate')
    def _compute_vehicle_name(self):
        for record in self:
            record.name = (record.model_id.brand_id.name or '') + '/' + (record.model_id.name or '') + '/' + (record.license_plate or _('No Plate'))

    @api.depends('range_unit')
    def _compute_co2_emission_unit(self):
        for record in self:
            if record.range_unit == 'km':
                record.co2_emission_unit = 'g/km'
            else:
                record.co2_emission_unit = 'g/mi'

    def _get_odometer(self):
        FleetVehicalOdometer = self.env['fleet.vehicle.odometer']
        for record in self:
            vehicle_odometer = FleetVehicalOdometer.search([('vehicle_id', 'in', record.ids)], limit=1, order='value desc')
            if vehicle_odometer:
                record.odometer = vehicle_odometer.value
            else:
                record.odometer = 0

    def _set_odometer(self):
        self.env['fleet.vehicle.odometer'].create([
            {
                'value': vehicle.odometer,
                'date': fields.Date.context_today(vehicle),
                'vehicle_id': vehicle.id,
                'driver_id': vehicle.driver_id.id
            } for vehicle in self if vehicle.odometer
        ])

    def _compute_count_all(self):
        Odometer = self.env['fleet.vehicle.odometer']
        LogService = self.env['fleet.vehicle.log.services'].with_context(active_test=False)
        LogContract = self.env['fleet.vehicle.log.contract'].with_context(active_test=False)
        History = self.env['fleet.vehicle.assignation.log']
        odometers_data = Odometer._read_group([('vehicle_id', 'in', self.ids)], ['vehicle_id'], ['__count'])
        services_data = LogService._read_group([('vehicle_id', 'in', self.ids)], ['vehicle_id', 'active'], ['__count'])
        logs_data = LogContract._read_group([('vehicle_id', 'in', self.ids), ('state', '!=', 'closed')], ['vehicle_id', 'active'], ['__count'])
        histories_data = History._read_group([('vehicle_id', 'in', self.ids)], ['vehicle_id'], ['__count'])

        mapped_odometer_data = defaultdict(lambda: 0)
        mapped_service_data = defaultdict(lambda: defaultdict(lambda: 0))
        mapped_log_data = defaultdict(lambda: defaultdict(lambda: 0))
        mapped_history_data = defaultdict(lambda: 0)

        for vehicle, count in odometers_data:
            mapped_odometer_data[vehicle.id] = count
        for vehicle, active, count in services_data:
            mapped_service_data[vehicle.id][active] = count
        for vehicle, active, count in logs_data:
            mapped_log_data[vehicle.id][active] = count
        for vehicle, count in histories_data:
            mapped_history_data[vehicle.id] = count

        for vehicle in self:
            vehicle.odometer_count = mapped_odometer_data[vehicle.id]
            vehicle.service_count = mapped_service_data[vehicle.id][vehicle.active]
            vehicle.contract_count = mapped_log_data[vehicle.id][vehicle.active]
            vehicle.history_count = mapped_history_data[vehicle.id]

    @api.depends('log_contracts')
    def _compute_contract_reminder(self):
        params = self.env['ir.config_parameter'].sudo()
        delay_alert_contract = int(params.get_param('hr_fleet.delay_alert_contract', default=30))
        current_date = fields.Date.context_today(self)
        data = self.env['fleet.vehicle.log.contract']._read_group(
            domain=[('expiration_date', '!=', False), ('vehicle_id', 'in', self.ids), ('state', '!=', 'closed')],
            groupby=['vehicle_id', 'state'],
            aggregates=['expiration_date:max'])

        prepared_data = {}
        for vehicle_id, state, expiration_date in data:
            if prepared_data.get(vehicle_id.id):
                if prepared_data[vehicle_id.id]['expiration_date'] < expiration_date:
                    prepared_data[vehicle_id.id]['expiration_date'] = expiration_date
                    prepared_data[vehicle_id.id]['state'] = state
            else:
                prepared_data[vehicle_id.id] = {
                    'state': state,
                    'expiration_date': expiration_date,
                }

        for record in self:
            vehicle_data = prepared_data.get(record.id)
            if vehicle_data:
                diff_time = (vehicle_data['expiration_date'] - current_date).days
                record.contract_renewal_overdue = diff_time < 0
                record.contract_renewal_due_soon = not record.contract_renewal_overdue and (diff_time < delay_alert_contract)
                record.contract_state = vehicle_data['state']
            else:
                record.contract_renewal_overdue = False
                record.contract_renewal_due_soon = False
                record.contract_state = ""

    def _get_analytic_name(self):
        # This function is used in fleet_account and is overrided in l10n_be_hr_payroll_fleet
        return self.license_plate or _('No plate')

    def _search_contract_renewal_due_soon(self, operator, value):
        if operator != 'in':
            return NotImplemented
        params = self.env['ir.config_parameter'].sudo()
        delay_alert_contract = int(params.get_param('hr_fleet.delay_alert_contract', default=30))
        today = fields.Date.context_today(self)
        datetime_today = fields.Datetime.from_string(today)
        limit_date = fields.Datetime.to_string(datetime_today + relativedelta(days=+delay_alert_contract))
        return [('log_contracts', 'any', [
            ('expiration_date', '>', today),
            ('expiration_date', '<', limit_date),
            ('state', 'in', ['open', 'expired']),
        ])]

    def _search_get_overdue_contract_reminder(self, operator, value):
        if operator != 'in':
            return NotImplemented
        today = fields.Date.context_today(self)
        # get the id of vehicles that have overdue contracts
        # but exclude those for which a new contract has already been created for them
        return [
            ("log_contracts", "any", [
                ('expiration_date', '!=', False),
                ('expiration_date', '<', today),
                ('state', 'in', ['open', 'expired'])
            ]),
            "!",
                ("log_contracts", "any", [
                    ('expiration_date', '!=', False),
                    ('expiration_date', '>=', today),
                    ('state', 'in', ['open', 'futur'])
                ]),
        ]

    @api.model_create_multi
    def create(self, vals_list):
        vehicles = super().create(vals_list)
        to_update_drivers_cars = set()
        to_update_drivers_bikes = set()
        state_waiting_list = self.env.ref('fleet.fleet_vehicle_state_waiting_list', raise_if_not_found=False)
        for vehicle, vals in zip(vehicles, vals_list):
            if vals.get('driver_id'):
                vehicle.create_driver_history(vals)
            if vals.get('future_driver_id'):
                state_id = vehicle.state_id.id
                if not state_waiting_list or state_waiting_list.id != state_id:
                    future_driver = vals['future_driver_id']
                    if vehicle.vehicle_type == 'bike':
                        to_update_drivers_bikes.add(future_driver)
                    elif vehicle.vehicle_type == 'car':
                        to_update_drivers_cars.add(future_driver)
        if to_update_drivers_cars:
            self.search([
                ('driver_id', 'in', to_update_drivers_cars),
                ('vehicle_type', '=', 'car'),
            ]).plan_to_change_car = True
        if to_update_drivers_bikes:
            self.search([
                ('driver_id', 'in', to_update_drivers_bikes),
                ('vehicle_type', '=', 'bike'),
            ]).plan_to_change_bike = True
        return vehicles

    def write(self, vals):
        if 'odometer' in vals and any(vehicle.odometer > vals['odometer'] for vehicle in self):
            raise UserError(_('The odometer value cannot be lower than the previous one.'))

        if 'driver_id' in vals and vals['driver_id']:
            driver_id = vals['driver_id']
            for vehicle in self.filtered(lambda v: v.driver_id.id != driver_id):
                vehicle.create_driver_history(vals)
                if vehicle.driver_id:
                    vehicle.activity_schedule(
                        'mail.mail_activity_data_todo',
                        user_id=vehicle.manager_id.id or self.env.user.id,
                        note=_('Specify the End date of %s', vehicle.driver_id.name))

        if 'future_driver_id' in vals and vals['future_driver_id']:
            future_driver = vals['future_driver_id']
            state_waiting_list = self.env.ref('fleet.fleet_vehicle_state_waiting_list', raise_if_not_found=False)
            vehicle_types = set(self.filtered(lambda vehicle: not state_waiting_list or\
                                state_waiting_list.id != vals.get('state_id', vehicle.state_id.id)).mapped('vehicle_type'))
            if vehicle_types:
                vehicle_read_group = dict(self.env['fleet.vehicle']._read_group(
                    domain=[('driver_id', '=', future_driver), ('vehicle_type', 'in', vehicle_types)],
                    groupby=['vehicle_type'],
                    aggregates=['id:recordset'])
                )
                if 'bike' in vehicle_read_group:
                    vehicle_read_group['bike'].write({'plan_to_change_bike': True})
                if 'car' in vehicle_read_group:
                    vehicle_read_group['car'].write({'plan_to_change_car': True})

        if 'active' in vals and not vals['active']:
            self.env['fleet.vehicle.log.contract'].search([('vehicle_id', 'in', self.ids)]).active = False
            self.env['fleet.vehicle.log.services'].search([('vehicle_id', 'in', self.ids)]).active = False

        res = super(FleetVehicle, self).write(vals)
        return res

    def _get_driver_history_data(self, vals):
        self.ensure_one()
        return {
            'vehicle_id': self.id,
            'driver_id': vals['driver_id'],
            'date_start': fields.Date.today(),
        }

    def create_driver_history(self, vals):
        for vehicle in self:
            self.env['fleet.vehicle.assignation.log'].create(
                vehicle._get_driver_history_data(vals),
            )

    def action_accept_driver_change(self):
        # Find all the vehicles of the same type for which the driver is the future_driver_id
        # remove their driver_id and close their history using current date
        vehicles = self.search([('driver_id', 'in', self.mapped('future_driver_id').ids), ('vehicle_type', '=', self.vehicle_type)])
        vehicles.write({
            'driver_id': False,
            'plan_to_change_car': False,
            'plan_to_change_bike': False,
        })
        
        for vehicle in self:
            vehicle.plan_to_change_bike = False
            vehicle.plan_to_change_car = False
            vehicle.driver_id = vehicle.future_driver_id
            vehicle.future_driver_id = False

    def return_action_to_open(self):
        """ This opens the xml view specified in xml_id for the current vehicle """
        self.ensure_one()
        xml_id = self.env.context.get('xml_id')
        if xml_id:

            res = self.env['ir.actions.act_window']._for_xml_id('fleet.%s' % xml_id)
            res.update(
                context=dict(self.env.context, default_vehicle_id=self.id, group_by=False),
                domain=[('vehicle_id', '=', self.id)]
            )
            return res
        return False

    def act_show_log_cost(self):
        """ This opens log view to view and add new log for this vehicle, groupby default to only show effective costs
            @return: the costs log view
        """
        self.ensure_one()
        copy_context = dict(self.env.context)
        copy_context.pop('group_by', None)
        res = self.env['ir.actions.act_window']._for_xml_id('fleet.fleet_vehicle_costs_action')
        res.update(
            context=dict(copy_context, default_vehicle_id=self.id, search_default_parent_false=True),
            domain=[('vehicle_id', '=', self.id)]
        )
        return res

    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'driver_id' in init_values or 'future_driver_id' in init_values:
            return self.env.ref('fleet.mt_fleet_driver_updated')
        return super(FleetVehicle, self)._track_subtype(init_values)

    def open_assignation_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Assignment Logs',
            'view_mode': 'list',
            'res_model': 'fleet.vehicle.assignation.log',
            'domain': [('vehicle_id', '=', self.id)],
            'context': {'default_driver_id': self.driver_id.id, 'default_vehicle_id': self.id}
        }

    def action_send_email(self):
        return {
            'name': _('Send Email'),
            'type': 'ir.actions.act_window',
            'target': 'new',
            'view_mode': 'form',
            'res_model': 'fleet.vehicle.send.mail',
            'context': {
                'default_vehicle_ids': self.ids,
            }
        }

    def action_open_odometer_report(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id('fleet.fleet_vehicle_odometer_reporting_action')
        action.update({
            'domain': [('vehicle_id', '=', self.id)],
            'context': {'search_default_groupby_date': True},
        })
        return action
