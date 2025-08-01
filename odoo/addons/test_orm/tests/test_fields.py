import base64
import io
import threading
from collections import OrderedDict
from datetime import date, datetime
from unittest.mock import patch

import psycopg2
from PIL import Image

from odoo import Command, fields, models
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.fields import Domain
from odoo.tests import Form, TransactionCase, tagged, users
from odoo.tools import float_repr, mute_logger
from odoo.tools.image import image_data_uri

from odoo.addons.base.tests.common import TransactionCaseWithUserDemo
from odoo.addons.base.tests.test_expression import TransactionExpressionCase


class TestFields(TransactionCaseWithUserDemo, TransactionExpressionCase):

    def setUp(self):
        # for tests methods that create custom models/fields
        self.addCleanup(self.registry.reset_changes)
        self.addCleanup(self.registry.clear_all_caches)
        super().setUp()
        self.env.ref('test_orm.discussion_0').write({'participants': [Command.link(self.user_demo.id)]})
        # YTI FIX ME: The cache shouldn't be inconsistent (rco is gonna fix it)
        # self.env.ref('test_orm.discussion_0').participants -> 1 user
        # self.env.ref('test_orm.discussion_0').invalidate()
        # self.env.ref('test_orm.discussion_0').with_context(active_test=False).participants -> 2 users
        self.env.ref('test_orm.message_0_1').write({'author': self.user_demo.id})

    def test_00_basics(self):
        """ test accessing new fields """
        # find a discussion
        discussion = self.env.ref('test_orm.discussion_0')

        # read field as a record attribute or as a record item
        self.assertIsInstance(discussion.name, str)
        self.assertIsInstance(discussion['name'], str)
        self.assertEqual(discussion['name'], discussion.name)

        # read it with method read()
        values = discussion.read(['name'])[0]
        self.assertEqual(values['name'], discussion.name)

    def test_01_basic_get_assertion(self):
        """ test item getter """
        # field access works on single record
        record = self.env.ref('test_orm.message_0_0')
        self.assertEqual(len(record), 1)
        ok = record.body  # noqa: F841

        # field access fails on multiple records
        records = self.env['test_orm.message'].search([])
        assert len(records) > 1
        with self.assertRaises(ValueError):
            faulty = records.body  # noqa: F841

    def test_01_basic_set_assertion(self):
        """ test item setter """
        # field assignment works on single record
        record = self.env.ref('test_orm.message_0_0')
        self.assertEqual(len(record), 1)
        record.body = 'OK'

        # field assignment on multiple records should assign value to all records
        records = self.env['test_orm.message'].search([])
        records.body = 'Updated'
        self.assertTrue(all(map(lambda record: record.body == 'Updated', records)))  # noqa: C417

        # field assigmenent does not cache the wrong value when write overridden
        record.priority = 4
        self.assertEqual(record.priority, 5)

    def test_05_unknown_fields(self):
        """ test ORM operations with unknown fields """
        cat = self.env['test_orm.category'].create({'name': 'Foo'})

        with self.assertRaisesRegex(ValueError, 'Invalid field'):
            cat.search([('zzz', '=', 42)])
        with self.assertRaisesRegex(ValueError, 'Invalid field'):
            cat.search([], order='zzz')

        with self.assertRaisesRegex(ValueError, 'Invalid field'):
            cat.read(['zzz'])

        with self.assertRaisesRegex(ValueError, 'Invalid field'):
            cat.create({'name': 'Foo', 'zzz': 42})

        with self.assertRaisesRegex(ValueError, 'Invalid field'):
            cat.write({'zzz': 42})

        with self.assertRaisesRegex(ValueError, 'Invalid field'):
            cat.new({'name': 'Foo', 'zzz': 42})

    def test_10_computed(self):
        """ check definition of computed fields """
        # by default function fields are not stored, readonly, not copied
        field = self.env['test_orm.message']._fields['size']
        self.assertFalse(field.store)
        self.assertFalse(field.compute_sudo)
        self.assertTrue(field.readonly)
        self.assertFalse(field.copy)

        field = self.env['test_orm.message']._fields['name']
        self.assertTrue(field.store)
        self.assertTrue(field.compute_sudo)
        self.assertTrue(field.readonly)
        self.assertFalse(field.copy)

        # stored editable computed fields are copied according to their type
        field = self.env['test_orm.compute.onchange']._fields['baz']
        self.assertTrue(field.store)
        self.assertTrue(field.compute_sudo)
        self.assertFalse(field.readonly)
        self.assertTrue(field.copy)

        field = self.env['test_orm.compute.onchange']._fields['line_ids']
        self.assertTrue(field.store)
        self.assertTrue(field.compute_sudo)
        self.assertFalse(field.readonly)
        self.assertFalse(field.copy)  # like a regular one2many field

        field = self.env['test_orm.compute.onchange']._fields['tag_ids']
        self.assertTrue(field.store)
        self.assertTrue(field.compute_sudo)
        self.assertFalse(field.readonly)
        self.assertTrue(field.copy)  # like a regular many2many field

    def test_10_computed_custom(self):
        """ check definition of custom computed fields """
        # Flush demo user before creating a new ir.model.fields to avoid
        # a deadlock
        self.env.flush_all()
        self.env['ir.model.fields'].create({
            'name': 'x_bool_false_computed',
            'model_id': self.env.ref('test_orm.model_test_orm_message').id,
            'field_description': 'A boolean computed to false',
            'compute': "for r in self: r['x_bool_false_computed'] = False",
            'store': False,
            'ttype': 'boolean',
        })
        field = self.env['test_orm.message']._fields['x_bool_false_computed']
        self.assertFalse(self.registry.field_depends[field])

    def test_10_computed_custom_invalid_transitive_depends(self):
        self.patch(type(self.env["ir.model.fields"]), "_check_depends", lambda self: True)
        self.env["ir.model.fields"].create(
            {
                "name": "x_computed_custom_valid_depends",
                "model_id": self.env.ref("test_orm.model_test_orm_foo").id,
                "state": "manual",
                "field_description": "A compute with a valid depends",
                "compute": "for r in self: r['x_computed_custom_valid_depends'] = False",
                "depends": "value1",
                "store": False,
                "ttype": "boolean",
            },
        )
        self.env["ir.model.fields"].create(
            {
                "name": "x_computed_custom_valid_transitive_depends",
                "model_id": self.env.ref("test_orm.model_test_orm_foo").id,
                "state": "manual",
                "field_description": "A compute with a valid transitive depends",
                "compute": "for r in self: r['x_computed_custom_valid_transitive_depends'] = False",
                "depends": "x_computed_custom_valid_depends",
                "store": False,
                "ttype": "boolean",
            },
        )
        self.env["ir.model.fields"].create(
            {
                "name": "x_computed_custom_invalid_depends",
                "model_id": self.env.ref("test_orm.model_test_orm_foo").id,
                "state": "manual",
                "field_description": "A compute with an invalid depends",
                "compute": "for r in self: r['x_computed_custom_invalid_depends'] = False",
                "depends": "bar",
                "store": False,
                "ttype": "boolean",
            },
        )
        self.env["ir.model.fields"].create(
            {
                "name": "x_computed_custom_invalid_transitive_depends",
                "model_id": self.env.ref("test_orm.model_test_orm_foo").id,
                "state": "manual",
                "field_description": "A compute with an invalid transitive depends",
                "compute": "for r in self: r['x_computed_custom_invalid_transitive_depends'] = False",
                "depends": "x_computed_custom_invalid_depends",
                "store": False,
                "ttype": "boolean",
            },
        )
        fields = self.env["test_orm.foo"]._fields
        get_trigger_tree = self.registry.get_trigger_tree
        value1 = fields["value1"]
        valid_depends = fields["x_computed_custom_valid_depends"]
        valid_transitive_depends = fields["x_computed_custom_valid_transitive_depends"]
        invalid_depends = fields["x_computed_custom_invalid_depends"]
        invalid_transitive_depends = fields["x_computed_custom_invalid_transitive_depends"]
        # `x_computed_custom_valid_depends` in the triggers of the field `value1`
        self.assertTrue(valid_depends in get_trigger_tree([value1]).root)
        # `x_computed_custom_valid_transitive_depends` in the triggers `x_computed_custom_valid_depends` and `value1`
        self.assertTrue(valid_transitive_depends in get_trigger_tree([valid_depends]).root)
        self.assertTrue(valid_transitive_depends in get_trigger_tree([value1]).root)
        # `x_computed_custom_invalid_depends` not in any triggers, as it was invalid and was skipped
        self.assertEqual(
            sum(invalid_depends in get_trigger_tree([field]).root for field in fields.values()), 0,
        )
        # `x_computed_custom_invalid_transitive_depends` in the triggers of `x_computed_custom_invalid_depends` only
        self.assertTrue(invalid_transitive_depends in get_trigger_tree([invalid_depends]).root)
        self.assertEqual(
            sum(invalid_transitive_depends in get_trigger_tree([field]).root for field in fields.values()), 1,
        )

    @mute_logger('odoo.fields')
    def test_10_computed_stored_x_name(self):
        # create a custom model with two fields
        self.env["ir.model"].create({
            "name": "x_test_10_compute_store_x_name",
            "model": "x_test_10_compute_store_x_name",
            "field_id": [
                (0, 0, {'name': 'x_name', 'ttype': 'char'}),
                (0, 0, {'name': 'x_stuff_id', 'ttype': 'many2one', 'relation': 'ir.model'}),
            ],
        })
        self.env.invalidate_all()
        # set 'x_stuff_id' refer to a model not loaded yet
        self.cr.execute("""
            UPDATE ir_model_fields
            SET relation = 'not.loaded'
            WHERE model = 'x_test_10_compute_store_x_name' AND name = 'x_stuff_id'
        """)
        # set 'x_name' be computed and depend on 'x_stuff_id'
        self.cr.execute("""
            UPDATE ir_model_fields
            SET compute = 'pass', depends = 'x_stuff_id.x_custom_1'
            WHERE model = 'x_test_10_compute_store_x_name' AND name = 'x_name'
        """)
        # setting up models should not crash
        self.registry._setup_models__(self.cr, ['x_test_10_compute_store_x_name'])

    def test_10_context_dependent_related(self):
        self.env['res.lang']._activate_lang('fr_FR')

        container = self.env['test_orm.compute.container'].create({'name': 'test', 'name_translated': 'test_en'})
        container.with_context(lang='fr_FR').name_translated = 'test_fr'

        member_root = self.env['test_orm.compute.member'].create({'name': 'test'})
        member_user = member_root.with_user(self.user_demo)

        # computed field container_context_id depends on env.uid
        self.assertEqual(member_user.container_context_id, container)
        self.assertFalse(member_root.container_context_id)

        # related field container_context_name also depends on env.uid
        self.assertEqual(member_user.container_context_name, 'test')
        self.assertFalse(member_root.container_context_name)

        # related field container_context_name_translated depends on env.uid and lang
        self.assertEqual(member_user.container_context_name_translated, 'test_en')
        self.assertFalse(member_root.container_context_name_translated)
        self.assertEqual(member_user.with_context(lang='fr_FR').container_context_name_translated, 'test_fr')
        self.assertFalse(member_root.with_context(lang='fr_FR').container_context_name_translated)

        member_user.sudo().update_field_translations('container_context_name_translated', {'fr_FR': 'test_fr_new'})
        self.assertEqual(member_user.container_context_name_translated, 'test_en')
        self.assertFalse(member_root.container_context_name_translated)
        self.assertEqual(member_user.with_context(lang='fr_FR').container_context_name_translated, 'test_fr_new')
        self.assertFalse(member_root.with_context(lang='fr_FR').container_context_name_translated)

    def test_10_display_name(self):
        """ test definition of automatic field 'display_name' """
        field = self.env.registry['test_orm.discussion'].display_name
        self.assertTrue(field.compute)
        self.assertEqual(self.registry.field_depends[field], ('name',))

    def test_10_non_stored(self):
        """ test non-stored fields """
        # a field declared with store=False should not have a column
        field = self.env['test_orm.category']._fields['dummy']
        self.assertFalse(field.store)
        self.assertFalse(field.compute)
        self.assertFalse(field.inverse)

        # find messages
        for message in self.env['test_orm.message'].search([]):
            # check definition of field
            self.assertEqual(message.size, len(message.body or ''))

            # check recomputation after record is modified
            size = message.size
            message.write({'body': (message.body or '') + "!!!"})
            self.assertEqual(message.size, size + 3)

        # create a message, assign body, and check size in several environments
        message1 = self.env['test_orm.message'].create({})
        message2 = message1.with_user(self.user_demo)
        self.assertEqual(message1.size, 0)
        self.assertEqual(message2.size, 0)

        message1.write({'body': "XXX"})
        self.assertEqual(message1.size, 3)
        self.assertEqual(message2.size, 3)

        # special case: computed field without dependency must be computed
        record = self.env['test_orm.mixed'].create({})
        self.assertTrue(record.now)

    def test_11_stored(self):
        """ test stored fields """
        def check_stored(disc):
            """ Check the stored computed field on disc.messages """
            for msg in disc.messages:
                self.assertEqual(msg.name, "[%s] %s" % (disc.name, msg.author.name))

        # find the demo discussion, and check messages
        discussion1 = self.env.ref('test_orm.discussion_0')
        self.assertTrue(discussion1.messages)
        check_stored(discussion1)

        # modify discussion name, and check again messages
        discussion1.name = 'Talking about stuff...'
        check_stored(discussion1)

        # switch message from discussion, and check again

        # See YTI FIXME
        self.env.invalidate_all()

        discussion2 = discussion1.copy({'name': 'Another discussion'})
        message2 = discussion1.messages[0]
        message2.discussion = discussion2
        check_stored(discussion2)

        # create a new discussion with messages, and check their name
        user_root = self.env.ref('base.user_root')
        user_demo = self.user_demo
        discussion3 = self.env['test_orm.discussion'].create({
            'name': 'Stuff',
            'participants': [Command.link(user_root.id), Command.link(user_demo.id)],
            'messages': [
                Command.create({'author': user_root.id, 'body': 'one'}),
                Command.create({'author': user_demo.id, 'body': 'two'}),
                Command.create({'author': user_root.id, 'body': 'three'}),
            ],
        })
        check_stored(discussion3)

        # modify the discussion messages: edit the 2nd one, remove the last one
        # (keep modifications in that order, as they reproduce a former bug!)
        discussion3.write({
            'messages': [
                Command.link(discussion3.messages[0].id),
                Command.update(discussion3.messages[1].id, {'author': user_root.id}),
                Command.delete(discussion3.messages[2].id),
            ],
        })
        check_stored(discussion3)

    def test_11_stored_protected(self):
        """ test protection against recomputation """
        model = self.env['test_orm.compute.readonly']
        field = model._fields['bar']

        record = model.create({'foo': 'unprotected #1'})
        self.assertEqual(record.bar, 'unprotected #1')

        record.write({'foo': 'unprotected #2'})
        self.assertEqual(record.bar, 'unprotected #2')

        # by protecting 'bar', we prevent it from being recomputed
        with self.env.protecting([field], record):
            record.write({'foo': 'protected'})
            self.assertEqual(record.bar, 'unprotected #2')

            # also works when nested
            with self.env.protecting([field], record):
                record.write({'foo': 'protected'})
                self.assertEqual(record.bar, 'unprotected #2')

            record.write({'foo': 'protected'})
            self.assertEqual(record.bar, 'unprotected #2')

        record.write({'foo': 'unprotected #3'})
        self.assertEqual(record.bar, 'unprotected #3')

        # also works with duplicated fields
        with self.env.protecting([field, field], record):
            record.write({'foo': 'protected'})
            self.assertEqual(record.bar, 'unprotected #3')

        record.write({'foo': 'unprotected #4'})
        self.assertEqual(record.bar, 'unprotected #4')

        # we protect 'bar' on a different record
        with self.env.protecting([field], record):
            record2 = model.create({'foo': 'unprotected'})
            self.assertEqual(record2.bar, 'unprotected')

    def test_11_computed_access(self):
        """ test computed fields with access right errors """
        User = self.env['res.users']
        user1 = User.create({'name': 'Aaaah', 'login': 'a'})
        user2 = User.create({'name': 'Boooh', 'login': 'b'})
        user3 = User.create({'name': 'Crrrr', 'login': 'c'})
        # add a rule to not give access to user2
        self.env['ir.rule'].create({
            'model_id': self.env['ir.model'].search([('model', '=', 'res.users')]).id,
            'domain_force': "[('id', '!=', %d)]" % user2.id,
        })
        # DLE P72: Since we decided that we do not raise security access errors for data to which we had the occassion
        # to put the value in the cache, we need to invalidate the cache for user1, user2 and user3 in order
        # to test the below access error. Otherwise the above create calls set in the cache the information needed
        # to compute `company_type` ('is_company'), and doesn't need to trigger a read.
        # We need to force the read in order to test the security access
        self.env.invalidate_all()
        # group users as a recordset, and read them as user demo
        users = (user1 + user2 + user3).with_user(self.user_demo)
        user1, user2, user3 = users
        # regression test: a bug invalidated the field's value from cache
        user1.company_type
        with self.assertRaises(AccessError):
            user2.company_type
        user3.company_type

    def test_12_recursive(self):
        """ test recursively dependent fields """
        Category = self.env['test_orm.category']
        abel = Category.create({'name': 'Abel'})
        beth = Category.create({'name': 'Bethany'})
        cath = Category.create({'name': 'Catherine'})
        dean = Category.create({'name': 'Dean'})
        ewan = Category.create({'name': 'Ewan'})
        finn = Category.create({'name': 'Finnley'})
        gabe = Category.create({'name': 'Gabriel'})

        cath.parent = finn.parent = gabe
        abel.parent = beth.parent = cath
        dean.parent = ewan.parent = finn

        self.assertEqual(abel.display_name, "Gabriel / Catherine / Abel")
        self.assertEqual(beth.display_name, "Gabriel / Catherine / Bethany")
        self.assertEqual(cath.display_name, "Gabriel / Catherine")
        self.assertEqual(dean.display_name, "Gabriel / Finnley / Dean")
        self.assertEqual(ewan.display_name, "Gabriel / Finnley / Ewan")
        self.assertEqual(finn.display_name, "Gabriel / Finnley")
        self.assertEqual(gabe.display_name, "Gabriel")

        ewan.parent = cath
        self.assertEqual(ewan.display_name, "Gabriel / Catherine / Ewan")

        cath.parent = finn
        self.assertEqual(ewan.display_name, "Gabriel / Finnley / Catherine / Ewan")

    def test_12_recursive_recompute(self):
        """ test recomputation on recursively dependent field """
        a = self.env['test_orm.recursive'].create({'name': 'A'})
        b = self.env['test_orm.recursive'].create({'name': 'B', 'parent': a.id})
        c = self.env['test_orm.recursive'].create({'name': 'C', 'parent': b.id})
        d = self.env['test_orm.recursive'].create({'name': 'D', 'parent': c.id})
        self.assertEqual(a.full_name, 'A')
        self.assertEqual(b.full_name, 'A / B')
        self.assertEqual(c.full_name, 'A / B / C')
        self.assertEqual(d.full_name, 'A / B / C / D')
        self.assertEqual(a.display_name, 'A')
        self.assertEqual(b.display_name, 'A / B')
        self.assertEqual(c.display_name, 'A / B / C')
        self.assertEqual(d.display_name, 'A / B / C / D')

        a.name = 'A1'
        self.assertEqual(a.full_name, 'A1')
        self.assertEqual(b.full_name, 'A1 / B')
        self.assertEqual(c.full_name, 'A1 / B / C')
        self.assertEqual(d.full_name, 'A1 / B / C / D')
        self.assertEqual(a.display_name, 'A1')
        self.assertEqual(b.display_name, 'A1 / B')
        self.assertEqual(c.display_name, 'A1 / B / C')
        self.assertEqual(d.display_name, 'A1 / B / C / D')

        b.parent = False
        self.assertEqual(a.full_name, 'A1')
        self.assertEqual(b.full_name, 'B')
        self.assertEqual(c.full_name, 'B / C')
        self.assertEqual(d.full_name, 'B / C / D')
        self.assertEqual(a.display_name, 'A1')
        self.assertEqual(b.display_name, 'B')
        self.assertEqual(c.display_name, 'B / C')
        self.assertEqual(d.display_name, 'B / C / D')

        # rename several records to trigger several recomputations at once
        (d + c + b).write({'name': 'X'})
        self.assertEqual(a.full_name, 'A1')
        self.assertEqual(b.full_name, 'X')
        self.assertEqual(c.full_name, 'X / X')
        self.assertEqual(d.full_name, 'X / X / X')
        self.assertEqual(a.display_name, 'A1')
        self.assertEqual(b.display_name, 'X')
        self.assertEqual(c.display_name, 'X / X')
        self.assertEqual(d.display_name, 'X / X / X')

        # delete b; both c and d are deleted in cascade; c should also be marked
        # to recompute, but recomputation should not fail...
        b.unlink()
        self.assertEqual((a + b + c + d).exists(), a)

    def test_12_recursive_tree(self):
        foo = self.env['test_orm.recursive.tree'].create({'name': 'foo'})
        self.assertEqual(foo.display_name, 'foo()')
        bar = foo.create({'name': 'bar', 'parent_id': foo.id})
        self.assertEqual(foo.display_name, 'foo(bar())')
        baz = foo.create({'name': 'baz', 'parent_id': bar.id})  # noqa: F841
        self.assertEqual(foo.display_name, 'foo(bar(baz()))')

    def test_12_recursive_unlink(self):
        order = self.env['test_orm.recursive.order'].create({'value': 42})
        line = self.env['test_orm.recursive.line'].create({'order_id': order.id})
        task = self.env['test_orm.recursive.task'].create({'value': 42})
        self.assertEqual(task.line_id, line)
        self.assertEqual(line.task_ids, task)
        self.assertTrue(line.task_number)

        # Before deleting order, the following are marked to recompute:
        #  - task.line_id (recursive, depends on task.line_id.order_id.value)
        #  - line.task_number (implicitely depends on line.task_ids.line_id)
        #
        # If task.line_id is ever recomputed in order to mark line.task_number,
        # its recomputed value will be lost in the cache invalidation, and
        # there will be nothing left to write in the database afterwards!  This
        # makes the call to unlink() crash in that case.
        #
        order.unlink()

    def test_12_recursive_context_dependent(self):
        a = self.env['test_orm.recursive'].create({'name': 'A'})
        b = self.env['test_orm.recursive'].create({'name': 'B', 'parent': a.id})
        c = self.env['test_orm.recursive'].create({'name': 'C', 'parent': b.id})
        d = self.env['test_orm.recursive'].create({'name': 'D', 'parent': c.id})
        self.assertEqual(a.context_dependent_name, 'A')
        self.assertEqual(b.context_dependent_name, 'A / B')
        self.assertEqual(c.context_dependent_name, 'A / B / C')
        self.assertEqual(d.context_dependent_name, 'A / B / C / D')

        # now let's swith to another context to update the dependency
        a.with_context(bozo=42).name = 'A1'
        self.assertEqual(a.context_dependent_name, 'A1')
        self.assertEqual(b.context_dependent_name, 'A1 / B')
        self.assertEqual(c.context_dependent_name, 'A1 / B / C')
        self.assertEqual(d.context_dependent_name, 'A1 / B / C / D')

    def test_12_cascade(self):
        """ test computed field depending on computed field """
        message = self.env.ref('test_orm.message_0_0')
        self.env.invalidate_all()
        double_size = message.double_size
        self.assertEqual(double_size, message.size)

        record = self.env['test_orm.cascade'].create({'foo': "Hi"})
        self.assertEqual(record.baz, "<[Hi]>")
        record.foo = "Ho"
        self.assertEqual(record.baz, "<[Ho]>")

    def test_12_unlink_cascade_active_store(self):
        """ Test that `unlink` on many records doesn't raise a RecursionError
        with a stored related `active` field.
        """
        message = self.env['test_orm.message'].create({
            'active': False,
        })
        self.env['test_orm.emailmessage'].create(
            [{'message': message.id}] * 101,
        )
        message.unlink()

    def test_12_unlink_cascade_ir_rule_using_related(self):
        """ Test that `unlink` on many records doesn't raise a RecursionError
        when there is an ir.rule with a stored related field to compute.
        """
        message = self.env['test_orm.message'].create({
            'active': False,
        })
        self.env['test_orm.emailmessage'].create(
            [{'message': message.id}] * 101,
        )

        # Create an ir.rule, which forces to flush field 'active'
        self.env['ir.rule'].create({
            'model_id': self.env['ir.model']._get_id('test_orm.emailmessage'),
            'groups': [self.env.ref('base.group_user').id],
            'domain_force': str([('active', '=', False)]),
        })

        message.with_user(self.user_demo).unlink()

    def test_12_dynamic_depends(self):
        Model = self.registry['test_orm.compute.dynamic.depends']
        self.assertEqual(self.registry.field_depends[Model.full_name], ())

        # the dependencies of full_name are stored in a config parameter
        self.env['ir.config_parameter'].set_param('test_orm.full_name', 'name1,name2')

        # this must re-evaluate the field's dependencies
        self.env.flush_all()
        self.registry._setup_models__(self.cr, ['test_orm.compute.dynamic.depends'])
        self.assertEqual(self.registry.field_depends[Model.full_name], ('name1', 'name2'))

    def test_12_one2many_reference_domain(self):
        model = self.env['test_orm.inverse_m2o_ref']
        o2m_field = model._fields['model_ids']
        self.assertEqual(o2m_field.get_comodel_domain(model), Domain('const', '=', True) & Domain('res_model', '=', model._name))
        o2m_field = model._fields['model_computed_ids']
        self.assertEqual(o2m_field.get_comodel_domain(model), Domain.TRUE)

    def test_13_inverse(self):
        """ test inverse computation of fields """
        Category = self.env['test_orm.category']
        abel = Category.create({'name': 'Abel'})
        beth = Category.create({'name': 'Bethany'})
        cath = Category.create({'name': 'Catherine'})
        dean = Category.create({'name': 'Dean'})  # noqa: F841
        ewan = Category.create({'name': 'Ewan'})
        finn = Category.create({'name': 'Finnley'})  # noqa: F841
        gabe = Category.create({'name': 'Gabriel'})  # noqa: F841
        self.assertEqual(ewan.display_name, "Ewan")

        ewan.display_name = "Abel / Bethany / Catherine / Erwan"

        self.assertEqual(beth.parent, abel)
        self.assertEqual(cath.parent, beth)
        self.assertEqual(ewan.parent, cath)
        self.assertEqual(ewan.name, "Erwan")

        # check create/write with several records
        vals = {'name': 'None', 'display_name': 'Foo'}
        foo1, foo2 = Category.create([vals, vals])
        self.assertEqual(foo1.name, 'Foo')
        self.assertEqual(foo2.name, 'Foo')

        (foo1 + foo2).write({'display_name': 'Bar'})
        self.assertEqual(foo1.name, 'Bar')
        self.assertEqual(foo2.name, 'Bar')

        # create/write on 'foo' should only invoke the compute method
        log = []
        model = self.env['test_orm.compute.inverse'].with_context(log=log)
        record = model.create({'foo': 'Hi'})
        self.assertEqual(record.foo, 'Hi')
        self.assertEqual(record.bar, 'Hi')
        self.assertCountEqual(log, ['compute'])

        log.clear()
        record.write({'foo': 'Ho'})
        self.assertEqual(record.foo, 'Ho')
        self.assertEqual(record.bar, 'Ho')
        self.assertCountEqual(log, ['compute'])

        # create/write on 'bar' should only invoke the inverse method
        log.clear()
        record = model.create({'bar': 'Hi'})
        self.assertEqual(record.foo, 'Hi')
        self.assertEqual(record.bar, 'Hi')
        self.assertCountEqual(log, ['inverse'])

        log.clear()
        record.write({'bar': 'Ho'})
        self.assertEqual(record.foo, 'Ho')
        self.assertEqual(record.bar, 'Ho')
        self.assertCountEqual(log, ['inverse'])

        # Test compatibility multiple compute/inverse fields
        log = []
        model = self.env['test_orm.multi_compute_inverse'].with_context(log=log)
        record = model.create({
            'bar1': '1',
            'bar2': '2',
            'bar3': '3',
        })
        self.assertEqual(record.foo, '1/2/3')
        self.assertEqual(record.bar1, '1')
        self.assertEqual(record.bar2, '2')
        self.assertEqual(record.bar3, '3')
        self.assertCountEqual(log, ['inverse1', 'inverse23'])

        log.clear()
        record.write({'bar2': '4', 'bar3': '5'})
        self.assertEqual(record.foo, '1/4/5')
        self.assertEqual(record.bar1, '1')
        self.assertEqual(record.bar2, '4')
        self.assertEqual(record.bar3, '5')
        self.assertCountEqual(log, ['inverse23'])

        log.clear()
        record.write({'bar1': '6', 'bar2': '7'})
        self.assertEqual(record.foo, '6/7/5')
        self.assertEqual(record.bar1, '6')
        self.assertEqual(record.bar2, '7')
        self.assertEqual(record.bar3, '5')
        self.assertCountEqual(log, ['inverse1', 'inverse23'])

        log.clear()
        record.write({'foo': 'A/B/C'})
        self.assertEqual(record.foo, 'A/B/C')
        self.assertEqual(record.bar1, 'A')
        self.assertEqual(record.bar2, 'B')
        self.assertEqual(record.bar3, 'C')
        self.assertCountEqual(log, ['compute'])

        # corner case: write on a field that is marked to compute
        log.clear()
        # writing on 'foo' marks 'bar1', 'bar2', 'bar3' to compute
        record.write({'foo': '1/2/3'})
        self.assertCountEqual(log, [])
        # writing on 'bar3' must force the computation before updating
        record.write({'bar3': 'X'})
        self.assertCountEqual(log, ['compute', 'inverse23'])
        self.assertEqual(record.foo, '1/2/X')
        self.assertEqual(record.bar1, '1')
        self.assertEqual(record.bar2, '2')
        self.assertEqual(record.bar3, 'X')
        self.assertCountEqual(log, ['compute', 'inverse23'])

        log.clear()
        # writing on 'foo' marks 'bar1', 'bar2', 'bar3' to compute
        record.write({'foo': 'A/B/C'})
        self.assertCountEqual(log, [])
        # writing on 'bar1', 'bar2', 'bar3' discards the computation
        record.write({'bar1': 'X', 'bar2': 'Y', 'bar3': 'Z'})
        self.assertCountEqual(log, ['inverse1', 'inverse23'])
        self.assertEqual(record.foo, 'X/Y/Z')
        self.assertEqual(record.bar1, 'X')
        self.assertEqual(record.bar2, 'Y')
        self.assertEqual(record.bar3, 'Z')
        self.assertCountEqual(log, ['inverse1', 'inverse23'])

    def test_13_inverse_access(self):
        """ test access rights on inverse fields """
        foo = self.env['test_orm.category'].create({'name': 'Foo'})
        user = self.env['res.users'].create({'name': 'Foo', 'login': 'foo'})
        self.assertFalse(user.has_group('base.group_system'))
        # add group on non-stored inverse field
        self.patch(type(foo).display_name, 'groups', 'base.group_system')
        with self.assertRaises(AccessError):
            foo.with_user(user).display_name = 'Forbidden'

    def test_13_inverse_with_unlink(self):
        """ test x2many delete command combined with an inverse field """

        country1 = self.env['res.country'].create({'name': 'test country', 'code': 'ZV'})
        country2 = self.env['res.country'].create({'name': 'other country', 'code': 'ZX'})
        company = self.env['res.company'].create({
            'name': 'test company',
            'child_ids': [
                (0, 0, {'name': 'Child Company 1'}),
                (0, 0, {'name': 'Child Company 2'}),
            ],
        })
        child_company = company.child_ids[0]

        # check first that the field has an inverse and is not stored
        field = type(company).country_id
        self.assertFalse(field.store)
        self.assertTrue(field.inverse)

        company.write({'country_id': country1.id})
        self.assertEqual(company.country_id, country1)

        company.write({
            'country_id': country2.id,
            'child_ids': [(2, child_company.id)],
        })
        self.assertEqual(company.country_id, country2)

    def test_14_search(self):
        """ test search on computed fields """
        discussion = self.env.ref('test_orm.discussion_0')

        # determine message sizes
        sizes = {message.size for message in discussion.messages}

        # search for messages based on their size
        for size in sizes:
            messages0 = self.env['test_orm.message'].search(
                [('discussion', '=', discussion.id), ('size', '<=', size)])

            messages1 = self.env['test_orm.message'].browse()
            for message in discussion.messages:
                if message.size <= size:
                    messages1 += message

            self.assertEqual(messages0, messages1)

    def test_15_constraint(self):
        """ test new-style Python constraints """
        discussion = self.env.ref('test_orm.discussion_0')
        self.env.flush_all()

        # remove oneself from discussion participants: we can no longer create
        # messages in discussion
        discussion.participants -= self.env.user
        with self.assertRaises(ValidationError):
            self.env['test_orm.message'].create({'discussion': discussion.id, 'body': 'Whatever'})

        # make sure that assertRaises() does not leave fields to recompute
        self.assertFalse(self.env.fields_to_compute())

        # put back oneself into discussion participants: now we can create
        # messages in discussion
        discussion.participants += self.env.user
        self.env['test_orm.message'].create({'discussion': discussion.id, 'body': 'Whatever'})

        # check constraint on recomputed field
        self.assertTrue(discussion.messages)
        with self.assertRaises(ValidationError):
            discussion.name = "X"

    def test_15_constraint_inverse(self):
        """ test constraint method on normal field and field with inverse """
        log = []
        model = self.env['test_orm.compute.inverse'].with_context(log=log, log_constraint=True)

        # create/write with normal field only
        log.clear()
        record = model.create({'baz': 'Hi'})
        self.assertCountEqual(log, ['constraint'])

        log.clear()
        record.write({'baz': 'Ho'})
        self.assertCountEqual(log, ['constraint'])

        # create/write with inverse field only
        log.clear()
        record = model.create({'bar': 'Hi'})
        self.assertCountEqual(log, ['inverse', 'constraint'])

        log.clear()
        record.write({'bar': 'Ho'})
        self.assertCountEqual(log, ['inverse', 'constraint'])

        # create/write with both fields only
        log.clear()
        record = model.create({'bar': 'Hi', 'baz': 'Hi'})
        self.assertCountEqual(log, ['inverse', 'constraint'])

        log.clear()
        record.write({'bar': 'Ho', 'baz': 'Ho'})
        self.assertCountEqual(log, ['inverse', 'constraint'])

    def test_16_compute_unassigned(self):
        model = self.env['test_orm.compute.unassigned']

        # real record
        record = model.create({})
        with self.assertRaises(ValueError):
            record.bar
        self.assertEqual(record.bare, False)
        self.assertEqual(record.bars, False)
        self.assertEqual(record.bares, False)

        # new record
        record = model.new()
        with self.assertRaises(ValueError):
            record.bar
        self.assertEqual(record.bare, False)
        self.assertEqual(record.bars, False)
        self.assertEqual(record.bares, False)

    def test_16_compute_unassigned_access_error(self):
        # create two records
        records = self.env['test_orm.compute.unassigned'].create([{}, {}])
        self.env.flush_all()

        # alter access rights: regular users cannot read 'records'
        access = self.env.ref('test_orm.access_test_orm_compute_unassigned')
        access.perm_read = False
        self.env.flush_all()

        # switch to environment with user demo
        records = records.with_user(self.user_demo)

        # check that records are not accessible
        with self.assertRaises(AccessError):
            records[0].bars
        with self.assertRaises(AccessError):
            records[1].bars

        # Modify the records and flush() changes with the current environment:
        # this should not trigger an access error, whatever the order in which
        # records are considered.  It may fail in the following scenario:
        #  - mark field 'bars' to compute on records
        #  - access records[0].bars
        #     - recompute bars on records (both) -> assign records[0] only
        #     - return records[0].bars from cache
        #  - access records[1].bars
        #     - recompute nothing (done already)
        #     - records[1].bars is not in cache
        #     - fetch records[1].bars -> access error
        records[0].foo = "assign"
        records[1].foo = "x"
        self.env.flush_all()

        # try the other way around, too
        self.env.invalidate_all()
        records[0].foo = "x"
        records[1].foo = "assign"
        self.env.flush_all()

    def test_17_compute_depends_on_many2many(self):
        user1, user2, user3 = self.env['test_orm.user'].create([{}, {}, {}])
        group = self.env['test_orm.group'].create({'user_ids': [Command.link(user1.id)]})
        self.env.flush_all()

        field = type(user1).group_count
        self.assertFalse(self.env.records_to_compute(field))

        # should mark user2 and user3 to compute only
        group.write({'user_ids': [Command.link(user1.id), Command.link(user2.id), Command.link(user3.id)]})
        self.assertEqual(self.env.records_to_compute(field), user2 + user3)

        # should mark user2 to compute only
        self.env.flush_all()
        group.write({'user_ids': [Command.unlink(user2.id)]})
        self.assertEqual(self.env.records_to_compute(field), user2)

        # should mark user2 and user3 to compute only
        self.env.flush_all()
        group.write({'user_ids': [Command.set([user1.id, user2.id])]})
        self.assertEqual(self.env.records_to_compute(field), user2 + user3)

        # should mark user3 to compute only
        self.env.flush_all()
        user3.write({'group_ids': [Command.link(group.id)]})
        self.assertEqual(self.env.records_to_compute(field), user3)

        # similar with new records, but only check recomputation
        user1 = self.env['test_orm.user'].new({})
        user2 = self.env['test_orm.user'].new({})
        group = self.env['test_orm.group'].new({'user_ids': [user1.id]})
        self.assertEqual(user1.group_count, 1)
        self.assertEqual(user2.group_count, 0)

        group.user_ids += user2
        self.assertEqual(user1.group_count, 1)
        self.assertEqual(user2.group_count, 1)

    def test_20_float(self):
        """ test rounding of float fields """
        record = self.env['test_orm.mixed'].create({})
        query = "SELECT 1 FROM test_orm_mixed WHERE id=%s AND number=%s"

        # 2.49609375 (exact float) must be rounded to 2.5
        record.write({'number': 2.49609375})
        self.env.flush_all()
        self.cr.execute(query, [record.id, '2.5'])
        self.assertTrue(self.cr.rowcount)
        self.assertEqual(record.number, 2.5)

        # 1.1 (1.1000000000000000888178420 in float) must be 1.1 in database
        record.write({'number': 1.1})
        self.env.flush_all()
        self.cr.execute(query, [record.id, '1.1'])
        self.assertTrue(self.cr.rowcount)
        self.assertEqual(record.number, 1.1)

    def test_21_float_digits(self):
        """ test field description """
        precision = self.env.ref('test_orm.decimal_orm_number')
        description = self.env['test_orm.mixed'].fields_get()['number2']
        self.assertEqual(description['digits'], (16, precision.digits))

    def check_monetary(self, record, amount, currency, msg=None):
        # determine the possible roundings of amount
        if currency:
            ramount = currency.round(amount)
            samount = float(float_repr(ramount, currency.decimal_places))
        else:
            ramount = samount = amount

        # check the currency on record
        self.assertEqual(record.currency_id, currency)

        # check the value on the record
        self.assertIn(record.amount, [ramount, samount], msg)

        # check the value in the database
        self.env.flush_all()
        self.cr.execute('SELECT amount FROM test_orm_mixed WHERE id=%s', [record.id])
        value = self.cr.fetchone()[0]
        self.assertEqual(value, samount, msg)

    def test_20_monetary(self):
        """ test monetary fields """
        model = self.env['test_orm.mixed']
        currency = self.env['res.currency'].with_context(active_test=False)
        amount = 14.70126

        for rounding in [0.01, 0.0001, 1.0, 0]:
            # first retrieve a currency corresponding to rounding
            if rounding:
                currency = currency.search([('rounding', '=', rounding)], limit=1)
                self.assertTrue(currency, "No currency found for rounding %s" % rounding)
            else:
                # rounding=0 corresponds to currency=False
                currency = currency.browse()

            # case 1: create with amount and currency
            record = model.create({'amount': amount, 'currency_id': currency.id})
            self.check_monetary(record, amount, currency, 'create(amount, currency)')

            # case 2: assign amount
            record.amount = 0
            record.amount = amount
            self.check_monetary(record, amount, currency, 'assign(amount)')

            # case 3: write with amount and currency
            record.write({'amount': 0, 'currency_id': False})
            record.write({'amount': amount, 'currency_id': currency.id})
            self.check_monetary(record, amount, currency, 'write(amount, currency)')

            # case 4: write with amount only
            record.write({'amount': 0})
            record.write({'amount': amount})
            self.check_monetary(record, amount, currency, 'write(amount)')

            # case 5: write with amount on several records
            records = record + model.create({'currency_id': currency.id})
            records.write({'amount': 0})
            records.write({'amount': amount})
            for record in records:
                self.check_monetary(record, amount, currency, 'multi write(amount)')

    def test_20_monetary_opw_2223134(self):
        """ test monetary fields with cache override """
        model = self.env['test_orm.monetary_order']
        currency = self.env.ref('base.USD')

        def check(value):
            self.assertEqual(record.total, value)
            self.env.flush_all()
            self.cr.execute('SELECT total FROM test_orm_monetary_order WHERE id=%s', [record.id])
            [total] = self.cr.fetchone()
            self.assertEqual(total, value)

        # create, and compute amount
        record = model.create({
            'currency_id': currency.id,
            'line_ids': [Command.create({'subtotal': 1.0})],
        })
        check(1.0)

        # delete and add a line: the deletion of the line clears the cache, then
        # the recomputation of 'total' must prefetch record.currency_id without
        # screwing up the new value in cache
        record.write({
            'line_ids': [Command.delete(record.line_ids.id), Command.create({'subtotal': 1.0})],
        })
        check(1.0)

    def test_20_monetary_related(self):
        """ test value rounding with related currency """
        currency = self.env.ref('base.USD')
        monetary_base = self.env['test_orm.monetary_base'].create({
            'base_currency_id': currency.id,
        })
        monetary_related = self.env['test_orm.monetary_related'].create({
            'monetary_id': monetary_base.id,
            'total': 1 / 3,
        })
        self.env.cr.execute(
            "SELECT total FROM test_orm_monetary_related WHERE id=%s",
            monetary_related.ids,
        )
        [total] = self.env.cr.fetchone()
        self.assertEqual(total, .33)

    def test_20_like(self):
        """ test filtered_domain() on char fields. """
        record = self.env['test_orm.multi.tag'].create({'name': 'Foo'})
        self.assertTrue(record.filtered_domain([('name', 'like', 'F')]))
        self.assertTrue(record.filtered_domain([('name', 'ilike', 'f')]))

        record.name = 'Bar'
        self.assertFalse(record.filtered_domain([('name', 'like', 'F')]))
        self.assertFalse(record.filtered_domain([('name', 'ilike', 'f')]))

        record.name = False
        self.assertFalse(record.filtered_domain([('name', 'like', 'F')]))
        self.assertFalse(record.filtered_domain([('name', 'ilike', 'f')]))

    def test_21_date(self):
        """ test date fields """
        record = self.env['test_orm.mixed'].create({})

        # one may assign False or None
        record.date = None
        self.assertFalse(record.date)

        # one may assign date but not datetime objects
        record.date = date(2012, 5, 1)
        self.assertEqual(record.date, date(2012, 5, 1))

        # DLE P41: We now support to assign datetime to date. Not sure this is the good practice though.
        # with self.assertRaises(TypeError):
        #     record.date = datetime(2012, 5, 1, 10, 45, 0)

        # one may assign dates and datetime in the default format, and it must be checked
        record.date = '2012-05-01'
        self.assertEqual(record.date, date(2012, 5, 1))

        record.date = "2012-05-01 10:45:00"
        self.assertEqual(record.date, date(2012, 5, 1))

        with self.assertRaises(ValueError):
            record.date = '12-5-1'

        # check filtered_domain
        self.assertTrue(record.filtered_domain([('date', '<', '2012-05-02')]))
        self.assertTrue(record.filtered_domain([('date', '<', date(2012, 5, 2))]))
        self.assertTrue(record.filtered_domain([('date', '<', datetime(2012, 5, 2, 12, 0, 0))]))
        self.assertTrue(record.filtered_domain([('date', '!=', False)]))
        self.assertFalse(record.filtered_domain([('date', '=', False)]))

        record.date = None
        self.assertFalse(record.filtered_domain([('date', '<', '2012-05-02')]))
        self.assertFalse(record.filtered_domain([('date', '<', date(2012, 5, 2))]))
        self.assertFalse(record.filtered_domain([('date', '<', datetime(2012, 5, 2, 12, 0, 0))]))
        self.assertFalse(record.filtered_domain([('date', '!=', False)]))
        self.assertTrue(record.filtered_domain([('date', '=', False)]))

    def test_21_datetime(self):
        """ test datetime fields """
        for _i in range(0, 10):
            self.assertEqual(fields.Datetime.now().microsecond, 0)

        record = self.env['test_orm.mixed'].create({})

        # assign falsy value
        record.moment = None
        self.assertFalse(record.moment)

        # assign string
        record.moment = '2012-05-01'
        self.assertEqual(record.moment, datetime(2012, 5, 1))
        record.moment = '2012-05-01 06:00:00'
        self.assertEqual(record.moment, datetime(2012, 5, 1, 6))
        with self.assertRaises(ValueError):
            record.moment = '12-5-1'

        # assign date or datetime
        record.moment = date(2012, 5, 1)
        self.assertEqual(record.moment, datetime(2012, 5, 1))
        record.moment = datetime(2012, 5, 1, 6)
        self.assertEqual(record.moment, datetime(2012, 5, 1, 6))

        # check filtered_domain
        self.assertTrue(record.filtered_domain([('moment', '<', '2012-05-02')]))
        self.assertTrue(record.filtered_domain([('moment', '<', date(2012, 5, 2))]))
        self.assertTrue(record.filtered_domain([('moment', '<', datetime(2012, 5, 1, 12, 0, 0))]))
        self.assertTrue(record.filtered_domain([('moment', '!=', False)]))
        self.assertFalse(record.filtered_domain([('moment', '=', False)]))

        record.moment = None
        self.assertFalse(record.filtered_domain([('moment', '<', '2012-05-02')]))
        self.assertFalse(record.filtered_domain([('moment', '<', date(2012, 5, 2))]))
        self.assertFalse(record.filtered_domain([('moment', '<', datetime(2012, 5, 2, 12, 0, 0))]))
        self.assertFalse(record.filtered_domain([('moment', '!=', False)]))
        self.assertTrue(record.filtered_domain([('moment', '=', False)]))

    def test_21_date_dynamic(self):
        record = self.env['test_orm.mixed'].create({'moment': fields.Datetime.now()})
        self.assertEqual(record, self._search(record, [('moment', '<', 'now +1d')], [('id', 'in', record.ids)]))
        self.assertFalse(self._search(record, [('moment', '<', 'today')], [('id', 'in', record.ids)]))
        self.assertEqual(record, self._search(record, [('moment', '>', '-1H')], [('id', 'in', record.ids)]))

    def test_22_selection(self):
        """ test selection fields """
        record_list = self.env['test_orm.selection'].create({})
        self.assertIsInstance(record_list._fields['state'].selection, list)

        # the following selection is defined by a callable (method name)
        record_call = self.env['test_orm.mixed'].create({})
        self.assertIsInstance(record_call._fields['lang'].selection, str)

        # one may assign a value
        record_list.state = 'foo'
        record_call.lang = self.env['res.lang'].search([], limit=1).code

        # one may assign False or None
        record_list.state = None
        self.assertFalse(record_list.state)
        record_call.lang = None
        self.assertFalse(record_call.lang)

        # the assigned value is only checked for the list case
        with self.assertRaises(ValueError):
            record_list.state = 'zz_ZZ'
        record_call.lang = 'zz_ZZ'

    def test_23_relation(self):
        """ test relation fields """
        demo = self.user_demo
        message = self.env.ref('test_orm.message_0_0')

        # check environment of record and related records
        self.assertEqual(message.env, self.env)
        self.assertEqual(message.discussion.env, self.env)

        demo_env = self.env(user=demo)
        self.assertNotEqual(demo_env, self.env)

        # check environment of record and related records
        self.assertEqual(message.env, self.env)
        self.assertEqual(message.discussion.env, self.env)

        # "migrate" message into demo_env, and check again
        demo_message = message.with_user(demo)
        self.assertEqual(demo_message.env, demo_env)
        self.assertEqual(demo_message.discussion.env, demo_env)

        # See YTI FIXME
        self.env.invalidate_all()

        # assign record's parent to a record in demo_env
        message.discussion = message.discussion.copy({'name': 'Copy'})

        # both message and its parent field must be in self.env
        self.assertEqual(message.env, self.env)
        self.assertEqual(message.discussion.env, self.env)

    def test_24_reference(self):
        """ test reference fields. """
        record = self.env['test_orm.mixed'].create({})

        # one may assign False or None
        record.reference = None
        self.assertFalse(record.reference)

        # one may assign a user or a partner...
        record.reference = self.env.user
        self.assertEqual(record.reference, self.env.user)
        record.reference = self.env.user.partner_id
        self.assertEqual(record.reference, self.env.user.partner_id)
        # ... but no record from a model that starts with 'ir.'
        with self.assertRaises(ValueError):
            record.reference = self.env['ir.model'].search([], limit=1)

    def test_25_related(self):
        """ test related fields. """
        message = self.env.ref('test_orm.message_0_0')
        discussion = message.discussion

        # by default related fields are not stored
        field = message._fields['discussion_name']
        self.assertFalse(field.store)
        self.assertFalse(field.readonly)

        # check value of related field
        self.assertEqual(message.discussion_name, discussion.name)

        # change discussion name, and check result
        discussion.name = 'Foo'
        self.assertEqual(message.discussion_name, 'Foo')

        # change discussion name via related field, and check result
        message.discussion_name = 'Bar'
        self.assertEqual(discussion.name, 'Bar')
        self.assertEqual(message.discussion_name, 'Bar')

        # change discussion name via related field on several records
        discussion1 = discussion.create({'name': 'X1'})
        discussion2 = discussion.create({'name': 'X2'})
        discussion1.participants = discussion2.participants = self.env.user
        message1 = message.create({'discussion': discussion1.id})
        message2 = message.create({'discussion': discussion2.id})
        self.assertEqual(message1.discussion_name, 'X1')
        self.assertEqual(message2.discussion_name, 'X2')

        (message1 + message2).write({'discussion_name': 'X3'})
        self.assertEqual(discussion1.name, 'X3')
        self.assertEqual(discussion2.name, 'X3')

        # search on related field, and check result
        search_on_related = self.env['test_orm.message'].search([('discussion_name', '=', 'Bar')])
        search_on_regular = self.env['test_orm.message'].search([('discussion.name', '=', 'Bar')])
        self.assertEqual(search_on_related, search_on_regular)

        # check that field attributes are copied
        message_field = message.fields_get(['discussion_name'])['discussion_name']
        discussion_field = discussion.fields_get(['name'])['name']
        self.assertEqual(message_field['help'], discussion_field['help'])

    def test_25_related_attributes(self):
        """ test the attributes of related fields """
        text = self.registry['test_orm.foo'].text
        self.assertFalse(text.trim, "The target field is defined with trim=False")

        # trim=True is the default on the field's class
        self.assertTrue(type(text).trim, "By default, a Char field has trim=True")

        # the parameter 'trim' is not set in text1's definition, so the field
        # retrieves its value from text.trim
        text1 = self.registry['test_orm.bar'].text1
        self.assertFalse(text1.trim, "The related field retrieves trim=False from target")

        # text2 is defined with trim=True, so it should get that value
        text2 = self.registry['test_orm.bar'].text2
        self.assertTrue(text2.trim, "The related field was defined with trim=True")

        # now let's change text.trim, and check that text1 gets the new value;
        # this tests the behavior of related fields when setting up models
        # incrementally
        self.patch(text, 'trim', True)
        self.registry._setup_models__(self.cr, ['test_orm.foo'])
        self.assertTrue(self.registry['test_orm.foo'].text.trim)
        self.assertTrue(self.registry['test_orm.bar'].text1.trim)

    def test_25_related_single(self):
        """ test related fields with a single field in the path. """
        record = self.env['test_orm.related'].create({'name': 'A'})
        self.assertEqual(record.related_name, record.name)
        self.assertEqual(record.related_related_name, record.name)

        # check searching on related fields
        records0 = self._search(record, [('name', '=', 'A')])
        self.assertIn(record, records0)
        records1 = self._search(record, [('related_name', '=', 'A')])
        self.assertEqual(records1, records0)
        records2 = self._search(record, [('related_related_name', '=', 'A')])
        self.assertEqual(records2, records0)

        # check writing on related fields
        record.write({'related_name': 'B'})
        self.assertEqual(record.name, 'B')
        record.write({'related_related_name': 'C'})
        self.assertEqual(record.name, 'C')

    def test_25_related_multi(self):
        """ test write() on several related fields based on a common computed field. """
        foo = self.env['test_orm.foo'].create({'name': 'A', 'value1': 1, 'value2': 2})
        oof = self.env['test_orm.foo'].create({'name': 'B', 'value1': 1, 'value2': 2})
        bar = self.env['test_orm.bar'].create({'name': 'A'})
        self.assertEqual(bar.foo, foo)
        self.assertEqual(bar.value1, 1)
        self.assertEqual(bar.value2, 2)

        self.env.invalidate_all()
        bar.write({'value1': 3, 'value2': 4})
        self.assertEqual(foo.value1, 3)
        self.assertEqual(foo.value2, 4)

        # modify 'name', and search on 'foo': this should flush 'name'
        bar.name = 'B'
        self.assertEqual(bar.foo, oof)
        self.assertIn(bar, bar.search([('foo', 'in', oof.ids)]))

    def test_25_related_many2one(self):
        bar = self.env['test_orm.related_bar'].create({'name': 'A'})
        foo = self.env['test_orm.related_foo'].create({'name': 'A', 'bar_id': bar.id})
        self.assertEqual(foo.bar_id, bar)
        self.assertEqual(foo.bar_alias, foo.bar_id)

        # After deactivating the foo record, the search should be executed with
        # context depending on searching a many2one field: active_test=False.
        for active in (True, False):
            with self.subTest(active=active):
                bar.active = active
                self.assertEqual(foo.search([('id', 'in', foo.ids), ('bar_id', 'ilike', 'A')]), foo)
                self.assertEqual(foo.search([('id', 'in', foo.ids), ('bar_alias', 'ilike', 'A')]), foo)

    def test_25_one2many_inverse_related(self):
        left = self.env['test_orm.trigger.left'].create({})
        right = self.env['test_orm.trigger.right'].create({})
        self.assertFalse(left.right_id)
        self.assertFalse(right.left_ids)
        self.assertFalse(right.left_size)

        # create middle: this should trigger left.right_id by traversing
        # middle.left_id, and right.left_size by traversing left.right_id
        # after its computation!
        middle = self.env['test_orm.trigger.middle'].create({
            'left_id': left.id,
            'right_id': right.id,
        })
        self.assertEqual(left.right_id, right)
        self.assertEqual(right.left_ids, left)
        self.assertEqual(right.left_size, 1)

        # delete middle: this should trigger left.right_id by traversing
        # middle.left_id, and right.left_size by traversing left.right_id
        # before its computation!
        middle.unlink()
        self.assertFalse(left.right_id)
        self.assertFalse(right.left_ids)
        self.assertFalse(right.left_size)

    def test_26_inherited(self):
        """ test inherited fields. """
        # a bunch of fields are inherited from res_partner
        for user in self.env['res.users'].search([]):
            partner = user.partner_id
            for field in ('is_company', 'name', 'email', 'country_id'):
                self.assertEqual(getattr(user, field), getattr(partner, field))
                self.assertEqual(user[field], partner[field])

    def test_27_company_dependent(self):
        """ test company-dependent fields. """
        # Company-dependent field variants should handle 0, '' and NULL database values
        # in the same way as their 'normal' (non-company-dependent) variants.
        # This section relies on there being no company defaults, so it needs to run first.
        null_record = self.env['test_orm.company'].create({})
        null_record_normal = self.env['test_orm.mixed'].create({})
        null_record.invalidate_recordset()
        null_record_normal.invalidate_recordset()
        field_correspondence = [
            ('foo', 'foo', ''),
            ('text', 'text', ''),
            ('date', 'date', False),
            ('moment', 'moment', False),
            ('truth', 'truth', False),
            ('count', 'count', 0),
            ('phi', 'number2', 0.0),
            ('html1', 'comment1', ''),
        ]
        # Check null values
        for field, normal_field, value_to_write in field_correspondence:
            self.assertEqual(null_record[field], null_record_normal[normal_field])
            null_record[field] = null_record_normal[normal_field] = value_to_write

        # Check empty / 0 values
        null_record.invalidate_recordset()
        null_record_normal.invalidate_recordset()
        for field, normal_field, _ in field_correspondence:
            self.assertEqual(null_record[field], null_record_normal[normal_field])

        # consider three companies
        company0 = self.env.ref('base.main_company')
        company1 = self.env['res.company'].create({'name': 'A'})
        company2 = self.env['res.company'].create({'name': 'B'})

        # create one user per company
        user0 = self.env['res.users'].create({
            'name': 'Foo', 'login': 'foo', 'company_id': company0.id,
            'company_ids': [Command.set([company0.id, company1.id, company2.id])]})
        user1 = self.env['res.users'].create({
            'name': 'Bar', 'login': 'bar', 'company_id': company1.id,
            'company_ids': [Command.set([company0.id, company1.id, company2.id])]})
        user2 = self.env['res.users'].create({
            'name': 'Baz', 'login': 'baz', 'company_id': company2.id,
            'company_ids': [Command.set([company0.id, company1.id, company2.id])]})

        # create values for many2one field
        tag0 = self.env['test_orm.multi.tag'].create({'name': 'Qux'})
        tag1 = self.env['test_orm.multi.tag'].create({'name': 'Quux'})
        tag2 = self.env['test_orm.multi.tag'].create({'name': 'Quuz'})

        # create default values for the company-dependent fields
        self.env['ir.default'].set('test_orm.company', 'foo', 'default')
        self.env['ir.default'].set('test_orm.company', 'foo', 'default1', company_id=company1.id)
        self.env['ir.default'].set('test_orm.company', 'tag_id', tag0.id)

        # assumption: users don't have access to 'ir.default'
        accesses = self.env['ir.model.access'].search([('model_id.model', '=', 'ir.default')])
        accesses.write(dict.fromkeys(['perm_read', 'perm_write', 'perm_create', 'perm_unlink'], False))

        # create/modify a record, and check the value for each user
        record = self.env['test_orm.company'].create({
            'foo': 'main',
            'date': '1932-11-09',
            'moment': '1932-11-09 00:00:00',
            'tag_id': tag1.id,
        })
        self.assertEqual(record.with_user(user0).foo, 'main')
        self.assertEqual(record.with_user(user1).foo, 'default1')
        self.assertEqual(record.with_user(user2).foo, 'default')
        self.assertEqual(str(record.with_user(user0).date), '1932-11-09')
        self.assertEqual(record.with_user(user1).date, False)
        self.assertEqual(record.with_user(user2).date, False)
        self.assertEqual(str(record.with_user(user0).moment), '1932-11-09 00:00:00')
        self.assertEqual(record.with_user(user1).moment, False)
        self.assertEqual(record.with_user(user2).moment, False)
        self.assertEqual(record.with_user(user0).tag_id, tag1)
        self.assertEqual(record.with_user(user1).tag_id, tag0)
        self.assertEqual(record.with_user(user2).tag_id, tag0)

        record.with_user(user1).write({
            'foo': 'alpha',
            'date': '1932-12-10',
            'moment': '1932-12-10 23:59:59',
            'tag_id': tag2.id,
        })
        self.assertEqual(record.with_user(user0).foo, 'main')
        self.assertEqual(record.with_user(user1).foo, 'alpha')
        self.assertEqual(record.with_user(user2).foo, 'default')
        self.assertEqual(str(record.with_user(user0).date), '1932-11-09')
        self.assertEqual(str(record.with_user(user1).date), '1932-12-10')
        self.assertEqual(record.with_user(user2).date, False)
        self.assertEqual(str(record.with_user(user0).moment), '1932-11-09 00:00:00')
        self.assertEqual(str(record.with_user(user1).moment), '1932-12-10 23:59:59')
        self.assertEqual(record.with_user(user2).moment, False)
        self.assertEqual(record.with_user(user0).tag_id, tag1)
        self.assertEqual(record.with_user(user1).tag_id, tag2)
        self.assertEqual(record.with_user(user2).tag_id, tag0)

        # regression: duplicated records caused values to be browse(browse(id))
        recs = record.create({}) + record + record
        self.env.invalidate_all()
        for rec in recs.with_user(user0):
            self.assertIsInstance(rec.tag_id.id, int)

        # unlink value of a many2one (tag2), and check again
        tag2.unlink()
        self.assertEqual(record.with_user(user0).tag_id, tag1)
        self.assertEqual(record.with_user(user1).tag_id, tag0.browse())
        self.assertEqual(record.with_user(user2).tag_id, tag0)

        record.with_user(user1).foo = False
        self.assertEqual(record.with_user(user0).foo, 'main')
        self.assertEqual(record.with_user(user1).foo, False)
        self.assertEqual(record.with_user(user2).foo, 'default')

        record.with_user(user0).with_company(company1).foo = 'beta'
        self.env.invalidate_all()
        self.assertEqual(record.with_user(user0).foo, 'main')
        self.assertEqual(record.with_user(user1).foo, 'beta')
        self.assertEqual(record.with_user(user2).foo, 'default')

        # add group on company-dependent field
        self.assertFalse(user0.has_group('base.group_system'))
        self.patch(type(record).foo, 'groups', 'base.group_system')
        with self.assertRaises(AccessError):
            record.with_user(user0).foo = 'forbidden'

        user0.write({'group_ids': [Command.link(self.env.ref('base.group_system').id)]})
        record.with_user(user0).foo = 'yes we can'

        # add ir.rule to prevent access on record
        self.assertTrue(user0._is_internal())
        self.env['ir.rule'].create({
            'model_id': self.env['ir.model']._get_id(record._name),
            'groups': [self.env.ref('base.group_user').id],
            'domain_force': str([('id', '!=', record.id)]),
        })
        with self.assertRaises(AccessError):
            record.with_user(user0).foo = 'forbidden'

        # create company record and attribute
        company_record = self.env['test_orm.company'].create({'foo': 'ABC'})
        attribute_record = self.env['test_orm.company.attr'].create({
            'company': company_record.id,
            'quantity': 1,
        })
        self.assertEqual(attribute_record.bar, 'ABC')

        # change quantity, 'bar' should recompute to 'ABCABC'
        attribute_record.quantity = 2
        self.assertEqual(attribute_record.bar, 'ABCABC')

        # change company field 'foo', 'bar' should recompute to 'DEFDEF'
        company_record.foo = 'DEF'
        self.assertEqual(attribute_record.company.foo, 'DEF')
        self.assertEqual(attribute_record.bar, 'DEFDEF')

        # a low priviledge user should be able to search on company_dependent fields
        company_record.env.user.group_ids -= self.env.ref('base.group_system')
        self.assertFalse(company_record.env.user.has_group('base.group_system'))
        company_records = self.env['test_orm.company'].search([('foo', '=', 'DEF')])
        self.assertEqual(len(company_records), 1)

    def test_27_company_dependent_bool_integer_float(self):
        company0 = self.env.ref('base.main_company')
        company1 = self.env['res.company'].create({'name': 'A'})
        Model = self.env['test_orm.company']
        record = Model.create({})
        record.invalidate_recordset()
        cr = self.env.cr
        cr.execute("SELECT truth, count, phi FROM test_orm_company WHERE id = %s", (record.id,))
        self.assertEqual(cr.fetchone(), (None, None, None))
        for company in [company0, company1]:
            record_company = record.with_company(company)
            self.assertEqual(record_company.truth, False)
            self.assertEqual(record_company.count, 0)
            self.assertEqual(record_company.phi, 0.0)
        record.write({'truth': False, 'count': 0, 'phi': 0})  # write fallback equivalent
        record.invalidate_recordset()
        cr.execute("SELECT truth, count, phi FROM test_orm_company WHERE id = %s", (record.id,))
        self.assertEqual(cr.fetchone(), (None, None, None))

    def test_27_company_dependent_missing_many2one(self):
        """ Test ORM can handle missing records for many2one company dependent fields """
        company0 = self.env.ref('base.main_company')  # noqa: F841
        company1 = self.env['res.company'].create({'name': 'A'})  # noqa: F841
        Model = self.env['test_orm.company']
        record = Model.create({})
        record.tag_id = 1000  # non-existing record id
        record.invalidate_recordset()

        self.env.cr.execute(
            'SELECT id FROM test_orm_company WHERE id = %s and (tag_id->%s)::int = %s',
            [record.id, str(self.env.company.id), 1000],
        )
        self.assertEqual(self.env.cr.rowcount, 1)
        self.assertFalse(record.tag_id)
        self.assertEqual(
            record.search([('id', '=', record.id), ('tag_id', '=', False)]),
            record,
        )

    def test_28_company_dependent_search(self):
        """ Test the search on company-dependent fields in all corner cases.
            This assumes that filtered_domain() correctly filters records when
            its domain refers to company-dependent fields.
        """
        IrDefault = self.env['ir.default']
        Model = self.env['test_orm.company']

        # create 4 records for all cases: two with explicit truthy values, one
        # with an explicit falsy value, and one without an explicit value
        records = Model.create([{}] * 4)
        record_fallback = Model.create({})

        # For each field, we assign values to the records, and test a number of
        # searches.  The search cases are given by comparison operators, and for
        # each operator, we test a number of possible operands.  Every search()
        # returns a subset of the records, and we compare it to an equivalent
        # search performed by filtered_domain().

        def test_field(field_name, truthy_values, operations):
            # set ir.defaults to all records except the last one
            for rec, val in zip(records, truthy_values + [False]):
                rec[field_name] = val

            # test without default value
            test_cases(field_name, operations)

            # set default value to False
            IrDefault.set(Model._name, field_name, False)
            self.env.flush_all()
            self.env.invalidate_all()
            for rec, val in zip(records, truthy_values + [False]):
                rec[field_name] = val
            test_cases(field_name, operations, False)

            # set default value to truthy_values[0]
            IrDefault.set(Model._name, field_name, truthy_values[0])
            self.env.flush_all()
            self.env.invalidate_all()
            for rec, val in zip(records, truthy_values + [False]):
                rec[field_name] = val
            test_cases(field_name, operations, truthy_values[0])

        def test_cases(field_name, operations, default=None):
            model = self.env['test_orm.company']
            field = model._fields[field_name]
            field_fallback = field.get_company_dependent_fallback(model)
            record_fallback[field_name] = field_fallback
            current_thread = threading.current_thread()

            for operator, values in operations.items():
                for value in values:
                    domain = [(field_name, operator, value)]
                    company_dependent_column_not_null = not record_fallback.filtered_domain(domain)
                    if company_dependent_column_not_null:
                        with self.subTest(domain=domain, default=default):
                            Model.search([('id', 'in', records.ids)] + domain)
                            current_thread.query_count = 0
                            current_thread.query_time = 0
                            Model.search([('id', 'in', records.ids)] + domain)  # warmup
                            if current_thread.query_count:
                                # parent_of and child_of may need extra queries
                                expected_contained_sqls = [''] * (current_thread.query_count - 1) + [f'"test_orm_company"."{field_name}" IS NOT NULL']
                                with self.assertQueriesContain(expected_contained_sqls):
                                    Model.search([('id', 'in', records.ids)] + domain)

                    with self.subTest(domain=domain, default=default):
                        self._search(
                            Model,
                            [('id', 'in', records.ids)] + domain,
                            [('id', 'in', records.ids)],
                            test_complement=True,
                        )

        # boolean fields
        test_field('truth', [True, True], {
            '=': (True, False),
            '!=': (True, False),
        })
        # integer fields
        test_field('count', [10, -2], {
            '=': (10, -2, 0, False),
            '!=': (10, -2, 0, False),
            '<': (10, -2, 0),
            '>=': (10, -2, 0),
            '<=': (10, -2, 0),
            '>': (10, -2, 0),
        })
        # float fields
        test_field('phi', [1.61803, -1], {
            '=': (1.61803, -1, 0, False),
            '!=': (1.61803, -1, 0, False),
            '<': (1.61803, -1, 0),
            '>=': (1.61803, -1, 0),
            '<=': (1.61803, -1, 0),
            '>': (1.61803, -1, 0),
        })
        # char fields
        test_field('foo', ['qwer', 'azer'], {
            'like': ('qwer', 'azer'),
            'ilike': ('qwer', 'azer'),
            'not like': ('qwer', 'azer'),
            'not ilike': ('qwer', 'azer'),
            '=': ('qwer', 'azer', False),
            '!=': ('qwer', 'azer', False),
            'not in': (['qwer', 'azer'], ['qwer', False], [False], []),
            'in': (['qwer', 'azer'], ['qwer', False], [False], []),
        })
        # date fields
        date1, date2 = date(2021, 11, 22), date(2021, 11, 23)
        test_field('date', [date1, date2], {
            '=': (date1, date2, False),
            '!=': (date1, date2, False),
            '<': (date1, date2),
            '>=': (date1, date2),
            '<=': (date1, date2),
            '>': (date1, date2),
        })
        # datetime fields
        moment1, moment2 = datetime(2021, 11, 22), datetime(2021, 11, 23)
        test_field('moment', [moment1, moment2], {
            '=': (moment1, moment2, False),
            '!=': (moment1, moment2, False),
            '<': (moment1, moment2),
            '>=': (moment1, moment2),
            '<=': (moment1, moment2),
            '>': (moment1, moment2),
        })
        # many2one fields
        tag1, tag2 = self.env['test_orm.multi.tag'].create([{'name': 'one'}, {'name': 'two'}])
        test_field('tag_id', [tag1.id, tag2.id], {
            'like': (tag1.name, tag2.name),
            'ilike': (tag1.name, tag2.name),
            'not like': (tag1.name, tag2.name),
            'not ilike': (tag1.name, tag2.name),
            '=': (tag1.id, tag2.id, False),
            '!=': (tag1.id, tag2.id, False),
            'in': ([tag1.id, tag2.id], [tag2.id, False], [False], []),
            'not in': ([tag1.id, tag2.id], [tag2.id, False], [False], []),
            'any': ([('name', '=', tag1.name)], [('name', '=', False)], []),
            'not any': ([('name', '=', tag1.name)], [('name', '=', False)], []),
        })

        company0 = self.env.ref('base.main_company')
        company1 = self.env['res.company'].create({'name': 'A1', 'parent_id': company0.id})
        company2 = self.env['res.company'].create({'name': 'B1', 'parent_id': company1.id})

        company1.partner_id.parent_id = company0.partner_id
        company2.partner_id.parent_id = company1.partner_id
        self.env.invalidate_all()
        test_field('company_id', [company1.id, company2.id], {
            'child_of': (company0.id, company1.id, company2.id),
            'parent_of': (company0.id, company1.id, company2.id),
        })
        test_field('partner_id', [company1.id, company2.id], {
            'child_of': (company0.partner_id.id, company1.partner_id.id, company2.partner_id.id),
            'parent_of': (company0.partner_id.id, company1.partner_id.id, company2.partner_id.id),
        })

    def test_29_company_dependent_html(self):
        company0 = self.env.ref('base.main_company')
        company1 = self.env['res.company'].create({'name': 'A'})
        company2 = self.env['res.company'].create({'name': 'B'})

        user0 = self.env['res.users'].create({
            'name': 'Foo', 'login': 'foo', 'company_id': company0.id,
            'company_ids': [Command.set([company0.id, company1.id, company2.id])]})
        user1 = self.env['res.users'].create({
            'name': 'Bar', 'login': 'bar', 'company_id': company1.id,
            'company_ids': [Command.set([company0.id, company1.id, company2.id])]})
        user2 = self.env['res.users'].create({
            'name': 'Baz', 'login': 'baz', 'company_id': company2.id,
            'company_ids': [Command.set([company0.id, company1.id, company2.id])]})

        some_ugly_html_0 = """<p>Oops this should maybe be sanitized
% if object.some_field and not object.oriented:
<table>
    % if object.other_field:
    <tr style="margin: 0px; border: 10px solid black;">
        ${object.mako_thing}
        <td>
    </tr>
    <tr class="custom_class">
        This is some html.
    </tr>
    % endif
    <tr>
%if object.dummy_field:
        <p>user0</p>
%endif"""

        some_ugly_html_1 = """<p>Oops this should maybe be sanitized
% if object.some_field and not object.oriented:
<table>
    % if object.other_field:
    <tr style="margin: 0px; border: 10px solid black;">
        ${object.mako_thing}
        <td>
    </tr>
    <tr class="custom_class">
        This is some html.
    </tr>
    % endif
    <tr>
%if object.dummy_field:
        <p>user1</p>
%endif"""

        record = self.env['test_orm.company'].create({
            'html1': some_ugly_html_0,
            'html2': some_ugly_html_0,
        })

        self.assertEqual(record.with_user(user0).html1, some_ugly_html_0, 'Error in HTML field: content was sanitized but field has sanitize=False')
        self.assertEqual(record.with_user(user1).html1, False)
        self.assertEqual(record.with_user(user2).html1, False)

        # sanitize should have closed tags left open in the original html for user0
        self.assertIn('</table>', record.with_user(user0).html2, 'Error in HTML field: content does not seem to have been sanitized despise sanitize=True')
        self.assertIn('</td>', record.with_user(user0).html2, 'Error in HTML field: content does not seem to have been sanitized despise sanitize=True')
        self.assertNotIn('<tr class="', record.with_user(user0).html2, 'Class attr should have been stripped')
        self.assertNotIn('<tr style="', record.with_user(user0).html2, 'Style attr should have been stripped')

        record.with_user(user1).write({
            'html1': some_ugly_html_1,
            'html2': some_ugly_html_1,
        })

        self.assertEqual(record.with_user(user0).html1, some_ugly_html_0, 'Error in HTML field: content was sanitized but field has sanitize=False')
        self.assertEqual(record.with_user(user1).html1, some_ugly_html_1, 'Error in HTML field: content was sanitized but field has sanitize=False')
        self.assertEqual(record.with_user(user2).html1, False)

        # sanitize should have closed tags left open in the original html for user1
        self.assertIn('</table>', record.with_user(user1).html2, 'Error in HTML field: content does not seem to have been sanitized despise sanitize=True')
        self.assertIn('</td>', record.with_user(user1).html2, 'Error in HTML field: content does not seem to have been sanitized despise sanitize=True')
        self.assertNotIn('<tr class="', record.with_user(user1).html2, 'Class attr should have been stripped')
        self.assertNotIn('<tr style="', record.with_user(user1).html2, 'Style attr should have been stripped')

    def test_30_read(self):
        """ test computed fields as returned by read(). """
        discussion = self.env.ref('test_orm.discussion_0')

        for message in discussion.messages:
            display_name = message.display_name
            size = message.size

            data = message.read(['display_name', 'size'])[0]
            self.assertEqual(data['display_name'], display_name)
            self.assertEqual(data['size'], size)

    def test_31_prefetch(self):
        """ test prefetch of records handle AccessError """
        Category = self.env['test_orm.category']
        cat1 = Category.create({'name': 'NOACCESS'})
        cat2 = Category.create({'name': 'ACCESS', 'parent': cat1.id})
        cats = cat1 + cat2

        self.env.clear()

        cat1, cat2 = cats
        self.assertEqual(cat2.name, 'ACCESS')
        # both categories should be ready for prefetching
        self.assertItemsEqual(cat2._prefetch_ids, cats.ids)
        # but due to our (lame) overwrite of `read`, it should not forbid us to read records we have access to
        self.assertFalse(cat2.discussions)
        self.assertEqual(cat2.parent, cat1)
        with self.assertRaises(AccessError):
            cat1.name

    def test_32_prefetch_missing_error(self):
        """ Test that prefetching non-column fields works in the presence of deleted records. """
        Discussion = self.env['test_orm.discussion']

        # add an ir.rule that forces reading field 'name'
        self.env['ir.model.access'].create({
            'name': 'demo',
            'model_id': self.env['ir.model']._get(Discussion.categories._name).id,
            'group_id': self.env.ref('base.group_user').id,
            'perm_read': True,
        })
        self.env['ir.rule'].create({
            'model_id': self.env['ir.model']._get(Discussion._name).id,
            'groups': [self.env.ref('base.group_user').id],
            'domain_force': "[('name', '!=', 'Super Secret discution')]",
        })

        records = Discussion.with_user(self.user_demo).create([
            {'name': 'EXISTING'},
            {'name': 'MISSING'},
        ])

        # unpack to keep the prefetch on each recordset
        existing, deleted = records
        self.assertEqual(existing._prefetch_ids, records._ids)

        # this invalidates the caches but the prefetching remains the same
        deleted.unlink()

        # this should not trigger a MissingError
        existing.categories

        # invalidate 'categories' for the assertQueryCount
        records.invalidate_model(['categories'])
        with self.assertQueryCount(4):
            # <categories>.__get__(existing)
            #  -> records._fetch_field(<categories>)
            #      -> records.fetch(['categories'])
            #          -> records.check_access('read')
            #              -> records._check_access('read')
            #                  -> records.sudo().filtered_domain(...)
            #                      -> <name>.__get__(existing)
            #                          -> records._fetch_field(<name>)
            #                              -> records.fetch(['name', ...])
            #                                  -> ONE QUERY to read ['name', ...] of records
            #                                  -> ONE QUERY for deleted.exists() / code: forbidden = missing.exists()
            #          -> ONE QUERY for records.exists() / code: self = self.exists()
            #          -> ONE QUERY to read the many2many of existing
            existing.categories

        # this one must trigger a MissingError
        with self.assertRaises(MissingError):
            deleted.categories

        # special case: should not fail
        Discussion.browse([None]).read(['categories'])

    def test_40_real_vs_new(self):
        """ test field access on new records vs real records. """
        Model = self.env['test_orm.category']
        real_record = Model.create({'name': 'Foo'})
        new_origin = Model.new({'name': 'Bar'}, origin=real_record)
        new_record = Model.new({'name': 'Baz'})

        # non-computed non-stored field: default value
        real_record = real_record.with_context(default_dummy='WTF')
        new_origin = new_origin.with_context(default_dummy='WTF')
        new_record = new_record.with_context(default_dummy='WTF')
        self.assertEqual(real_record.dummy, 'WTF')
        self.assertEqual(new_origin.dummy, 'WTF')
        self.assertEqual(new_record.dummy, 'WTF')

        # non-computed stored field: origin or default if no origin
        real_record = real_record.with_context(default_color=42)
        new_origin = new_origin.with_context(default_color=42)
        new_record = new_record.with_context(default_color=42)
        self.assertEqual(real_record.color, 0)
        self.assertEqual(new_origin.color, 0)
        self.assertEqual(new_record.color, 42)

        # computed non-stored field: always computed
        self.assertEqual(real_record.display_name, 'Foo')
        self.assertEqual(new_origin.display_name, 'Bar')
        self.assertEqual(new_record.display_name, 'Baz')

        # computed stored field: origin or computed if no origin
        Model = self.env['test_orm.recursive']
        real_record = Model.create({'name': 'Foo'})
        new_origin = Model.new({'name': 'Bar'}, origin=real_record)
        new_record = Model.new({'name': 'Baz'})
        self.assertEqual(real_record.display_name, 'Foo')
        self.assertEqual(new_origin.display_name, 'Bar')
        self.assertEqual(new_record.display_name, 'Baz')

        # computed stored field with recomputation: always computed
        real_record.name = 'Fool'
        new_origin.name = 'Barr'
        new_record.name = 'Bazz'
        self.assertEqual(real_record.display_name, 'Fool')
        self.assertEqual(new_origin.display_name, 'Barr')
        self.assertEqual(new_record.display_name, 'Bazz')

    def test_40_new_defaults(self):
        """ Test new records with defaults. """
        user = self.env.user
        discussion = self.env.ref('test_orm.discussion_0')

        # create a new message; fields have their default value if not given
        new_msg = self.env['test_orm.message'].new({'body': "XXX"})
        self.assertFalse(new_msg.id)
        self.assertEqual(new_msg.body, "XXX")
        self.assertEqual(new_msg.author, user)

        # assign some fields; should have no side effect
        new_msg.discussion = discussion
        new_msg.body = "YYY"
        self.assertEqual(new_msg.discussion, discussion)
        self.assertEqual(new_msg.body, "YYY")
        self.assertNotIn(new_msg, discussion.messages)

        # check computed values of fields
        self.assertEqual(new_msg.name, "[%s] %s" % (discussion.name, user.name))
        self.assertEqual(new_msg.size, 3)

        # extra tests for x2many fields with default
        cat1 = self.env['test_orm.category'].create({'name': "Cat1"})
        cat2 = self.env['test_orm.category'].create({'name': "Cat2"})
        discussion = discussion.with_context(default_categories=[Command.link(cat1.id)])
        # no value gives the default value
        new_disc = discussion.new({'name': "Foo"})
        self.assertEqual(new_disc.categories._origin, cat1)
        # value overrides default value
        new_disc = discussion.new({'name': "Foo", 'categories': [Command.link(cat2.id)]})
        self.assertEqual(new_disc.categories._origin, cat2)

    def test_40_new_fields(self):
        """ Test new records with relational fields. """
        # create a new discussion with all kinds of relational fields
        msg0 = self.env['test_orm.message'].create({'body': "XXX"})
        msg1 = self.env['test_orm.message'].create({'body': "WWW"})
        cat0 = self.env['test_orm.category'].create({'name': 'AAA'})
        cat1 = self.env['test_orm.category'].create({'name': 'DDD'})
        new_disc = self.env['test_orm.discussion'].new({
            'name': "Stuff",
            'moderator': self.env.uid,
            'messages': [
                Command.link(msg0.id),
                Command.link(msg1.id), Command.update(msg1.id, {'body': "YYY"}),
                Command.create({'body': "ZZZ"}),
            ],
            'categories': [
                Command.link(cat0.id),
                Command.link(cat1.id), Command.update(cat1.id, {'name': "BBB"}),
                Command.create({'name': "CCC"}),
            ],
        })
        self.assertFalse(new_disc.id)

        # many2one field values are actual records
        self.assertEqual(new_disc.moderator.id, self.env.uid)

        # x2many fields values are new records
        new_msg0, new_msg1, new_msg2 = new_disc.messages
        self.assertFalse(new_msg0.id)
        self.assertFalse(new_msg1.id)
        self.assertFalse(new_msg2.id)

        new_cat0, new_cat1, new_cat2 = new_disc.categories
        self.assertFalse(new_cat0.id)
        self.assertFalse(new_cat1.id)
        self.assertFalse(new_cat2.id)

        # the x2many has its inverse field set
        self.assertEqual(new_msg0.discussion, new_disc)
        self.assertEqual(new_msg1.discussion, new_disc)
        self.assertEqual(new_msg2.discussion, new_disc)

        self.assertFalse(msg0.discussion)
        self.assertFalse(msg1.discussion)

        self.assertEqual(new_cat0.discussions, new_disc)    # add other discussions
        self.assertEqual(new_cat1.discussions, new_disc)
        self.assertEqual(new_cat2.discussions, new_disc)

        self.assertNotIn(new_disc, cat0.discussions)
        self.assertNotIn(new_disc, cat1.discussions)

        # new lines are connected to their origin
        self.assertEqual(new_msg0._origin, msg0)
        self.assertEqual(new_msg1._origin, msg1)
        self.assertFalse(new_msg2._origin)

        self.assertEqual(new_cat0._origin, cat0)
        self.assertEqual(new_cat1._origin, cat1)
        self.assertFalse(new_cat2._origin)

        # the field values are either specific, or the same as the origin
        self.assertEqual(new_msg0.body, "XXX")
        self.assertEqual(new_msg1.body, "YYY")
        self.assertEqual(new_msg2.body, "ZZZ")

        self.assertEqual(msg0.body, "XXX")
        self.assertEqual(msg1.body, "WWW")

        self.assertEqual(new_cat0.name, "AAA")
        self.assertEqual(new_cat1.name, "BBB")
        self.assertEqual(new_cat2.name, "CCC")

        self.assertEqual(cat0.name, "AAA")
        self.assertEqual(cat1.name, "DDD")

        # special case for many2one fields that define _inherits
        new_email = self.env['test_orm.emailmessage'].new({'body': "XXX"})
        self.assertFalse(new_email.id)
        self.assertTrue(new_email.message)
        self.assertFalse(new_email.message.id)
        self.assertEqual(new_email.body, "XXX")

        new_email = self.env['test_orm.emailmessage'].new({'message': msg0.id})
        self.assertFalse(new_email.id)
        self.assertFalse(new_email._origin)
        self.assertFalse(new_email.message.id)
        self.assertEqual(new_email.message._origin, msg0)
        self.assertEqual(new_email.body, "XXX")

        # check that this does not generate an infinite recursion
        new_disc._convert_to_write(new_disc._cache)

    def test_40_new_convert_to_write(self):
        new_disc = self.env['test_orm.discussion'].new({
            'name': "Stuff",
            'moderator': self.env.uid,
            'participants': [(6, 0, self.env.user.ids)],
        })
        # Put the user groups in the cache of the new record
        new_disc.participants.group_ids

        # Check that the groups in the cache are not returned by convert_to_write
        # because no real change happened, the values are identical except that
        # self.env.user.group_ids._ids = (Id1, Id2, ...) whereas
        # new_disc.participants.group_ids._ids = (NewId(origin=Id1), NewId(origin=Id2), ...)
        field = new_disc._fields.get("participants")
        # make sure that there is no inverse field for discussions on res_users,
        # as the test depends on it
        self.assertFalse(new_disc.pool.field_inverses[field])
        convert = field.convert_to_write(new_disc["participants"], new_disc)
        self.assertEqual(convert, [(6, 0, self.env.user.ids)])

    def test_40_new_inherited_fields(self):
        """ Test the behavior of new records with inherited fields. """
        email = self.env['test_orm.emailmessage'].new({'body': 'XXX'})
        self.assertEqual(email.body, 'XXX')
        self.assertEqual(email.message.body, 'XXX')

        email.body = 'YYY'
        self.assertEqual(email.body, 'YYY')
        self.assertEqual(email.message.body, 'YYY')

        email.message.body = 'ZZZ'
        self.assertEqual(email.body, 'ZZZ')
        self.assertEqual(email.message.body, 'ZZZ')

    def test_40_new_ref_origin(self):
        """ Test the behavior of new records with ref/origin. """
        Discussion = self.env['test_orm.discussion']
        new = Discussion.new

        # new records with identical/different refs
        xs = new() + new(ref='a') + new(ref='b') + new(ref='b')
        self.assertEqual([x == y for x in xs for y in xs], [
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 1,
            0, 0, 1, 1,
        ])
        for x in xs:
            self.assertFalse(x._origin)

        # new records with identical/different origins
        a, b = Discussion.create([{'name': "A"}, {'name': "B"}])
        xs = new() + new(origin=a) + new(origin=b) + new(origin=b)
        self.assertEqual([x == y for x in xs for y in xs], [
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 1,
            0, 0, 1, 1,
        ])
        self.assertFalse(xs[0]._origin)
        self.assertEqual(xs[1]._origin, a)
        self.assertEqual(xs[2]._origin, b)
        self.assertEqual(xs[3]._origin, b)
        self.assertEqual(xs._origin, a + b + b)
        self.assertEqual(xs._origin._origin, a + b + b)

        # new records with refs and origins
        x1 = new(ref='a')
        x2 = new(origin=b)
        self.assertNotEqual(x1, x2)

        # new discussion based on existing discussion
        disc = self.env.ref('test_orm.discussion_0')
        new_disc = disc.new(origin=disc)
        self.assertFalse(new_disc.id)
        self.assertEqual(new_disc._origin, disc)
        self.assertEqual(new_disc.name, disc.name)
        # many2one field
        self.assertEqual(new_disc.moderator, disc.moderator)
        # one2many field
        self.assertTrue(new_disc.messages)
        self.assertNotEqual(new_disc.messages, disc.messages)
        self.assertEqual(new_disc.messages._origin, disc.messages)
        # many2many field
        self.assertTrue(new_disc.participants)
        self.assertNotEqual(new_disc.participants, disc.participants)
        self.assertEqual(new_disc.participants._origin, disc.participants)

        # provide many2one field as a dict of values; the value is a new record
        # with the given 'id' as origin (if given, of course)
        new_msg = disc.messages.new({
            'discussion': {'name': disc.name},
        })
        self.assertTrue(new_msg.discussion)
        self.assertFalse(new_msg.discussion.id)
        self.assertFalse(new_msg.discussion._origin)

        new_msg = disc.messages.new({
            'discussion': {'name': disc.name, 'id': disc.id},
        })
        self.assertTrue(new_msg.discussion)
        self.assertFalse(new_msg.discussion.id)
        self.assertEqual(new_msg.discussion._origin, disc)

        # check convert_to_write
        tag = self.env['test_orm.multi.tag'].create({'name': 'Foo'})
        rec = self.env['test_orm.multi'].create({
            'lines': [(0, 0, {'tags': [(6, 0, tag.ids)]})],
        })
        new = rec.new(origin=rec)
        self.assertEqual(new.lines.tags._origin, rec.lines.tags)
        vals = new._convert_to_write(new._cache)
        self.assertEqual(vals['lines'], [(6, 0, rec.lines.ids)])

    def test_41_new_compute(self):
        """ Check recomputation of fields on new records. """
        move = self.env['test_orm.move'].create({
            'line_ids': [Command.create({'quantity': 1}), Command.create({'quantity': 1})],
        })
        self.env.flush_all()
        line = move.line_ids[0]

        new_move = move.new(origin=move)
        new_line = line.new(origin=line)

        # move_id is fetched from origin
        self.assertEqual(new_line.move_id, move)
        self.assertEqual(new_move.quantity, 2)
        self.assertEqual(move.quantity, 2)

        # modifying new_line must trigger recomputation on new_move, even if
        # new_line.move_id is not new_move!
        new_line.quantity = 2
        self.assertEqual(new_line.move_id, move)
        self.assertEqual(new_move.quantity, 3)
        self.assertEqual(move.quantity, 2)

    def test_41_new_one2many(self):
        """ Check command on one2many field on new record. """
        move = self.env['test_orm.move'].create({})
        line = self.env['test_orm.move_line'].create({'move_id': move.id, 'quantity': 1})
        self.env.flush_all()

        new_move = move.new(origin=move)
        new_line = line.new(origin=line)
        self.assertEqual(new_move.line_ids, new_line)

        # drop line, and create a new one
        new_move.line_ids = [Command.delete(new_line.id), Command.create({'quantity': 2})]
        self.assertEqual(len(new_move.line_ids), 1)
        self.assertFalse(new_move.line_ids.id)
        self.assertEqual(new_move.line_ids.quantity, 2)

        # assign line to new move without origin
        new_move = move.new()
        new_move.line_ids = line
        self.assertFalse(new_move.line_ids.id)
        self.assertEqual(new_move.line_ids._origin, line)
        self.assertEqual(new_move.line_ids.move_id, new_move)

    def test_41_new_many2many(self):
        group = self.env['test_orm.group'].create({})
        user0 = self.env['test_orm.user'].create({'group_ids': [Command.link(group.id)]})
        new_user0 = user0.new(origin=user0)
        new_group = group.new(origin=group)

        self.env.invalidate_all()

        # creating new_user1 shoud not fetch new_group.all_user_ids, which is the
        # inverse of field new_user1.group_ids
        with self.assertQueryCount(0):
            new_user1 = self.env['test_orm.user'].new({'group_ids': [Command.link(group.id)]})
            self.assertEqual(new_user1.group_ids, new_group)

        # accessing new_group.all_user_ids should fetch group.all_user_ids and patch
        # new_group.all_user_ids
        with self.assertQueryCount(1):
            self.assertEqual(new_group.user_ids, new_user0 + new_user1)

        # creating new_user2 should patch new_group.all_user_ids immediately, since
        # it is in cache
        with self.assertQueryCount(0):
            new_user2 = self.env['test_orm.user'].new({'group_ids': [Command.link(group.id)]})
            self.assertEqual(new_user2.group_ids, new_group)
            self.assertEqual(new_group.user_ids, new_user0 + new_user1 + new_user2)

        # the patches on new_group.all_user_ids should not have changed group.all_user_ids
        self.assertEqual(group.user_ids, user0)

    @mute_logger('odoo.addons.base.models.ir_model')
    def test_41_new_related(self):
        """ test the behavior of related fields starting on new records. """
        # make discussions unreadable for demo user
        access = self.env.ref('test_orm.access_discussion')
        access.write({'perm_read': False})

        # create an environment for demo user
        env = self.env(user=self.user_demo)
        self.assertEqual(env.user.login, "demo")

        # create a new message as demo user
        discussion = self.env.ref('test_orm.discussion_0')
        message = env['test_orm.message'].new({'discussion': discussion})
        self.assertEqual(message.discussion, discussion)

        # read the related field discussion_name
        self.assertEqual(message.discussion.env, env)
        self.assertEqual(message.discussion_name, discussion.name)
        # DLE P75: message.discussion.name is put in the cache as sudo thanks to the computation of message.discussion_name
        # As we decided that now if we had the chance to access the value at some point in the code, and that it was stored in the cache
        # it's not a big deal to no longer raise the accesserror, as we had the chance to get the value at some point
        # with self.assertRaises(AccessError):
        #     message.discussion.name

    @mute_logger('odoo.addons.base.models.ir_model')
    def test_42_new_related(self):
        """ test the behavior of related fields traversing new records. """
        # make discussions unreadable for demo user
        access = self.env.ref('test_orm.access_discussion')
        access.write({'perm_read': False})

        # create an environment for demo user
        env = self.env(user=self.user_demo)
        self.assertEqual(env.user.login, "demo")

        # create a new discussion and a new message as demo user
        discussion = env['test_orm.discussion'].new({'name': 'Stuff'})
        message = env['test_orm.message'].new({'discussion': discussion})
        self.assertEqual(message.discussion, discussion)

        # read the related field discussion_name
        self.assertNotEqual(message.sudo().env, message.env)
        self.assertEqual(message.discussion_name, discussion.name)

    def test_43_new_related(self):
        """ test the behavior of one2many related fields """
        partner = self.env['res.partner'].create({
            'name': 'Foo',
            'child_ids': [Command.create({'name': 'Bar'})],
        })
        multi = self.env['test_orm.multi'].new()
        multi.partner = partner
        self.assertEqual(multi.partners.mapped('name'), ['Bar'])

    def test_50_defaults(self):
        """ test default values. """
        fields = ['discussion', 'body', 'author', 'size']
        defaults = self.env['test_orm.message'].default_get(fields)
        self.assertEqual(defaults, {'author': self.env.uid})

        defaults = self.env['test_orm.mixed'].default_get(['number'])
        self.assertEqual(defaults, {'number': 3.14})

    def test_50_search_many2one(self):
        """ test search through a path of computed fields"""
        messages = self.env['test_orm.message'].search(
            [('author_partner.name', '=', 'Marc Demo')])
        self.assertEqual(messages, self.env.ref('test_orm.message_0_1'))

    def test_51_search_many2one_ordered(self):
        """ test search on many2one ordered by id """
        with self.assertQueries(['''
            SELECT "test_orm_message"."id" FROM "test_orm_message"
            WHERE "test_orm_message"."active" IS TRUE
            ORDER BY  "test_orm_message"."discussion"
        ''']):
            self.env['test_orm.message'].search([], order='discussion')

    def test_52_search_many2one_active_test(self):
        Model = self.env['test_orm.model_active_field']

        active_parent = Model.create({'name': 'Parent'})
        child_of_active = Model.create({'parent_id': active_parent.id})

        inactive_parent = Model.create({'name': 'Parent', 'active': False})
        child_of_inactive = Model.create({'parent_id': inactive_parent.id})

        self.assertEqual(
            self._search(Model, [('parent_id.name', '=', 'Parent')]),
            child_of_active + child_of_inactive,
        )
        self.assertEqual(
            self._search(Model, [('parent_id', '=', 'Parent')]),
            child_of_active + child_of_inactive,
        )
        self.assertEqual(
            Model.search([('id', 'child_of', active_parent.id)]),
            active_parent + child_of_active,
        )
        # weird semantics: active_parent is in both results but doesn't have a parent_id
        self.assertEqual(
            self._search(Model, [('parent_id', 'child_of', active_parent.id)]),
            active_parent + child_of_active,
        )
        self.assertEqual(
            self._search(Model, [('parent_id', 'child_of', 'Parent')]),
            active_parent + child_of_active + child_of_inactive,
        )

    def test_53_boolean_query(self):
        Model = self.env['test_orm.model_active_field']

        with self.assertQueries(["""
            SELECT "test_orm_model_active_field"."id"
            FROM "test_orm_model_active_field"
            WHERE "test_orm_model_active_field"."active" IS TRUE
            ORDER BY "test_orm_model_active_field"."id"
        """, """
            SELECT "test_orm_model_active_field"."id"
            FROM "test_orm_model_active_field"
            WHERE "test_orm_model_active_field"."active" IS NOT TRUE
            ORDER BY "test_orm_model_active_field"."id"
        """]):
            Model.search([('active', '=', True)])
            Model.search([('active', '=', False)])

        with self.assertQueries(["""
            SELECT "test_orm_model_active_field"."id"
            FROM "test_orm_model_active_field"
            ORDER BY "test_orm_model_active_field"."id"
        """]):
            Model.search([('active', 'in', [True, False])])
        with self.assertQueries([]):
            Model.search([('active', 'not in', [True, False])])

    def test_54_not_null_id_query(self):
        # Test at post_install since not_null_fields is only loaded at the end of the registry
        Model = self.env['test_orm.model_active_field'].with_context(active_test=False)

        self.patch(self.env.registry, 'not_null_fields', {Model._fields['id']})

        with self.assertQueries(["""
            SELECT "test_orm_model_active_field"."id"
            FROM "test_orm_model_active_field"
            WHERE "test_orm_model_active_field"."id" NOT IN %s
            ORDER BY "test_orm_model_active_field"."id"
        """]):
            Model.search([('id', '!=', 1)])
            Model.search([('id', '=', False)])  # No query

    def test_60_one2many_domain(self):
        """ test the cache consistency of a one2many field with a domain """
        discussion = self.env.ref('test_orm.discussion_0')
        message = discussion.messages[0]
        self.assertNotIn(message, discussion.important_messages)

        message.important = True
        self.assertIn(message, discussion.important_messages)

        # writing on very_important_messages should call its domain method
        self.assertIn(message, discussion.very_important_messages)
        discussion.write({'very_important_messages': [Command.clear()]})
        self.assertFalse(discussion.very_important_messages)
        self.assertFalse(message.exists())

    def test_60_many2many_domain(self):
        """ test the cache consistency of a many2many field with a domain """
        tag = self.env['test_orm.multi.tag'].create({'name': 'bar'})
        record = self.env['test_orm.multi'].create({'tags': tag.ids})
        self.env.flush_all()
        self.env.invalidate_all()

        self.assertEqual(type(record).tags.domain, [('name', 'ilike', 'a')])

        # the tag is in the many2many
        self.assertIn(tag, record.tags)

        # modify the tag; it should not longer be in the many2many
        tag.name = 'foo'
        self.assertNotIn(tag, record.tags)

        # modify again the tag; it should be back in the many2many
        tag.name = 'baz'
        self.assertIn(tag, record.tags)

    def test_70_x2many_write(self):
        discussion = self.env.ref('test_orm.discussion_0')
        # See YTI FIXME
        self.env.invalidate_all()

        Message = self.env['test_orm.message']
        # There must be 3 messages, 0 important
        self.assertEqual(len(discussion.messages), 3)
        self.assertEqual(len(discussion.important_messages), 0)
        self.assertEqual(len(discussion.very_important_messages), 0)
        discussion.important_messages = [Command.create({
            'body': 'What is the answer?',
            'important': True,
        })]
        # There must be 4 messages, 1 important
        self.assertEqual(len(discussion.messages), 4)
        self.assertEqual(len(discussion.important_messages), 1)
        self.assertEqual(len(discussion.very_important_messages), 1)
        discussion.very_important_messages |= Message.new({
            'body': '42',
            'important': True,
        })
        # There must be 5 messages, 2 important
        self.assertEqual(len(discussion.messages), 5)
        self.assertEqual(len(discussion.important_messages), 2)
        self.assertEqual(len(discussion.very_important_messages), 2)

    def test_70_relational_inverse(self):
        """ Check the consistency of relational fields with inverse(s). """
        discussion = self.env.ref('test_orm.discussion_0')
        demo_discussion = discussion.with_user(self.user_demo)

        # check that the demo user sees the same messages
        self.assertEqual(demo_discussion.messages, discussion.messages)

        # See YTI FIXME
        self.env.flush_all()
        self.env.invalidate_all()

        # add a message as user demo
        messages = demo_discussion.messages
        message = messages.create({'discussion': discussion.id})
        self.assertEqual(demo_discussion.messages, messages + message)
        self.assertEqual(demo_discussion.messages, discussion.messages)

        # add a message as superuser
        messages = discussion.messages
        message = messages.create({'discussion': discussion.id})
        self.assertEqual(discussion.messages, messages + message)
        self.assertEqual(demo_discussion.messages, discussion.messages)

    def test_71_relational_inverse(self):
        """ Check the consistency of relational fields with inverse(s). """
        move1 = self.env['test_orm.move'].create({})
        move2 = self.env['test_orm.move'].create({})
        line = self.env['test_orm.move_line'].create({'move_id': move1.id})
        self.env.flush_all()
        self.env.invalidate_all()

        line.with_context(prefetch_fields=False).move_id

        # Setting 'move_id' updates the one2many field that is based on it,
        # which has a domain.  Here we check that evaluating the domain does not
        # accidentally override 'move_id' (by prefetch).
        line.move_id = move2
        self.assertEqual(line.move_id, move2)

    def test_72_relational_inverse(self):
        """ Check the consistency of relational fields with inverse(s). """
        move1 = self.env['test_orm.move'].create({})
        move2 = self.env['test_orm.move'].create({})

        # makes sure that line.move_id is flushed before search
        line = self.env['test_orm.move_line'].create({'move_id': move1.id})
        moves = self.env['test_orm.move'].search([('line_ids', 'in', line.id)])
        self.assertEqual(moves, move1)

        # makes sure that line.move_id is flushed before search
        line.move_id = move2
        moves = self.env['test_orm.move'].search([('line_ids', 'in', line.id)])
        self.assertEqual(moves, move2)

    def test_73_relational_inverse(self):
        """ Check the consistency of relational fields with inverse(s). """
        discussion1, discussion2 = self.env['test_orm.discussion'].create([
            {'name': "discussion1"}, {'name': "discussion2"},
        ])
        category1, category2 = self.env['test_orm.category'].create([
            {'name': "category1"}, {'name': "category2"},
        ])

        # assumption: category12 and category21 are in different order, but are
        # in the same order when put in a set()
        category12 = category1 + category2
        category21 = category2 + category1
        self.assertNotEqual(category12.ids, category21.ids)
        self.assertEqual(list(set(category12.ids)), list(set(category21.ids)))

        # make sure discussion1.categories is in cache; the write() below should
        # update the cache of discussion1.categories by appending category12.ids
        discussion1.categories
        category12.write({'discussions': [Command.link(discussion1.id)]})
        self.assertEqual(discussion1.categories.ids, category12.ids)

        # make sure discussion2.categories is in cache; the write() below should
        # update the cache of discussion2.categories by appending category21.ids
        discussion2.categories
        category21.write({'discussions': [Command.link(discussion2.id)]})
        self.assertEqual(discussion2.categories.ids, category21.ids)

    def test_80_copy(self):
        discussion = self.env.ref('test_orm.discussion_0')
        message = self.env.ref('test_orm.message_0_0')
        message1 = self.env.ref('test_orm.message_0_1')

        email = self.env.ref('test_orm.emailmessage_0_0')
        self.assertEqual(email.message, message)

        self.env['res.lang']._activate_lang('fr_FR')

        # set a translation for message.label
        email.with_context(lang='fr_FR').label = "bonjour"
        self.assertEqual(message.with_context(lang='fr_FR').label, 'bonjour')
        self.assertFalse(message1.label)

        # setting the parent record should not copy its translations
        email.copy({'message': message1.id})
        self.assertEqual(message.with_context(lang='fr_FR').label, 'bonjour')
        self.assertFalse(message1.label)

        # setting a one2many should not copy translations on the lines
        discussion.copy({'messages': [Command.set(message1.ids)]})
        self.assertEqual(message.with_context(lang='fr_FR').label, 'bonjour')
        self.assertFalse(message1.label)

    def test_85_binary_guess_zip(self):
        from odoo.addons.base.tests.test_mimetypes import ZIP  # noqa: PLC0415
        # Regular ZIP files can be uploaded by non-admin users
        self.env['test_orm.binary_svg'].with_user(self.user_demo).create({
            'name': 'Test without attachment',
            'image_wo_attachment': base64.b64decode(ZIP),
        })

    def test_86_text_base64_guess_svg(self):
        from odoo.addons.base.tests.test_mimetypes import SVG  # noqa: PLC0415
        with self.assertRaises(UserError) as e:
            self.env['test_orm.binary_svg'].with_user(self.user_demo).create({
                'name': 'Test without attachment',
                'image_wo_attachment': SVG.decode("utf-8"),
            })
        self.assertEqual(e.exception.args[0], 'Only admins can upload SVG files.')

    def test_90_binary_svg(self):
        from odoo.addons.base.tests.test_mimetypes import SVG  # noqa: PLC0415
        # This should work without problems
        self.env['test_orm.binary_svg'].create({
            'name': 'Test without attachment',
            'image_wo_attachment': SVG,
        })
        # And this gives error
        with self.assertRaises(UserError):
            self.env['test_orm.binary_svg'].with_user(
                self.user_demo,
            ).create({
                'name': 'Test without attachment',
                'image_wo_attachment': SVG,
            })

    def test_91_binary_svg_attachment(self):
        from odoo.addons.base.tests.test_mimetypes import SVG  # noqa: PLC0415
        # This doesn't neuter SVG with admin
        record = self.env['test_orm.binary_svg'].create({
            'name': 'Test without attachment',
            'image_attachment': SVG,
        })
        attachment = self.env['ir.attachment'].search([
            ('res_model', '=', record._name),
            ('res_field', '=', 'image_attachment'),
            ('res_id', '=', record.id),
        ])
        self.assertEqual(attachment.mimetype, 'image/svg+xml')
        # ...but this should be neutered with demo user
        record = self.env['test_orm.binary_svg'].with_user(
            self.user_demo,
        ).create({
            'name': 'Test without attachment',
            'image_attachment': SVG,
        })
        attachment = self.env['ir.attachment'].search([
            ('res_model', '=', record._name),
            ('res_field', '=', 'image_attachment'),
            ('res_id', '=', record.id),
        ])
        self.assertEqual(attachment.mimetype, 'text/plain')

    def test_92_binary_self_avatar_svg(self):
        from odoo.addons.base.tests.test_mimetypes import SVG  # noqa: PLC0415
        demo_user = self.user_demo
        # User demo changes his own avatar
        demo_user.with_user(demo_user).image_1920 = SVG
        # The SVG file should have been neutered
        attachment = self.env['ir.attachment'].search([
            ('res_model', '=', demo_user.partner_id._name),
            ('res_field', '=', 'image_1920'),
            ('res_id', '=', demo_user.partner_id.id),
        ])
        self.assertEqual(attachment.mimetype, 'text/plain')

    def test_93_monetary_related(self):
        """ Check the currency field on related monetary fields. """
        # check base field
        model = self.env['test_orm.monetary_base']
        field = model._fields['amount']
        self.assertEqual(field.get_currency_field(model), 'base_currency_id')

        # related fields must use the field 'currency_id' or 'x_currency_id'
        model = self.env['test_orm.monetary_related']
        field = model._fields['amount']
        self.assertEqual(field.related, 'monetary_id.amount')
        self.assertEqual(field.get_currency_field(model), 'currency_id')

        model = self.env['test_orm.monetary_custom']
        field = model._fields['x_amount']
        self.assertEqual(field.related, 'monetary_id.amount')
        self.assertEqual(field.get_currency_field(model), 'x_currency_id')

        # inherited field must use the same field as its parent field
        model = self.env['test_orm.monetary_inherits']
        field = model._fields['amount']
        self.assertEqual(field.related, 'monetary_id.amount')
        self.assertEqual(field.get_currency_field(model), 'base_currency_id')

    def test_94_image(self):
        f = io.BytesIO()
        Image.new('RGB', (4000, 2000), '#4169E1').save(f, 'PNG')
        f.seek(0)
        image_w = base64.b64encode(f.read())

        f = io.BytesIO()
        Image.new('RGB', (2000, 4000), '#4169E1').save(f, 'PNG')
        f.seek(0)
        image_h = base64.b64encode(f.read())

        record = self.env['test_orm.model_image'].create({
            'name': 'image',
            'image': image_w,
            'image_128': image_w,
        })

        # test create (no resize)
        self.assertEqual(record.image, image_w)
        # test create (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_128))).size, (128, 64))
        # test create related store (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (512, 256))
        # test create related no store (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (256, 128))
        # test create related store on column (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (64, 32))

        record.write({
            'image': image_h,
            'image_128': image_h,
        })

        # test write (no resize)
        self.assertEqual(record.image, image_h)
        # test write (resize, height limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_128))).size, (64, 128))
        # test write related store (resize, height limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (256, 512))
        # test write related no store (resize, height limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (128, 256))
        # test write related store on column (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (32, 64))

        record = self.env['test_orm.model_image'].create({
            'name': 'image',
            'image': image_h,
            'image_128': image_h,
        })

        # test create (no resize)
        self.assertEqual(record.image, image_h)
        # test create (resize, height limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_128))).size, (64, 128))
        # test create related store (resize, height limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (256, 512))
        # test create related no store (resize, height limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (128, 256))
        # test create related store on column (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (32, 64))

        record.write({
            'image': image_w,
            'image_128': image_w,
        })

        # test write (no resize)
        self.assertEqual(record.image, image_w)
        # test write (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_128))).size, (128, 64))
        # test write related store (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (512, 256))
        # test write related store (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (256, 128))
        # test write related store on column (resize, width limited)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (64, 32))

        # test create inverse store
        record = self.env['test_orm.model_image'].create({
            'name': 'image',
            'image_512': image_w,
        })
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (512, 256))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image))).size, (4000, 2000))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (256, 128))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (64, 32))
        # test write inverse store
        record.write({
            'image_512': image_h,
        })
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (256, 512))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image))).size, (2000, 4000))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (128, 256))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (32, 64))

        # test create inverse no store
        record = self.env['test_orm.model_image'].with_context(image_no_postprocess=True).create({
            'name': 'image',
            'image_256': image_w,
        })
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (512, 256))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image))).size, (4000, 2000))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (256, 128))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (64, 32))
        # test write inverse no store
        record.write({
            'image_256': image_h,
        })
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (256, 512))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image))).size, (2000, 4000))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (128, 256))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (32, 64))

        # test create inverse stored column
        record = self.env['test_orm.model_image'].with_context(image_no_postprocess=True).create({
            'name': 'image',
            'image_64': image_w,
        })
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (512, 256))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image))).size, (4000, 2000))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (256, 128))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (64, 32))
        # test write inverse stored column
        record.write({
            'image_64': image_h,
        })
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_512))).size, (256, 512))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image))).size, (2000, 4000))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_256))).size, (128, 256))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(record.image_64))).size, (32, 64))

        # test bin_size
        record_bin_size = record.with_context(bin_size=True)
        self.assertEqual(record_bin_size.image, b'31.54 Kb')
        self.assertEqual(record_bin_size.image_512, b'1.02 Kb')
        self.assertEqual(record_bin_size.image_256, b'424.00 bytes')
        # non-attachment binary fields: value returned as str in a different
        # form, because coming from PostgreSQL instead of filestore
        self.assertEqual(record_bin_size.image_64, '148 bytes')

        # ensure image_data_uri works (value must be bytes and not string)
        self.assertEqual(record.image_256[:8], b'iVBORw0K')
        self.assertEqual(image_data_uri(record.image_256)[:30], 'data:image/png;base64,iVBORw0K')

        # ensure invalid image raises
        with self.assertRaises(UserError):
            record.write({
                'image': 'invalid image',
            })

        # assignment of invalid image on new record does nothing, the value is
        # taken from origin instead (use-case: onchange)
        new_record = record.new(origin=record)
        new_record.image = '31.54 Kb'
        self.assertEqual(record.image, image_h)
        self.assertEqual(new_record.image, image_h)

        # assignment to new record with origin should not do any query
        with self.assertQueryCount(0):
            new_record.image = image_w

    def test_95_binary_bin_size_create(self):
        binary_value = base64.b64encode(b'content')
        binary_size = b'7.00 bytes'

        def assertBinaryValue(record, value):
            for field in ('binary', 'binary_related_store', 'binary_related_no_store'):
                self.assertEqual(record[field], value, f'Incorrect result for {field}')

        # created and first read without context
        record = self.env['test_orm.model_binary'].create({'binary': binary_value})
        record_no_bin_size = record.with_context(bin_size=False)
        record_bin_size = record.with_context(bin_size=True)

        assertBinaryValue(record, binary_value)
        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record_bin_size, binary_size)

        # created and first read with bin_size=False
        record_no_bin_size = self.env['test_orm.model_binary'].with_context(bin_size=False).create({'binary': binary_value})
        record = self.env['test_orm.model_binary'].browse(record.id)
        record_bin_size = record.with_context(bin_size=True)

        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record, binary_value)
        assertBinaryValue(record_bin_size, binary_size)

        # created and first read with bin_size=True
        record_bin_size = self.env['test_orm.model_binary'].with_context(bin_size=True).create({'binary': binary_value})
        record = self.env['test_orm.model_binary'].browse(record.id)
        record_no_bin_size = record.with_context(bin_size=False)

        assertBinaryValue(record_bin_size, binary_size)
        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record, binary_value)

        # created without context and flushed/invalidated with bin_size=True
        record = self.env['test_orm.model_binary'].create({'binary': binary_value})
        record.with_context(bin_size=True).env.invalidate_all()
        record_no_bin_size = record.with_context(bin_size=False)
        record_bin_size = record.with_context(bin_size=True)

        assertBinaryValue(record, binary_value)
        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record_bin_size, binary_size)

        # check computed binary field with arbitrary Python value
        record = self.env['test_orm.model_binary'].create({})
        record_no_bin_size = record.with_context(bin_size=False)
        record_bin_size = record.with_context(bin_size=True)

        expected_value = [(record.id, False)]
        self.assertEqual(record.binary_computed, expected_value)
        self.assertEqual(record_no_bin_size.binary_computed, expected_value)
        self.assertEqual(record_bin_size.binary_computed, expected_value)

    def test_95_binary_bin_size_write(self):
        binary_value = base64.b64encode(b'content')
        binary_size = b'7.00 bytes'

        def assertBinaryValue(record, value):
            for field in ('binary', 'binary_related_store', 'binary_related_no_store'):
                self.assertEqual(record[field], value, f'Incorrect result for {field}')

        # created and written without context
        record = self.env['test_orm.model_binary'].create({})
        record.write({'binary': binary_value})
        record_no_bin_size = record.with_context(bin_size=False)
        record_bin_size = record.with_context(bin_size=True)

        assertBinaryValue(record, binary_value)
        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record_bin_size, binary_size)

        # created without context, written with bin_size=False
        record = self.env['test_orm.model_binary'].create({})
        record.with_context(bin_size=False).write({'binary': binary_value})
        record_bin_size = record.with_context(bin_size=True)

        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record, binary_value)
        assertBinaryValue(record_bin_size, binary_size)

        # created without context, written with bin_size=True
        record = self.env['test_orm.model_binary'].create({})
        record.with_context(bin_size=True).write({'binary': binary_value})
        record_no_bin_size = record.with_context(bin_size=False)

        assertBinaryValue(record_bin_size, binary_size)
        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record, binary_value)

        # created without context and flushed with bin_size=True
        record = self.env['test_orm.model_binary'].create({})
        record.write({'binary': binary_value})
        record.with_context(bin_size=True).env.invalidate_all()
        record_no_bin_size = record.with_context(bin_size=False)
        record_bin_size = record.with_context(bin_size=True)

        assertBinaryValue(record, binary_value)
        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record_bin_size, binary_size)

        # created and written without context, flushed without bin_size
        record = self.env['test_orm.model_binary'].create({})
        record.write({'binary': binary_value})
        record.env.invalidate_all()
        record_no_bin_size = record.with_context(bin_size=False)
        record_bin_size = record.with_context(bin_size=True)

        assertBinaryValue(record, binary_value)
        assertBinaryValue(record_no_bin_size, binary_value)
        assertBinaryValue(record_bin_size, binary_size)

    def test_96_order_m2o(self):
        belgium, congo = self.env['test_orm.country'].create([
            {'name': "Duchy of Brabant"},
            {'name': "Congo"},
        ])
        cities = self.env['test_orm.city'].create([
            {'name': "Brussels", 'country_id': belgium.id},
            {'name': "Kinshasa", 'country_id': congo.id},
        ])
        # cities are sorted by country_id, name
        self.assertEqual(cities.sorted().mapped('name'), ["Kinshasa", "Brussels"])

        # change order of countries, and check sorted()
        belgium.name = "Belgium"
        self.assertEqual(cities.sorted().mapped('name'), ["Brussels", "Kinshasa"])

    def test_97_ir_rule_m2m_field(self):
        """Ensures m2m fields can't be read if the left records can't be read.
        Also makes sure reading m2m doesn't take more queries than necessary."""
        tag = self.env['test_orm.multi.tag'].create({})
        record = self.env['test_orm.multi.line'].create({
            'name': 'image',
            'tags': [Command.link(tag.id)],
        })

        # only one query as admin: reading pivot table
        with self.assertQueryCount(1):
            # trick: if value is in cache, read() does not make any query
            record.invalidate_recordset(['tags'])
            record.read(['tags'])

        user = self.env['res.users'].create({'name': "user", 'login': "user"})
        record_user = record.with_user(user)

        # prep the following query count by caching access check related data
        record_user.invalidate_recordset(['tags'])
        record_user.read(['tags'])

        # only one query as user: reading pivot table
        with self.assertQueryCount(1):
            # trick: if value is in cache, read() does not make any query
            record_user.invalidate_recordset(['tags'])
            record_user.read(['tags'])

        # create a passing ir.rule
        self.env['ir.rule'].create({
            'model_id': self.env['ir.model']._get(record._name).id,
            'domain_force': "[('id', '=', %d)]" % record.id,
        })

        # prep the following query count by caching access check related data
        record_user.invalidate_recordset(['tags'])
        record_user.read(['tags'])

        # still only 1 query: reading pivot table
        # access rules are checked in python in this case
        with self.assertQueryCount(1):
            # trick: if value is in cache, read() does not make any query
            record_user.invalidate_recordset(['tags'])
            record_user.read(['tags'])

        # create a blocking ir.rule
        self.env['ir.rule'].create({
            'model_id': self.env['ir.model']._get(record._name).id,
            'domain_force': "[('id', '!=', %d)]" % record.id,
        })

        # ensure ir.rule is applied even when reading m2m
        with self.assertRaises(AccessError):
            record_user.read(['tags'])

    def test_98_prefetch_translate(self):
        Model = self.registry['test_orm.prefetch']

        # translated '_rec_name' field should be prefetched
        self.assertTrue(Model.name.prefetch)

        # translated fields should be prefetch=True by default
        self.assertTrue(Model.description.prefetch)
        self.assertTrue(Model.html_description.prefetch)

        # parameter 'prefetch' can be always overridden
        self.assertFalse(Model.rare_description.prefetch)
        self.assertFalse(Model.rare_html_description.prefetch)

    def test_98_unlink_recompute(self):
        move = self.env['test_orm.move'].create({
            'line_ids': [(0, 0, {'quantity': 42})],
        })
        line = move.line_ids
        self.assertEqual(move.quantity, 42)

        # create an ir.rule for lines that uses move.quantity
        self.env['ir.rule'].create({
            'model_id': self.env['ir.model']._get(line._name).id,
            'domain_force': "[('move_id.quantity', '>=', 0)]",
        })

        # unlink the line, and check the recomputation of move.quantity
        user = self.user_demo
        line.with_user(user).unlink()
        self.assertEqual(move.quantity, 0)

    def test_99_prefetch_group(self):
        records = self.env['test_orm.prefetch'].create([{} for _ in range(10)])
        self.env.flush_all()
        self.env.invalidate_all()

        with self.assertQueries(["""
            SELECT "test_orm_prefetch"."id",
                   "test_orm_prefetch"."name"->>%s,
                   "test_orm_prefetch"."description"->>%s,
                   "test_orm_prefetch"."html_description"->>%s,
                   "test_orm_prefetch"."create_uid",
                   "test_orm_prefetch"."create_date",
                   "test_orm_prefetch"."write_uid",
                   "test_orm_prefetch"."write_date"
            FROM "test_orm_prefetch"
            WHERE "test_orm_prefetch"."id" IN %s
        """]):
            records.mapped('name')  # fetch all fields with prefetch=True

        with self.assertQueries(["""
            SELECT
                "test_orm_prefetch"."id",
                "test_orm_prefetch"."harry",
                "test_orm_prefetch"."hermione",
                "test_orm_prefetch"."ron"
            FROM "test_orm_prefetch"
            WHERE "test_orm_prefetch"."id" IN %s
        """]):
            records.mapped('harry')  # fetch all fields with prefetch='Harry Potter'
            records.mapped('hermione')  # fetched already
            records.mapped('ron')  # fetched already

        with self.assertQueries(["""
            SELECT
                "test_orm_prefetch"."id",
                "test_orm_prefetch"."hansel",
                "test_orm_prefetch"."gretel"
            FROM "test_orm_prefetch"
            WHERE "test_orm_prefetch"."id" IN %s
        """]):
            records.mapped('hansel')  # fetch all fields with prefetch='Hansel and Gretel'
            records.mapped('gretel')  # fetched already

        self.env.invalidate_all()

        with self.assertQueryCount(4):
            records.mapped('name')  # fetch all fields with prefetch=True
            records.mapped('hansel')  # fetch all fields with prefetch='Hansel and Gretel'
            records.mapped('harry')  # fetch all fields with prefetch='Harry Potter'
            records.mapped('rare_description')  # fetch that field only

    def test_cache_key_invalidation(self):
        company0 = self.env.ref('base.main_company')
        company1 = self.env['res.company'].create({'name': 'A'})

        user0 = self.env['res.users'].create({
            'name': 'Foo', 'login': 'foo', 'company_id': company0.id,
            'company_ids': [Command.set([company0.id, company1.id])],
        })

        # this uses company0
        record = self.env['test_orm.company'].with_user(user0).create({
            'foo': 'main',
        })
        self.assertEqual(record.env.company, company0)
        self.assertEqual(record.foo, 'main')

        # change the user's company, so we implicitly switch to company1
        user0.company_id = company1
        self.assertEqual(record.env.company, company1)
        self.assertEqual(record.foo, False)

    def test_field_set_prefetch(self):
        records = self.env['test_orm.prefetch'].create([
            {'line_ids': [Command.create({})]},
            {'line_ids': [Command.create({})]},
            {'line_ids': [Command.create({})]},
            {'line_ids': [Command.create({})]},
        ])

        # This test ensures that the prefetch set is preserved when using Field.__set__(),
        # which calls BaseModel.write().  The prefetch set is important for write() to
        # ensure that method modified() can batch the fetching of relational fields.
        # In this case, modifying 'harry' on a record should add a related field to
        # recompute through the one2many field 'line_ids', which we expect to be fetched
        # in batch with the prefetch set.

        # one query for modified, one for the records, one for their lines
        self.env.invalidate_all()
        with self.assertQueryCount(3):
            for index, record in enumerate(records):
                record.harry = index + 1

        # same result by calling write() directly
        self.env.invalidate_all()
        with self.assertQueryCount(3):
            for index, record in enumerate(records):
                record.write({'harry': index + 2})

    def test_html_sanitize(self):
        record = self.env['test_orm.mixed'].create({})
        record_value = write_value = "<div>EXTERNAL SUBMISSION - Customer not verified<br>\n<br>\n<p>### TOUR DATA ###</p></div>"
        record.comment0 = write_value
        self.assertEqual(record.comment0, record_value)
        record.invalidate_recordset()
        self.assertEqual(record.comment0, record_value)


class TestX2many(TransactionExpressionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_portal = cls.env['res.users'].sudo().search([('login', '=', 'portal')])
        cls.partner_portal = cls.user_portal.partner_id

        if not cls.user_portal:
            cls.env['ir.config_parameter'].sudo().set_param('auth_password_policy.minlength', 4)
            cls.partner_portal = cls.env['res.partner'].create({
                'name': 'Joel Willis',
                'email': 'joel.willis63@example.com',
            })
            cls.user_portal = cls.env['res.users'].with_context(no_reset_password=True).create({
                'login': 'portal',
                'password': 'portal',
                'partner_id': cls.partner_portal.id,
                'group_ids': [Command.set([cls.env.ref('base.group_portal').id])],
            })

    def test_definition_many2many(self):
        """ Test the definition of inherited many2many fields. """
        field = self.env['test_orm.multi.line']._fields['tags']
        self.assertEqual(field.relation, 'test_orm_multi_line_test_orm_multi_tag_rel')
        self.assertEqual(field.column1, 'test_orm_multi_line_id')
        self.assertEqual(field.column2, 'test_orm_multi_tag_id')

        field = self.env['test_orm.multi.line2']._fields['tags']
        self.assertEqual(field.relation, 'test_orm_multi_line2_test_orm_multi_tag_rel')
        self.assertEqual(field.column1, 'test_orm_multi_line2_id')
        self.assertEqual(field.column2, 'test_orm_multi_tag_id')

    def test_10_ondelete_many2many(self):
        """Test A can't be deleted when used on the relation."""
        record_a = self.env['test_orm.model_a'].create({'name': 'a'})
        record_b = self.env['test_orm.model_b'].create({'name': 'b'})
        record_a.write({
            'a_restricted_b_ids': [Command.set(record_b.ids)],
        })
        with self.assertRaises(psycopg2.IntegrityError):
            with mute_logger('odoo.sql_db'):
                record_a.unlink()
        # Test B is still cascade.
        record_b.unlink()
        self.assertFalse(record_b.exists())

    def test_11_ondelete_many2many(self):
        """Test B can't be deleted when used on the relation."""
        record_a = self.env['test_orm.model_a'].create({'name': 'a'})
        record_b = self.env['test_orm.model_b'].create({'name': 'b'})
        record_a.write({
            'b_restricted_b_ids': [Command.set(record_b.ids)],
        })
        with self.assertRaises(psycopg2.IntegrityError):
            with mute_logger('odoo.sql_db'):
                record_b.unlink()
        # Test A is still cascade.
        record_a.unlink()
        self.assertFalse(record_a.exists())

    def test_12_active_test_one2many(self):
        Model = self.env['test_orm.model_active_field']

        parent = Model.create({})
        self.assertFalse(parent.children_ids)

        # create with implicit active_test=True in context
        child1, child2 = Model.create([
            {'parent_id': parent.id, 'active': True},
            {'parent_id': parent.id, 'active': False},
        ])
        act_children = child1
        all_children = child1 + child2
        self.assertEqual(parent.children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=True).children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=False).children_ids, all_children)

        # create with active_test=False in context
        child3, child4 = Model.with_context(active_test=False).create([
            {'parent_id': parent.id, 'active': True},
            {'parent_id': parent.id, 'active': False},
        ])
        act_children = child1 + child3
        all_children = child1 + child2 + child3 + child4
        self.assertEqual(parent.children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=True).children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=False).children_ids, all_children)

        # replace active children
        parent.write({'children_ids': [Command.set([child1.id])]})
        act_children = child1
        all_children = child1 + child2 + child4
        self.assertEqual(parent.children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=True).children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=False).children_ids, all_children)

        # replace all children
        parent.with_context(active_test=False).write({'children_ids': [Command.set([child1.id])]})
        act_children = child1
        all_children = child1
        self.assertEqual(parent.children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=True).children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=False).children_ids, all_children)

        # check recomputation of inactive records
        parent.write({'children_ids': [Command.set(child4.ids)]})
        self.assertTrue(child4.parent_active)
        parent.active = False
        self.assertFalse(child4.parent_active)

    def test_12_active_test_one2many_with_context(self):
        Model = self.env['test_orm.model_active_field']
        parent = Model.create({})
        all_children = Model.create([
            {'parent_id': parent.id, 'active': True},
            {'parent_id': parent.id, 'active': False},
        ])
        act_children = all_children[0]

        self.assertEqual(parent.children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=True).children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=False).children_ids, all_children)

        self.assertEqual(parent.all_children_ids, all_children)
        self.assertEqual(parent.with_context(active_test=True).all_children_ids, all_children)
        self.assertEqual(parent.with_context(active_test=False).all_children_ids, all_children)

        self.assertEqual(parent.active_children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=True).active_children_ids, act_children)
        self.assertEqual(parent.with_context(active_test=False).active_children_ids, act_children)

        # check read()
        self.env.invalidate_all()
        self.assertEqual(parent.children_ids, act_children)
        self.assertEqual(parent.all_children_ids, all_children)
        self.assertEqual(parent.active_children_ids, act_children)

        self.env.invalidate_all()
        self.assertEqual(parent.with_context(active_test=False).children_ids, all_children)
        self.assertEqual(parent.with_context(active_test=False).all_children_ids, all_children)
        self.assertEqual(parent.with_context(active_test=False).active_children_ids, act_children)

    def test_12_active_test_one2many_search(self):
        Model = self.env['test_orm.model_active_field']
        parent = Model.create({
            'children_ids': [
                Command.create({'name': 'A', 'active': True}),
                Command.create({'name': 'B', 'active': False}),
            ],
        })

        # a one2many field without context does not match its inactive children
        self.assertIn(parent, self._search(Model, [('children_ids.name', '=', 'A')]))
        self.assertNotIn(parent, self._search(Model, [('children_ids.name', '=', 'B')]))
        # Same result when it used _search_display_name
        self.assertIn(parent, self._search(Model, [('children_ids', '=', 'A')]))
        self.assertNotIn(parent, self._search(Model, [('children_ids', '=', 'B')]))
        # Same result with the child_of operator
        self.assertIn(parent, self._search(Model, [('children_ids', 'child_of', 'A')]))
        self.assertNotIn(parent, self._search(Model, [('children_ids', 'child_of', 'B')]))

        # a one2many field with active_test=False matches its inactive children
        self.assertIn(parent, self._search(Model, [('all_children_ids.name', '=', 'A')]))
        self.assertIn(parent, self._search(Model, [('all_children_ids.name', '=', 'B')]))
        # Same result when it used _search_display_name
        self.assertIn(parent, self._search(Model, [('all_children_ids', '=', 'A')]))
        # Same result with the child_of operator
        self.assertIn(parent, self._search(Model, [('all_children_ids', 'child_of', 'A')]))
        self.assertIn(parent, self._search(Model, [('all_children_ids', '=', 'B')]))
        # Same result with the child_of operator
        self.assertIn(parent, self._search(Model, [('all_children_ids', 'child_of', 'A')]))
        self.assertIn(parent, self._search(Model, [('all_children_ids', 'child_of', 'B')]))

    def test_12_active_test_many2many_search(self):
        Model = self.env['test_orm.model_active_field']
        parent = Model.create({
            'relatives_ids': [
                Command.create({'name': 'A', 'active': True}),
                Command.create({'name': 'B', 'active': False}),
            ],
        })
        child_a, child_b = parent.with_context(active_test=False).relatives_ids
        # TODO all_relatives_ids is empty, because it is another fields using
        # the same backend table as relative_ids
        Model.invalidate_model(['all_relatives_ids'])

        # a many2many field without context does not match its inactive children
        self.assertIn(parent, self._search(Model, [('relatives_ids.name', '=', 'A')]))
        self.assertNotIn(parent, self._search(Model, [('relatives_ids.name', '=', 'B')]))
        # Same result when it used _search_display_name
        self.assertIn(parent, self._search(Model, [('relatives_ids', '=', 'A')]))
        self.assertNotIn(parent, self._search(Model, [('relatives_ids', '=', 'B')]))
        # Same result with the child_of operator
        self.assertIn(parent, self._search(Model, [('relatives_ids', 'child_of', child_a.id)]))
        self.assertIn(parent, self._search(Model, [('relatives_ids', 'child_of', 'A')]))
        self.assertNotIn(parent, self._search(Model, [('relatives_ids', 'child_of', child_b.id)]))
        self.assertNotIn(parent, self._search(Model, [('relatives_ids', 'child_of', 'B')]))

        # a many2many field with active_test=False matches its inactive children
        self.assertIn(parent, self._search(Model, [('all_relatives_ids.name', '=', 'A')]))
        self.assertIn(parent, self._search(Model, [('all_relatives_ids.name', '=', 'B')]))
        # Same result when it used _search_display_name
        self.assertIn(parent, self._search(Model, [('all_relatives_ids', '=', 'A')]))
        self.assertIn(parent, self._search(Model, [('all_relatives_ids', '=', 'B')]))
        # Same result with the child_of operator
        self.assertIn(parent, self._search(Model, [('all_relatives_ids', 'child_of', child_a.id)]))
        self.assertIn(parent, self._search(Model, [('all_relatives_ids', 'child_of', 'A')]))
        self.assertIn(parent, self._search(Model, [('all_relatives_ids', 'child_of', child_b.id)]))
        self.assertIn(parent, self._search(Model, [('all_relatives_ids', 'child_of', 'B')]))

    def test_search_many2many(self):
        """ Tests search on many2many fields. """
        tags = self.env['test_orm.multi.tag']
        tagA = tags.create({})
        tagB = tags.create({})
        tagC = tags.create({})
        recs = self.env['test_orm.multi.line']
        recW = recs.create({})
        recX = recs.create({'tags': [Command.link(tagA.id)]})
        recY = recs.create({'tags': [Command.link(tagB.id)]})
        recZ = recs.create({'tags': [Command.link(tagA.id), Command.link(tagB.id)]})
        recs = recW + recX + recY + recZ

        # test 'in'
        result = self._search(recs, [('tags', 'in', (tagA + tagB).ids)])
        self.assertEqual(result, recX + recY + recZ)

        result = self._search(recs, [('tags', 'in', tagA.ids)])
        self.assertEqual(result, recX + recZ)

        result = self._search(recs, [('tags', 'in', tagB.ids)])
        self.assertEqual(result, recY + recZ)

        result = self._search(recs, [('tags', 'in', tagC.ids)])
        self.assertEqual(result, recs.browse())

        result = self._search(recs, [('tags', 'in', [])])
        self.assertEqual(result, recs.browse())

        # test 'not in'
        result = self._search(recs, [('id', 'in', recs.ids), ('tags', 'not in', (tagA + tagB).ids)])
        self.assertEqual(result, recs - recX - recY - recZ)

        result = self._search(recs, [('id', 'in', recs.ids), ('tags', 'not in', tagA.ids)])
        self.assertEqual(result, recs - recX - recZ)

        result = self._search(recs, [('id', 'in', recs.ids), ('tags', 'not in', tagB.ids)])
        self.assertEqual(result, recs - recY - recZ)

        result = self._search(recs, [('id', 'in', recs.ids), ('tags', 'not in', tagC.ids)])
        self.assertEqual(result, recs)

        result = self._search(recs, [('id', 'in', recs.ids), ('tags', 'not in', [])])
        self.assertEqual(result, recs)

        # special case: compare with False
        result = self._search(recs, [('id', 'in', recs.ids), ('tags', '=', False)])
        self.assertEqual(result, recW)

        result = self._search(recs, [('id', 'in', recs.ids), ('tags', '!=', False)])
        self.assertEqual(result, recs - recW)

    def test_search_one2many(self):
        """ Tests search on one2many fields. """
        recs = self.env['test_orm.multi']
        recX = recs.create({'lines': [Command.create({}), Command.create({})]})
        recY = recs.create({'lines': [Command.create({})]})
        recZ = recs.create({})
        recs = recX + recY + recZ
        line1, line2, line3 = recs.lines
        line4 = recs.create({'lines': [Command.create({})]}).lines
        line0 = line4.create({})

        # test 'in'
        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'in', (line1 + line2 + line3 + line4).ids)])
        self.assertEqual(result, recX + recY)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'in', (line1 + line3 + line4).ids)])
        self.assertEqual(result, recX + recY)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'in', (line1 + line4).ids)])
        self.assertEqual(result, recX)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'in', line4.ids)])
        self.assertEqual(result, recs.browse())

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'in', [])])
        self.assertEqual(result, recs.browse())

        # test 'not in'
        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'not in', (line1 + line2 + line3).ids)])
        self.assertEqual(result, recs - recX - recY)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'not in', (line1 + line3).ids)])
        self.assertEqual(result, recs - recX - recY)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'not in', line1.ids)])
        self.assertEqual(result, recs - recX)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'not in', (line1 + line4).ids)])
        self.assertEqual(result, recs - recX)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'not in', line4.ids)])
        self.assertEqual(result, recs)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'not in', [])])
        self.assertEqual(result, recs)

        # test 'not in' where the lines contain NULL values
        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'not in', (line1 + line0).ids)])
        self.assertEqual(result, recs - recX)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', 'not in', line0.ids)])
        self.assertEqual(result, recs)

        # special case: compare with False
        result = self._search(recs, [('id', 'in', recs.ids), ('lines', '=', False)])
        self.assertEqual(result, recZ)

        result = self._search(recs, [('id', 'in', recs.ids), ('lines', '!=', False)])
        self.assertEqual(result, recs - recZ)

    def test_create_batch_m2m(self):
        lines = self.env['test_orm.multi.line'].create([{
            'tags': [Command.create({'name': str(j)}) for j in range(3)],
        } for i in range(3)])
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertEqual(len(line.tags), 3)

    def test_custom_m2m(self):
        model_id = self.env['ir.model']._get_id('res.partner')
        field = self.env['ir.model.fields'].create({
            'name': 'x_foo',
            'field_description': 'Foo',
            'model_id': model_id,
            'ttype': 'many2many',
            'relation': 'res.country',
            'store': False,
        })
        self.assertTrue(field.unlink())

    def test_custom_m2m_related(self):
        # this checks the ondelete of a related many2many field
        model_id = self.env['ir.model']._get_id('res.partner')
        field = self.env['ir.model.fields'].create({
            'name': 'x_foo',
            'field_description': 'Foo',
            'model_id': model_id,
            'ttype': 'many2many',
            'relation': 'res.partner.category',
            'related': 'category_id',
            'readonly': True,
            'store': True,
        })
        self.assertTrue(field.unlink())

    @mute_logger('odoo.addons.base.models.ir_model')
    @users('portal')
    def test_sudo_commands(self):
        """Test manipulating a x2many field using Commands with `sudo` or with another user (`with_user`)
        is not allowed when the destination model is flagged `_allow_sudo_commands = False` and the transaction user
        does not have the required access rights.

        This test asserts an AccessError is raised
        when a user attempts to pass Commands to a One2many and Many2many field
        targeting a model flagged with `_allow_sudo_commands = False`
        while using an environment with `sudo()` or `with_user(admin_user)`.

        The `with_user` are edge cases in some business codes, where a more-priviledged user is used temporary
        to perform an action, such as:
        - `Documents.with_user(share.create_uid)`
        - `request.env['sign.request'].with_user(contract.hr_responsible_id).sudo()`
        """

        admin_user = self.env.ref('base.user_admin')
        my_user = self.env.user.sudo(False)

        # 1. one2many field `res.partner.user_ids`
        # Sanity checks
        # `res.partner` must be flagged as `_allow_sudo_commands = False` otherwise the test is pointless
        self.assertEqual(self.env['res.users']._allow_sudo_commands, False)
        # in case the type of `res.partner.user_ids` changes in a future release.
        # if `res.partner.user_ids` is no longer a one2many, this test must be adapted.
        self.assertEqual(self.env['res.partner']._fields['user_ids'].type, 'one2many')
        p = my_user.partner_id

        for Partner, my_partner in [
            (self.env['res.partner'].with_user(admin_user), p.with_user(admin_user)),
            (self.env['res.partner'].sudo(), p.sudo()),
        ]:
            # 1.0 Command.CREATE
            # Case: a public/portal user creating a new users with arbitrary values
            with self.assertRaisesRegex(AccessError, "not allowed to create 'User'"):
                Partner.create({
                    'name': 'foo',
                    'user_ids': [Command.create({
                        'login': 'foo',
                        'password': 'foo',
                    })],
                })
            # 1.1 Command.UPDATE
            # Case: a public/portal updating his user to add himself a group
            with self.assertRaisesRegex(AccessError, "not allowed to modify 'User'"):
                my_partner.write({
                    'user_ids': [Command.update(my_partner.user_ids[0].id, {
                        'group_ids': [self.env.ref('base.group_system').id],
                    })],
                })
            # 1.2 Command.DELETE
            # Case: a public user deleting the public user to mess with the database
            with self.assertRaisesRegex(AccessError, "not allowed to delete 'User'"):
                my_partner.write({
                    'user_ids': [Command.delete(my_partner.user_ids[0].id)],
                })
            # 1.3 Command.UNLINK
            # Case: a public user unlinking the public partner and the public user to mess with the database
            with self.assertRaisesRegex(AccessError, "not allowed to modify 'User'"):
                my_partner.write({
                    'user_ids': [Command.unlink(my_partner.user_ids[0].id)],
                })
            # 1.4 Command.LINK
            # Case: a public/portal user changing the `partner_id` of an admin,
            # to change the email address of the user and ask for a reset password.
            with self.assertRaisesRegex(AccessError, "not allowed to modify 'User'"):
                my_partner.write({
                    'user_ids': [Command.link(admin_user.id)],
                })
            # 1.5 Command.CLEAR
            # Case: a public user unlinking the public partner and the public user just to mess with the database
            with self.assertRaisesRegex(AccessError, "not allowed to modify 'User'"):
                my_partner.write({
                    'user_ids': [Command.clear()],
                })
            # 1.6 Command.SET
            # Case: a public/portal user changing the `partner_id` of an admin,
            # to change the email address of the user and ask for a reset password.
            with self.assertRaisesRegex(AccessError, "not allowed to modify 'User'"):
                my_partner.write({
                    'user_ids': [Command.set([admin_user.id])],
                })

        # 2. many2many field `test_orm.discussion.participants`
        # Sanity checks
        # `test_orm.user` must be flagged as `_allow_sudo_commands = False` otherwise the test is pointless
        self.assertEqual(self.env['test_orm.group']._allow_sudo_commands, False)
        # in case the type of `test_orm.discussion.participants` changes in a future release.
        # if `test_orm.discussion.participants` is no longer a many2many, this test must be adapted.
        self.assertEqual(self.env['test_orm.user']._fields['group_ids'].type, 'many2many')
        public_group = self.env['test_orm.group'].with_user(admin_user).create({
            'name': 'public',
        }).with_user(self.env.user)
        u = self.env['test_orm.user'].with_user(admin_user).create({
            'name': 'foo',
            'group_ids': [public_group.id],
        }).with_user(self.env.user)

        for User, my_user in [
            (self.env['test_orm.user'].with_user(admin_user), u.with_user(admin_user)),
            (self.env['test_orm.user'].sudo(), u.sudo()),
        ]:
            # 2.0 Command.CREATE
            # Case: a public/portal user creating a new users with arbitrary values
            with self.assertRaisesRegex(AccessError, "not allowed to create 'test_orm.group'"):
                User.create({
                    'name': 'foo',
                    'group_ids': [Command.create({})],
                })
            # 2.1 Command.UPDATE
            # Case: a public/portal updating his user to add himself a group
            with self.assertRaisesRegex(AccessError, "not allowed to modify 'test_orm.group'"):
                my_user.write({
                    'group_ids': [Command.update(my_user.group_ids[0].id, {})],
                })
            # 2.2 Command.DELETE
            # Case: a public user deleting the public user to mess with the database
            with self.assertRaisesRegex(AccessError, "not allowed to delete 'test_orm.group'"):
                my_user.write({
                    'group_ids': [Command.delete(my_user.group_ids[0].id)],
                })


class TestHtmlField(TransactionCase):

    def setUp(self):
        super().setUp()
        self.model = self.env['test_orm.mixed']

    def test_00_sanitize(self):
        self.assertEqual(self.model._fields['comment1'].sanitize, False)
        self.assertEqual(self.model._fields['comment2'].sanitize_attributes, True)
        self.assertEqual(self.model._fields['comment2'].strip_classes, False)
        self.assertEqual(self.model._fields['comment3'].sanitize_attributes, True)
        self.assertEqual(self.model._fields['comment3'].strip_classes, True)

        some_ugly_html = """<p>Oops this should maybe be sanitized
% if object.some_field and not object.oriented:
<table>
    % if object.other_field:
    <tr style="margin: 0px; border: 10px solid black;">
        ${object.mako_thing}
        <td>
    </tr>
    <tr class="custom_class">
        This is some html.
    </tr>
    % endif
    <tr>
%if object.dummy_field:
        <p>Youpie</p>
%endif"""

        record = self.model.create({
            'comment1': some_ugly_html,
            'comment2': some_ugly_html,
            'comment3': some_ugly_html,
            'comment4': some_ugly_html,
        })

        self.assertEqual(record.comment1, some_ugly_html, 'Error in HTML field: content was sanitized but field has sanitize=False')

        self.assertIn('<tr class="', record.comment2)

        # sanitize should have closed tags left open in the original html
        self.assertIn('</table>', record.comment3, 'Error in HTML field: content does not seem to have been sanitized despise sanitize=True')
        self.assertIn('</td>', record.comment3, 'Error in HTML field: content does not seem to have been sanitized despise sanitize=True')
        self.assertIn('<tr style="', record.comment3, 'Style attr should not have been stripped')
        # sanitize does not keep classes if asked to
        self.assertNotIn('<tr class="', record.comment3)

        self.assertNotIn('<tr style="', record.comment4, 'Style attr should have been stripped')

    def test_01_sanitize_groups(self):
        self.assertEqual(self.model._fields['comment5'].sanitize, True)
        self.assertEqual(self.model._fields['comment5'].sanitize_overridable, True)

        internal_user = self.env['res.users'].create({
            'name': 'test internal user',
            'login': 'test_sanitize',
            'group_ids': [(6, 0, [self.ref('base.group_user')])],
        })
        bypass_user = self.env['res.users'].create({
            'name': 'test bypass user',
            'login': 'test_sanitize2',
            'group_ids': [(6, 0, [self.ref('base.group_user'), self.ref('base.group_sanitize_override')])],
        })
        record = self.env['test_orm.mixed'].create({})

        # 1. Test normalize case: diff due to normalize should not prevent the
        #    changes
        val = '<blockquote>Something</blockquote>'
        normalized_val = '<blockquote data-o-mail-quote-node="1" data-o-mail-quote="1">Something</blockquote>'
        write_vals = {'comment5': val}

        record.with_user(internal_user).write(write_vals)
        self.assertEqual(record.comment5, normalized_val,
                         "should be normalized (not in groups)")
        record.with_user(bypass_user).write(write_vals)
        self.assertEqual(record.comment5, val,
                         "should not be normalized (has group)")
        record.with_user(internal_user).write(write_vals)
        self.assertEqual(record.comment5, normalized_val,
                         "should be normalized (not in groups) despite admin previous diff")

        # 2. Test main use case: prevent restricted user to wipe non restricted
        #    user previous change
        val = '<script></script>'
        write_vals = {'comment5': val}

        record.with_user(internal_user).write(write_vals)
        self.assertEqual(record.comment5, '',
                         "should be sanitized (not in groups)")
        record.with_user(bypass_user).write(write_vals)
        self.assertEqual(record.comment5, val,
                         "should not be sanitized (has group)")
        with self.assertRaises(UserError):
            # should crash (not in groups and sanitize would break content of
            # other user that bypassed the sanitize)
            record.with_user(internal_user).write(write_vals)

        # 3. Make sure field compare in `_convert` is working as expected with
        #    special content / format
        val = '<span  attr1 ="att1"   attr2=\'attr2\'>é@&nbsp;</span><p><span/></p>'
        write_vals = {'comment5': val}
        # Once sent through `html_sanitize()` this is becoming:
        # `<span attr1="att1" attr2="attr2">é@\xa0</span><p><span></span></p>`
        # Notice those change:
        # -     `attr1 =` -> `attr1=`    (space before `=`)
        # -    `   attr2` -> ` attr2`    (multi space -> single space)
        # -  `=\'attr2\'` -> `="attr2"`  (escaped single quote -> double quote)
        # -      `&nbsp;` -> `\xa0`
        # Still, those 2 archs should be considered equals and not raise

        record.with_user(bypass_user).write(write_vals)
        # Next write shouldn't raise a sanitize right error
        record.with_user(internal_user).write(write_vals)

        # 4. Ensure our exception handling is fine
        val = '<!-- I am a comment -->'
        write_vals = {'comment5': val}
        record.with_user(internal_user).write(write_vals)
        self.assertEqual(record.comment5, '',
                         "should be sanitized (not in groups)")

        # extra test with new record having 'record' as origin
        new_record = record.new(origin=record)
        new_record.with_user(bypass_user).comment5

        # this was causing an infinite recursion (see explanation in fields.py)
        new_record.invalidate_recordset()
        new_record.with_user(internal_user).comment5

    @patch('odoo.orm.fields_textual.html_sanitize', return_value='<p>comment</p>')
    def test_onchange_sanitize(self, patch):
        self.assertTrue(self.registry['test_orm.mixed'].comment2.sanitize)

        record = self.env['test_orm.mixed'].create({
            'comment2': '<p>comment</p>',
        })

        # in a perfect world this should be 1, but at the moment the value is
        # sanitized more than once during creation of the record
        self.assertEqual(patch.call_count, 2)

        # new value needs to be validated, so it is sanitized once more
        record.comment2 = '<p>comment</p>'
        self.assertEqual(patch.call_count, 3)

        # the value is already sanitized for flushing
        record.flush_recordset()
        self.assertEqual(patch.call_count, 3)

        # value coming from db does not need to be sanitized
        record.invalidate_recordset()
        record.comment2
        self.assertEqual(patch.call_count, 3)

        # value coming from db during an onchange does not need to be sanitized
        new_record = record.new(origin=record)
        new_record.comment2
        self.assertEqual(patch.call_count, 3)


class TestMagicFields(TransactionCase):

    def test_write_date(self):
        record = self.env['test_orm.discussion'].create({'name': 'Booba'})
        self.assertEqual(record.create_uid, self.env.user)
        self.assertEqual(record.write_uid, self.env.user)

    def test_mro_mixin(self):
        #                               Mixin
        #                                |
        #                                |
        #                                |
        #   ExtendedDisplay    'test_orm.mixin'    Display    'base'
        #         |                      |            |         |
        #         +----------------------+------------+---------+
        #                                |
        #                       'test_orm.display'
        #
        # The field 'display_name' is defined as store=True on the class Display
        # above.  The field 'display_name' on the model 'test_orm.mixin' is
        # expected to be automatic and non-stored.  But the field 'display_name'
        # on the model 'test_orm.display' should not be automatic: it must
        # correspond to the definition given in class Display, even if the MRO
        # of the model shows the automatic field on the mixin model before the
        # actual definition.
        registry = self.env.registry
        models = registry.models

        # check setup of models in alphanumeric order
        self.patch(registry, 'models', OrderedDict(sorted(models.items())))
        registry._setup_models__(self.cr)
        field = registry['test_orm.display'].display_name
        self.assertTrue(field.store)

        # check setup of models in reverse alphanumeric order
        self.patch(registry, 'models', OrderedDict(sorted(models.items(), reverse=True)))
        registry._setup_models__(self.cr)
        field = registry['test_orm.display'].display_name
        self.assertTrue(field.store)


class TestParentStore(TransactionCase):

    def setUp(self):
        super().setUp()
        # make a tree of categories:
        #   0
        #  /|\
        # 1 2 3
        #    /|\
        #   4 5 6
        #      /|\
        #     7 8 9
        Cat = self.env['test_orm.category']
        cat0 = Cat.create({'name': '0'})
        cat1 = Cat.create({'name': '1', 'parent': cat0.id})
        cat2 = Cat.create({'name': '2', 'parent': cat0.id})
        cat3 = Cat.create({'name': '3', 'parent': cat0.id})
        cat4 = Cat.create({'name': '4', 'parent': cat3.id})
        cat5 = Cat.create({'name': '5', 'parent': cat3.id})
        cat6 = Cat.create({'name': '6', 'parent': cat3.id})
        cat7 = Cat.create({'name': '7', 'parent': cat6.id})
        cat8 = Cat.create({'name': '8', 'parent': cat6.id})
        cat9 = Cat.create({'name': '9', 'parent': cat6.id})
        self._cats = Cat.concat(cat0, cat1, cat2, cat3, cat4,
                                cat5, cat6, cat7, cat8, cat9)

    def cats(self, *indexes):
        """ Return the given categories. """
        ids = self._cats.ids
        return self._cats.browse([ids[index] for index in indexes])

    def assertChildOf(self, category, children):
        self.assertEqual(category.search([('id', 'child_of', category.ids)]), children)

    def assertParentOf(self, category, parents):
        self.assertEqual(category.search([('id', 'parent_of', category.ids)]), parents)

    def test_base(self):
        """ Check the initial tree structure. """
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(1), self.cats(1))
        self.assertChildOf(self.cats(2), self.cats(2))
        self.assertChildOf(self.cats(3), self.cats(3, 4, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(4), self.cats(4))
        self.assertChildOf(self.cats(5), self.cats(5))
        self.assertChildOf(self.cats(6), self.cats(6, 7, 8, 9))
        self.assertChildOf(self.cats(7), self.cats(7))
        self.assertChildOf(self.cats(8), self.cats(8))
        self.assertChildOf(self.cats(9), self.cats(9))
        self.assertParentOf(self.cats(0), self.cats(0))
        self.assertParentOf(self.cats(1), self.cats(0, 1))
        self.assertParentOf(self.cats(2), self.cats(0, 2))
        self.assertParentOf(self.cats(3), self.cats(0, 3))
        self.assertParentOf(self.cats(4), self.cats(0, 3, 4))
        self.assertParentOf(self.cats(5), self.cats(0, 3, 5))
        self.assertParentOf(self.cats(6), self.cats(0, 3, 6))
        self.assertParentOf(self.cats(7), self.cats(0, 3, 6, 7))
        self.assertParentOf(self.cats(8), self.cats(0, 3, 6, 8))
        self.assertParentOf(self.cats(9), self.cats(0, 3, 6, 9))

    def test_base_compute(self):
        """ Check the tree structure after computation from scratch. """
        self.cats()._parent_store_compute()
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(1), self.cats(1))
        self.assertChildOf(self.cats(2), self.cats(2))
        self.assertChildOf(self.cats(3), self.cats(3, 4, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(4), self.cats(4))
        self.assertChildOf(self.cats(5), self.cats(5))
        self.assertChildOf(self.cats(6), self.cats(6, 7, 8, 9))
        self.assertChildOf(self.cats(7), self.cats(7))
        self.assertChildOf(self.cats(8), self.cats(8))
        self.assertChildOf(self.cats(9), self.cats(9))
        self.assertParentOf(self.cats(0), self.cats(0))
        self.assertParentOf(self.cats(1), self.cats(0, 1))
        self.assertParentOf(self.cats(2), self.cats(0, 2))
        self.assertParentOf(self.cats(3), self.cats(0, 3))
        self.assertParentOf(self.cats(4), self.cats(0, 3, 4))
        self.assertParentOf(self.cats(5), self.cats(0, 3, 5))
        self.assertParentOf(self.cats(6), self.cats(0, 3, 6))
        self.assertParentOf(self.cats(7), self.cats(0, 3, 6, 7))
        self.assertParentOf(self.cats(8), self.cats(0, 3, 6, 8))
        self.assertParentOf(self.cats(9), self.cats(0, 3, 6, 9))

    def test_delete(self):
        """ Delete a node. """
        self.cats(6).unlink()
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4, 5))
        self.assertChildOf(self.cats(3), self.cats(3, 4, 5))
        self.assertChildOf(self.cats(5), self.cats(5))
        self.assertParentOf(self.cats(0), self.cats(0))
        self.assertParentOf(self.cats(3), self.cats(0, 3))
        self.assertParentOf(self.cats(5), self.cats(0, 3, 5))

    def test_move_1_0(self):
        """ Move a node to a root position. """
        self.cats(6).write({'parent': False})
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4, 5))
        self.assertChildOf(self.cats(3), self.cats(3, 4, 5))
        self.assertChildOf(self.cats(6), self.cats(6, 7, 8, 9))
        self.assertParentOf(self.cats(9), self.cats(6, 9))

    def test_move_1_1(self):
        """ Move a node into an empty subtree. """
        self.cats(6).write({'parent': self.cats(1).id})
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(1), self.cats(1, 6, 7, 8, 9))
        self.assertChildOf(self.cats(3), self.cats(3, 4, 5))
        self.assertChildOf(self.cats(6), self.cats(6, 7, 8, 9))
        self.assertParentOf(self.cats(9), self.cats(0, 1, 6, 9))

    def test_move_1_N(self):
        """ Move a node into a non-empty subtree. """
        self.cats(6).write({'parent': self.cats(0).id})
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(3), self.cats(3, 4, 5))
        self.assertChildOf(self.cats(6), self.cats(6, 7, 8, 9))
        self.assertParentOf(self.cats(9), self.cats(0, 6, 9))

    def test_move_N_0(self):
        """ Move multiple nodes to root position. """
        self.cats(5, 6).write({'parent': False})
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4))
        self.assertChildOf(self.cats(3), self.cats(3, 4))
        self.assertChildOf(self.cats(5), self.cats(5))
        self.assertChildOf(self.cats(6), self.cats(6, 7, 8, 9))
        self.assertParentOf(self.cats(5), self.cats(5))
        self.assertParentOf(self.cats(9), self.cats(6, 9))

    def test_move_N_1(self):
        """ Move multiple nodes to an empty subtree. """
        self.cats(5, 6).write({'parent': self.cats(1).id})
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(1), self.cats(1, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(3), self.cats(3, 4))
        self.assertChildOf(self.cats(5), self.cats(5))
        self.assertChildOf(self.cats(6), self.cats(6, 7, 8, 9))
        self.assertParentOf(self.cats(5), self.cats(0, 1, 5))
        self.assertParentOf(self.cats(9), self.cats(0, 1, 6, 9))

    def test_move_N_N(self):
        """ Move multiple nodes to a non- empty subtree. """
        self.cats(5, 6).write({'parent': self.cats(0).id})
        self.assertChildOf(self.cats(0), self.cats(0, 1, 2, 3, 4, 5, 6, 7, 8, 9))
        self.assertChildOf(self.cats(3), self.cats(3, 4))
        self.assertChildOf(self.cats(5), self.cats(5))
        self.assertChildOf(self.cats(6), self.cats(6, 7, 8, 9))
        self.assertParentOf(self.cats(5), self.cats(0, 5))
        self.assertParentOf(self.cats(9), self.cats(0, 6, 9))

    def test_move_1_cycle(self):
        """ Move a node to create a cycle. """
        with self.assertRaises(UserError):
            self.cats(3).write({'parent': self.cats(9).id})

    def test_move_N_cycle(self):
        """ Move multiple nodes to create a cycle. """
        with self.assertRaises(UserError):
            self.cats(1, 3).write({'parent': self.cats(9).id})

    def test_compute_depend_parent_path(self):
        self.assertEqual(self.cats(7).depth, 3)
        self.assertEqual(self.cats(8).depth, 3)
        self.assertEqual(self.cats(9).depth, 3)

        # change parent of node to have 2 parents
        self.cats(7).parent = self.cats(2)
        self.assertEqual(self.cats(7).depth, 2)

        # change parent of node to root
        self.cats(7).parent = False
        self.assertEqual(self.cats(7).depth, 0)

        # change grand-parent of nodes
        self.cats(6).parent = self.cats(0)
        self.assertEqual(self.cats(8).depth, 2)
        self.assertEqual(self.cats(9).depth, 2)

        # add a new node: one query to INSERT, one query to UPDATE parent_path
        with self.assertQueryCount(2):
            cat = self.cats().create({'name': '10', 'parent': self.cats(6).id})
            self.assertEqual(cat.depth, 2)


class TestRequiredMany2one(TransactionCase):

    def test_explicit_ondelete(self):
        field = self.env['test_orm.req_m2o']._fields['foo']
        self.assertEqual(field.ondelete, 'cascade')

    def test_implicit_ondelete(self):
        field = self.env['test_orm.req_m2o']._fields['bar']
        self.assertEqual(field.ondelete, 'restrict')

    def test_explicit_set_null(self):
        Model = self.env['test_orm.req_m2o']
        field = Model._fields['foo']

        # clean up registry after this test
        self.addCleanup(self.registry.reset_changes)
        self.patch(field, 'ondelete', 'set null')

        with self.assertRaises(ValueError):
            field.setup_nonrelated(Model)


class TestRequiredMany2oneTransient(TransactionCase):

    def test_explicit_ondelete(self):
        field = self.env['test_orm.req_m2o_transient']._fields['foo']
        self.assertEqual(field.ondelete, 'restrict')

    def test_implicit_ondelete(self):
        field = self.env['test_orm.req_m2o_transient']._fields['bar']
        self.assertEqual(field.ondelete, 'cascade')

    def test_explicit_set_null(self):
        Model = self.env['test_orm.req_m2o_transient']
        field = Model._fields['foo']

        # clean up registry after this test
        self.addCleanup(self.registry.reset_changes)
        self.patch(field, 'ondelete', 'set null')

        with self.assertRaises(ValueError):
            field.setup_nonrelated(Model)


@tagged('m2oref')
class TestMany2oneReference(TransactionExpressionCase):

    def test_delete_m2o_reference_records(self):
        m = self.env['test_orm.model_many2one_reference']
        self.env.cr.execute("SELECT max(id) FROM test_orm_model_many2one_reference")
        ids = self.env.cr.fetchone()
        # fake record to emulate the unlink of a non-existant record
        foo = m.browse(1 if not ids[0] else (ids[0] + 1))
        self.assertTrue(foo.unlink())

    def test_search_inverse_one2many_autojoin(self):
        record = self.env['test_orm.inverse_m2o_ref'].create({})

        # the one2many field 'model_ids' should be auto_join=True
        self.patch(type(record).model_ids, 'auto_join', True)

        # create a reference to record
        reference = self.env['test_orm.model_many2one_reference'].create({'res_id': record.id})
        reference.res_model = record._name

        # the model field 'res_model' is not in database yet
        self.assertIn(record.id, self.env._field_dirty[reference._fields['res_model']])

        # searching on the one2many should flush the field 'res_model'
        records = record.search([('model_ids.create_date', '!=', False)])
        self.assertIn(record, records)

        # filtered should be aligned
        # TODO right now, need to invalidate because the inverse of
        # many2one_reference is not updated
        record.invalidate_model()
        self._search(record, [('model_ids.create_date', '!=', False)])


@tagged('selection_abstract')
class TestSelectionDeleteUpdate(TransactionCase):

    MODEL_ABSTRACT = 'test_orm.state_mixin'

    def setUp(self):
        super().setUp()
        # enable unlinking ir.model.fields.selection
        self.patch(self.registry, 'ready', False)

    def test_unlink_asbtract(self):
        self.env['ir.model.fields.selection'].search([
            ('field_id.model', '=', self.MODEL_ABSTRACT),
            ('field_id.name', '=', 'state'),
            ('value', '=', 'confirmed'),
        ], limit=1).unlink()


@tagged('selection_update_base')
class TestSelectionUpdates(TransactionCase):
    MODEL_BASE = 'test_orm.model_selection_base'
    MODEL_RELATED = 'test_orm.model_selection_related'
    MODEL_RELATED_UPDATE = 'test_orm.model_selection_related_updatable'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Specifying a lang in env/context should not increase query counts
        # of CRUD operations
        cls.env = cls.env(context={'lang': 'en_US'})

    def test_selection(self):
        self.env[self.MODEL_BASE].create({})   # warming up
        with self.assertQueryCount(1):
            self.env[self.MODEL_BASE].create({})
        with self.assertQueryCount(1):
            record = self.env[self.MODEL_BASE].create({'my_selection': 'foo'})
        with self.assertQueryCount(1):
            record.my_selection = 'bar'

    def test_selection_related_readonly(self):
        related_record = self.env[self.MODEL_BASE].create({'my_selection': 'foo'})
        with self.assertQueryCount(2):  # defaults (readonly related field), INSERT
            record = self.env[self.MODEL_RELATED].create({'selection_id': related_record.id})
        with self.assertQueryCount(0):
            record.related_selection = 'bar'

    def test_selection_related(self):
        related_record = self.env[self.MODEL_BASE].create({'my_selection': 'foo'})
        with self.assertQueryCount(2):  # defaults (related field), INSERT
            record = self.env[self.MODEL_RELATED_UPDATE].create({'selection_id': related_record.id})
        with self.assertQueryCount(2):
            record.related_selection = 'bar'


@tagged('selection_ondelete_base')
class TestSelectionOndelete(TransactionCase):

    MODEL_BASE = 'test_orm.model_selection_base'
    MODEL_REQUIRED = 'test_orm.model_selection_required'
    MODEL_NONSTORED = 'test_orm.model_selection_non_stored'
    MODEL_WRITE_OVERRIDE = 'test_orm.model_selection_required_for_write_override'

    def setUp(self):
        super().setUp()
        # enable unlinking ir.model.fields.selection
        self.patch(self.registry, 'ready', False)

    def _unlink_option(self, model, option):
        self.env['ir.model.fields.selection'].search([
            ('field_id.model', '=', model),
            ('field_id.name', '=', 'my_selection'),
            ('value', '=', option),
        ], limit=1).unlink()

    def test_ondelete_default(self):
        # create some records, one of which having the extended selection option
        rec1 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'foo'})
        rec2 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'bar'})
        rec3 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'baz'})

        # test that all values are correct before the removal of the value
        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertEqual(rec3.my_selection, 'baz')

        # unlink the extended option (simulates a module uninstall)
        self._unlink_option(self.MODEL_REQUIRED, 'baz')

        # verify that the ondelete policy has succesfully been applied
        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertEqual(rec3.my_selection, 'foo')   # reset to default

    def test_ondelete_base_null_explicit(self):
        rec1 = self.env[self.MODEL_BASE].create({'my_selection': 'foo'})
        rec2 = self.env[self.MODEL_BASE].create({'my_selection': 'bar'})
        rec3 = self.env[self.MODEL_BASE].create({'my_selection': 'quux'})

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertEqual(rec3.my_selection, 'quux')

        self._unlink_option(self.MODEL_BASE, 'quux')

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertFalse(rec3.my_selection)

    def test_ondelete_base_null_implicit(self):
        rec1 = self.env[self.MODEL_BASE].create({'my_selection': 'foo'})
        rec2 = self.env[self.MODEL_BASE].create({'my_selection': 'bar'})
        rec3 = self.env[self.MODEL_BASE].create({'my_selection': 'ham'})

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertEqual(rec3.my_selection, 'ham')

        self._unlink_option(self.MODEL_BASE, 'ham')

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertFalse(rec3.my_selection)

    def test_ondelete_cascade(self):
        rec1 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'foo'})
        rec2 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'bar'})
        rec3 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'eggs'})

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertEqual(rec3.my_selection, 'eggs')

        self._unlink_option(self.MODEL_REQUIRED, 'eggs')

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertFalse(rec3.exists())

    def test_ondelete_literal(self):
        rec1 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'foo'})
        rec2 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'bar'})
        rec3 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'bacon'})

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertEqual(rec3.my_selection, 'bacon')

        self._unlink_option(self.MODEL_REQUIRED, 'bacon')

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertEqual(rec3.my_selection, 'bar')

    def test_ondelete_multiple_explicit(self):
        rec1 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'foo'})
        rec2 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'eevee'})
        rec3 = self.env[self.MODEL_REQUIRED].create({'my_selection': 'pikachu'})

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'eevee')
        self.assertEqual(rec3.my_selection, 'pikachu')

        self._unlink_option(self.MODEL_REQUIRED, 'eevee')
        self._unlink_option(self.MODEL_REQUIRED, 'pikachu')

        self.assertEqual(rec1.my_selection, 'foo')
        self.assertEqual(rec2.my_selection, 'bar')
        self.assertEqual(rec3.my_selection, 'foo')

    def test_ondelete_callback(self):
        rec = self.env[self.MODEL_REQUIRED].create({'my_selection': 'knickers'})

        self.assertEqual(rec.my_selection, 'knickers')

        self._unlink_option(self.MODEL_REQUIRED, 'knickers')

        self.assertEqual(rec.my_selection, 'foo')
        self.assertFalse(rec.active)

    def test_non_stored_selection(self):
        rec = self.env[self.MODEL_NONSTORED].create({})
        rec.my_selection = 'foo'

        self.assertEqual(rec.my_selection, 'foo')

        self._unlink_option(self.MODEL_NONSTORED, 'foo')

        self.assertFalse(rec.my_selection)

    def test_required_base_selection_field(self):
        # test that no ondelete action is executed on a required selection field that is not
        # extended, only required fields that extend it with selection_add should
        # have ondelete actions defined
        rec = self.env[self.MODEL_REQUIRED].create({'my_selection': 'foo'})
        self.assertEqual(rec.my_selection, 'foo')

        self._unlink_option(self.MODEL_REQUIRED, 'foo')
        self.assertEqual(rec.my_selection, 'foo')

    @mute_logger('odoo.addons.base.models.ir_model')
    def test_write_override_selection(self):
        # test that on override to write that raises an error does not prevent the ondelete
        # policy from executing and cleaning up what needs to be cleaned up
        rec = self.env[self.MODEL_WRITE_OVERRIDE].create({'my_selection': 'divinity'})
        self.assertEqual(rec.my_selection, 'divinity')

        self._unlink_option(self.MODEL_WRITE_OVERRIDE, 'divinity')
        self.assertEqual(rec.my_selection, 'foo')


@tagged('selection_ondelete_advanced')
class TestSelectionOndeleteAdvanced(TransactionCase):

    MODEL_BASE = 'test_orm.model_selection_base'
    MODEL_REQUIRED = 'test_orm.model_selection_required'

    def setUp(self):
        super().setUp()
        # necessary cleanup for resetting changes in the registry
        for model_name in (self.MODEL_BASE, self.MODEL_REQUIRED):
            Model = self.registry[model_name]
            self.addCleanup(setattr, Model, '_base_classes__', Model._base_classes__)

    def test_ondelete_unexisting_policy(self):
        from odoo.orm.model_classes import add_to_registry  # noqa: PLC0415

        class Foo(models.Model):
            _module = None
            _name = self.MODEL_REQUIRED
            _inherit = [self.MODEL_REQUIRED]

            my_selection = fields.Selection(selection_add=[
                ('random', "Random stuff"),
            ], ondelete={'random': 'poop'})

        add_to_registry(self.registry, Foo)

        with self.assertRaises(ValueError):
            self.registry._setup_models__(self.env.cr, [])  # incremental setup

    def test_ondelete_default_no_default(self):
        from odoo.orm.model_classes import add_to_registry  # noqa: PLC0415

        class Foo(models.Model):
            _module = None
            _name = self.MODEL_BASE
            _inherit = [self.MODEL_BASE]

            my_selection = fields.Selection(selection_add=[
                ('corona', "Corona beers suck"),
            ], ondelete={'corona': 'set default'})

        add_to_registry(self.registry, Foo)

        with self.assertRaises(AssertionError):
            self.registry._setup_models__(self.env.cr, [])  # incremental setup

    def test_ondelete_value_no_valid(self):
        from odoo.orm.model_classes import add_to_registry  # noqa: PLC0415

        class Foo(models.Model):
            _module = None
            _name = self.MODEL_BASE
            _inherit = [self.MODEL_BASE]

            my_selection = fields.Selection(selection_add=[
                ('westvleteren', "Westvleteren beers is overrated"),
            ], ondelete={'westvleteren': 'set foooo'})

        add_to_registry(self.registry, Foo)

        with self.assertRaises(AssertionError):
            self.registry._setup_models__(self.env.cr, [])  # incremental setup

    def test_ondelete_required_null_explicit(self):
        from odoo.orm.model_classes import add_to_registry  # noqa: PLC0415

        class Foo(models.Model):
            _module = None
            _name = self.MODEL_REQUIRED
            _inherit = [self.MODEL_REQUIRED]

            my_selection = fields.Selection(selection_add=[
                ('brap', "Brap"),
            ], ondelete={'brap': 'set null'})

        add_to_registry(self.registry, Foo)

        with self.assertRaises(ValueError):
            self.registry._setup_models__(self.env.cr, [])  # incremental setup

    def test_ondelete_required_null_implicit(self):
        from odoo.orm.model_classes import add_to_registry  # noqa: PLC0415

        class Foo(models.Model):
            _module = None
            _name = self.MODEL_REQUIRED
            _inherit = [self.MODEL_REQUIRED]

            my_selection = fields.Selection(selection_add=[
                ('boing', "Boyoyoyoing"),
            ])

        add_to_registry(self.registry, Foo)

        with self.assertRaises(ValueError):
            self.registry._setup_models__(self.env.cr, [])  # incremental setup


class TestFieldParametersValidation(TransactionCase):
    def test_invalid_parameter(self):
        from odoo.orm.model_classes import add_to_registry  # noqa: PLC0415

        class Foo(models.Model):
            _module = None
            _name = _description = 'test_orm.field_parameter_validation'

            name = fields.Char(invalid_parameter=42)

        add_to_registry(self.registry, Foo)
        self.addCleanup(self.registry.__delitem__, Foo._name)

        with self.assertLogs('odoo.fields', level='WARNING') as cm:
            self.registry._setup_models__(self.env.cr, [])  # incremental setup

        self.assertTrue(cm.output[0].startswith(
            "WARNING:odoo.fields:Field test_orm.field_parameter_validation.name: "
            "unknown parameter 'invalid_parameter'",
        ))


def select(model, *fnames):
    """ Return the expected query string to SELECT the given columns. """
    table = model._table
    model_fields = model._fields
    terms = ", ".join(
        f'"{table}"."{fname}"' if not model_fields[fname].translate else f'"{table}"."{fname}"->>%s'
        for fname in ['id'] + list(fnames)
    )
    return f'SELECT {terms} FROM "{table}" WHERE "{table}"."id" IN %s'


def insert(model, *fnames, rowcount=1):
    """ Return the expected query string to INSERT the given columns. """
    columns = sorted(fnames + ('create_uid', 'create_date', 'write_uid', 'write_date'))
    header = ", ".join(f'"{column}"' for column in columns)
    template = ", ".join("%s" for _index in range(rowcount))
    return f'INSERT INTO "{model._table}" ({header}) VALUES {template} RETURNING "id"'


def update(model, *fnames):
    """ Return the expected query string to UPDATE the given columns. """
    table = f'"{model._table}"'
    fnames = sorted(fnames + ('write_uid', 'write_date'))
    columns = ", ".join(f'"{column}"' for column in fnames)
    assignments = ", ".join(
        f'"{fname}" = "__tmp"."{fname}"::{model._fields[fname].column_type[1]}'
        for fname in fnames
    )
    return (
        f'UPDATE {table} SET {assignments} '
        f'FROM (VALUES %s) AS "__tmp"("id", {columns}) '
        f'WHERE {table}."id" = "__tmp"."id"'
    )


class TestComputeQueries(TransactionCase):
    """ Test the queries made by create() with computed fields. """

    def test_compute_readonly(self):
        model = self.env['test_orm.compute.readonly']
        model.create({})

        # no value, no default
        with self.assertQueries([insert(model, 'foo'), update(model, 'bar')]):
            record = model.create({'foo': 'Foo'})
        self.assertEqual(record.bar, 'Foo')

        # some value, no default
        with self.assertQueries([insert(model, 'foo', 'bar'), update(model, 'bar')]):
            record = model.create({'foo': 'Foo', 'bar': 'Bar'})
        self.assertEqual(record.bar, 'Foo')

        model = model.with_context(default_bar='Def')

        # no value, some default
        with self.assertQueries([insert(model, 'foo', 'bar'), update(model, 'bar')]):
            record = model.create({'foo': 'Foo'})
        self.assertEqual(record.bar, 'Foo')

        # some value, some default
        with self.assertQueries([insert(model, 'foo', 'bar'), update(model, 'bar')]):
            record = model.create({'foo': 'Foo', 'bar': 'Bar'})
        self.assertEqual(record.bar, 'Foo')

    def test_compute_readwrite(self):
        model = self.env['test_orm.compute.readwrite']
        model.create({})

        # no value, no default
        with self.assertQueries([insert(model, 'foo'), update(model, 'bar')]):
            record = model.create({'foo': 'Foo'})
        self.assertEqual(record.bar, 'Foo')

        # some value, no default
        with self.assertQueries([insert(model, 'foo', 'bar')]):
            record = model.create({'foo': 'Foo', 'bar': 'Bar'})
        self.assertEqual(record.bar, 'Bar')

        model = model.with_context(default_bar='Def')

        # no value, some default
        with self.assertQueries([insert(model, 'foo', 'bar')]):
            record = model.create({'foo': 'Foo'})
        self.assertEqual(record.bar, 'Def')

        # some value, some default
        with self.assertQueries([insert(model, 'foo', 'bar')]):
            record = model.create({'foo': 'Foo', 'bar': 'Bar'})
        self.assertEqual(record.bar, 'Bar')

    def test_compute_inverse(self):
        model = self.env['test_orm.compute.inverse']
        model.create({})

        # no value, no default
        with self.assertQueries([insert(model, 'foo'), update(model, 'bar')]):
            record = model.create({'foo': 'Foo'})
        self.assertEqual(record.foo, 'Foo')
        self.assertEqual(record.bar, 'Foo')

        # some value, no default
        with self.assertQueries([insert(model, 'foo', 'bar'), update(model, 'foo')]):
            record = model.create({'foo': 'Foo', 'bar': 'Bar'})
        self.assertEqual(record.foo, 'Bar')
        self.assertEqual(record.bar, 'Bar')

        model = model.with_context(default_bar='Def')

        # no value, some default
        with self.assertQueries([insert(model, 'foo', 'bar'), update(model, 'foo')]):
            record = model.create({'foo': 'Foo'})
        self.assertEqual(record.foo, 'Def')
        self.assertEqual(record.bar, 'Def')

        # some value, some default
        with self.assertQueries([insert(model, 'foo', 'bar'), update(model, 'foo')]):
            record = model.create({'foo': 'Foo', 'bar': 'Bar'})
        self.assertEqual(record.foo, 'Bar')
        self.assertEqual(record.bar, 'Bar')

    def test_x2many_computed_inverse(self):
        record = self.env['test_orm.compute.inverse'].create(
            {'child_ids': [Command.create({'foo': 'child'})]},
        )
        self.assertEqual(
            len(record.child_ids), 1,
            f"Should be a single record: {record.child_ids!r}",
        )
        self.assertTrue(
            record.child_ids.id,
            f"Should be database records: {record.child_ids!r}",
        )
        self.assertEqual(record.foo, 'has one child')

    def test_multi_create(self):
        model = self.env['test_orm.foo']
        model.create({})

        with self.assertQueries([insert(model, 'name', 'value1', 'value2', rowcount=4)]):
            create_values = [
                {'name': 'Foo1', 'value1': 10},
                {'name': 'Foo2', 'value2': 12},
                {'name': 'Foo3'},
                {},
            ]
            records = model.create(create_values)
        self.assertEqual(records.mapped('name'), ['Foo1', 'Foo2', 'Foo3', False])
        self.assertEqual(records.mapped('value1'), [10, 0, 0, 0])
        self.assertEqual(records.mapped('value2'), [0, 12, 0, 0])

    def test_create_cache_consistency(self):
        """ The cache should always contains the raw value of the database. The
        cache value of non-assigned column during create() should be None for
        any column field type.
        """
        record = self.env['test_orm.create.performance'].create({})
        self.assertEqual(record.confirmed, False)
        cached_value = record._cache['confirmed']

        # the cached value should be the same as if we had fetched it from database
        record.invalidate_recordset()
        record.fetch(['confirmed'])
        self.assertEqual(record._cache['confirmed'], cached_value)

    def test_create_cache_of_compute_store_fields(self):
        model = self.env['test_orm.create.performance']
        model.create({})  # warmup

        with self.assertQueryCount(2):  # one for create + one to update name_changes
            record = model.create({'name': 'blabla'})
            self.assertEqual(record.name_changes, 1)

    def test_create_x2many_performance(self):
        model = self.env['test_orm.create.performance']
        model.create({})  # warmup

        # 1 INSERT on model table (without the pending update of name_changes)
        with self.assertQueryCount(1, flush=False):
            record = model.create({})
        with self.assertQueryCount(0):
            self.assertFalse(record.line_ids)
        with self.assertQueryCount(0):
            self.assertFalse(record.tag_ids)

        # 1 INSERT on model table (without the pending update of name_changes)
        with self.assertQueryCount(1, flush=False):
            record = model.create({
                'line_ids': [],
                'tag_ids': [],
            })
        with self.assertQueryCount(0):
            self.assertFalse(record.line_ids)
        with self.assertQueryCount(0):
            self.assertFalse(record.tag_ids)

        # warmup for defaults in secondary models
        record = model.create({
            'line_ids': [Command.create({})],
            'tag_ids': [Command.create({})],
        })

        # 1 INSERT on model table (without the pending update of name_changes)
        # 1 INSERT on table of comodel of line_ids
        # 1 INSERT on table of comodel of tag_ids
        # 1 INSERT on relation of tag_ids
        with self.assertQueryCount(4, flush=False):
            record = model.create({
                'line_ids': [Command.create({})],
                'tag_ids': [Command.create({})],
            })
        with self.assertQueryCount(0):
            self.assertTrue(record.line_ids)
        with self.assertQueryCount(0):
            self.assertTrue(record.tag_ids)

    def test_partial_compute_batching(self):
        """ Create several 'new' records and check that the partial compute
        method is called only once.
        """
        order = self.env['test_orm.order'].new({
            'line_ids': [Command.create({'reward': False})] * 100,
        })

        OrderLine = self.env.registry['test_orm.order.line']
        with patch.object(
            OrderLine,
            '_compute_has_been_rewarded',
            side_effect=OrderLine._compute_has_been_rewarded,
            autospec=True,
        ) as patch_compute:
            order.line_ids.mapped('has_been_rewarded')
            self.assertEqual(patch_compute.call_count, 1)


class TestComputeSudo(TransactionCaseWithUserDemo):
    def test_compute_sudo_depends_context_uid(self):
        record = self.env['test_orm.compute.sudo'].create({})
        self.assertEqual(record.with_user(self.user_demo).name_for_uid, self.user_demo.name)


class test_shared_cache(TransactionCaseWithUserDemo):
    def test_shared_cache_computed_field(self):
        # Test case: Check that the shared cache is not used if a compute_sudo stored field
        # is computed IF there is an ir.rule defined on this specific model.

        # Real life example:
        # A user can only see its own timesheets on a task, but the field "Planned Hours",
        # which is stored-compute_sudo, should take all the timesheet lines into account
        # However, when adding a new line and then recomputing the value, no existing line
        # from another user is binded on self, then the value is erased and saved on the
        # database.

        task = self.env['test_orm.model_shared_cache_compute_parent'].create({
            'name': 'Shared Task'})
        self.env['test_orm.model_shared_cache_compute_line'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'parent_id': task.id,
            'amount': 1,
        })
        self.assertEqual(task.total_amount, 1)

        self.env.flush_all()
        self.env.invalidate_all()  # Start fresh, as it would be the case on 2 different sessions.

        task = task.with_user(self.user_demo)
        with Form(task) as task_form:
            # Use demo has no access to the already existing line
            self.assertEqual(len(task_form.line_ids), 0)
            # But see the real total_amount
            self.assertEqual(task_form.total_amount, 1)
            # Now let's add a new line (and retrigger the compute method)
            with task_form.line_ids.new() as line:
                line.amount = 2
            # The new value for total_amount, should be 3, not 2.
            self.assertEqual(task_form.total_amount, 2)


@tagged('unlink_constraints')
class TestUnlinkConstraints(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        MODEL = cls.env['test_orm.model_constrained_unlinks']

        cls.deletable_bar = MODEL.create({'bar': 5})
        cls.undeletable_bar = MODEL.create({'bar': 6})
        cls.deletable_foo = MODEL.create({'foo': 'formaggio'})
        cls.undeletable_foo = MODEL.create({'foo': 'prosciutto'})

        from odoo.addons.base.models.ir_model import (  # noqa: PLC0415
            MODULE_UNINSTALL_FLAG,
        )
        uninstall = {MODULE_UNINSTALL_FLAG: True}
        cls.undeletable_bar_uninstall = cls.undeletable_bar.with_context(**uninstall)
        cls.undeletable_foo_uninstall = cls.undeletable_foo.with_context(**uninstall)

    def test_unlink_constraint_manual_bar(self):
        self.assertTrue(self.deletable_bar.unlink())
        with self.assertRaises(ValueError, msg="Nooooooooo bar can't be greater than five!!"):
            self.undeletable_bar.unlink()

    def test_unlink_constraint_uninstall_bar(self):
        self.assertTrue(self.deletable_bar.unlink())
        # should succeed since it's at_uninstall=False
        self.assertTrue(self.undeletable_bar_uninstall.unlink())

    def test_unlink_constraint_manual_foo(self):
        self.assertTrue(self.deletable_foo.unlink())
        with self.assertRaises(ValueError, msg="You didn't say if you wanted it crudo or cotto..."):
            self.undeletable_foo.unlink()

    def test_unlink_constraint_uninstall_foo(self):
        self.assertTrue(self.deletable_foo)
        # should fail since it's at_uninstall=True
        with self.assertRaises(ValueError, msg="You didn't say if you wanted it crudo or cotto..."):
            self.undeletable_foo_uninstall.unlink()


@tagged('wrong_related_path')
class TestWrongRelatedError(TransactionCase):
    def test_wrong_related_path(self):
        from odoo.orm.model_classes import add_to_registry  # noqa: PLC0415

        class Foo(models.Model):
            _module = None
            _name = _description = 'test_orm.wrong_related_path'

            foo_id = fields.Many2one('test_orm.foo')
            foo_non_existing = fields.Char(related='foo_id.non_existing_field')
        add_to_registry(self.registry, Foo)
        self.addCleanup(self.registry.__delitem__, Foo._name)

        errMsg = (
            "Field non_existing_field referenced in related field definition "
            "test_orm.wrong_related_path.foo_non_existing does not exist."
        )
        with self.assertRaisesRegex(KeyError, errMsg):
            self.registry._setup_models__(self.env.cr, [])  # incremental setup


class TestPrecomputeModel(TransactionCase):

    def test_precompute_consistency(self):
        Model = self.registry['test_orm.precompute']
        self.assertEqual(Model.lower.compute, Model.upper.compute)
        self.assertTrue(Model.lower.precompute)
        self.assertTrue(Model.upper.precompute)

        # see what happens if not both are precompute
        self.addCleanup(self.registry.reset_changes)
        self.patch(Model.upper, 'precompute', False)
        with self.assertWarns(UserWarning):
            self.registry._setup_models__(self.cr, ['test_orm.precompute'])
            self.registry.field_computed

    def test_precompute_dependencies_base(self):
        Model = self.registry['test_orm.precompute']
        self.assertTrue(Model.lower.precompute)
        self.assertTrue(Model.upper.precompute)
        self.assertTrue(Model.lowup.precompute)

        # see what happens if precompute depends on non-precompute
        self.addCleanup(self.registry.reset_changes)

        def reset():
            Model.lowup.precompute = True
        self.addCleanup(reset)
        self.patch(Model.lower, 'precompute', False)
        self.patch(Model.upper, 'precompute', False)

        with self.assertWarns(UserWarning):
            self.registry._setup_models__(self.cr, ['test_orm.precompute'])
            self.registry.get_trigger_tree(Model._fields.values())

    def test_precompute_dependencies_many2one(self):
        Model = self.registry['test_orm.precompute']
        Partner = self.registry['res.partner']

        # Model.commercial_id depends on partner_id.commercial_partner_id, and
        # precomputation is valid when traversing many2one fields
        self.assertTrue(Model.commercial_id.precompute)
        self.assertFalse(Partner.commercial_partner_id.precompute)

    def test_precompute_dependencies_one2many(self):
        Model = self.registry['test_orm.precompute']
        Line = self.registry['test_orm.precompute.line']
        self.assertTrue(Model.size.precompute)
        self.assertTrue(Line.size.precompute)

        # see what happens if precompute depends on non-precompute
        self.addCleanup(self.registry.reset_changes)
        # ensure that Model.size.precompute is restored after _setup_models__()
        self.patch(Model.size, 'precompute', True)
        self.patch(Line.size, 'precompute', False)
        with self.assertWarns(UserWarning):
            self.registry._setup_models__(self.cr, ['test_orm.precompute', 'test_orm.precompute.line'])
            self.registry.get_trigger_tree(Model._fields.values())


class TestPrecompute(TransactionCase):

    def test_precompute(self):

        model = self.env['test_orm.precompute']
        Model = self.registry['test_orm.precompute']
        self.assertTrue(Model.lower.precompute)
        self.assertTrue(Model.upper.precompute)
        self.assertTrue(Model.lowup.precompute)

        # warmup
        model.create({'name': 'Foo', 'line_ids': [Command.create({'name': 'bar'})]})
        # the creation makes one insert query for the main record, and one for the line
        with self.assertQueries([
            insert(model, 'name', 'lower', 'upper', 'lowup', 'commercial_id', 'size'),
            insert(model.line_ids, 'parent_id', 'name', 'size'),
        ]):
            record = model.create({'name': 'Foo', 'line_ids': [Command.create({'name': 'bar'})]})

        # check the values in the database
        self.cr.execute(f'SELECT * FROM "{model._table}" WHERE id=%s', [record.id])
        [row] = self.cr.dictfetchall()

        self.assertEqual(row['name'], 'Foo')
        self.assertEqual(row['lower'], 'foo')
        self.assertEqual(row['upper'], 'FOO')
        self.assertEqual(row['lowup'], 'fooFOO')
        self.assertEqual(row['size'], 3)

    def test_precompute_combo(self):
        model = self.env['test_orm.precompute.combo']

        # warmup
        model.create({})
        QUERIES = [insert(model, 'name', 'reader', 'editer', 'setter')]

        # no value at all
        with self.assertQueries(QUERIES):
            record = model.create({'name': 'A'})

        self.assertEqual(record.reader, 'A')
        self.assertEqual(record.editer, 'A')
        self.assertEqual(record.setter, 'A')

        # default value
        with self.assertQueries(QUERIES), self.assertLogs('precompute_setter', level='WARNING'):
            defaults = dict(default_reader='X', default_editer='Y', default_setter='Z')
            record = model.with_context(**defaults).create({'name': 'A'})

        self.assertEqual(record.reader, 'A')
        self.assertEqual(record.editer, 'Y')
        self.assertEqual(record.setter, 'Z')

        # explicit value
        with self.assertQueries(QUERIES), self.assertLogs('precompute_setter', level='WARNING'):
            record = model.create({'name': 'A', 'reader': 'X', 'editer': 'Y', 'setter': 'Z'})

        self.assertEqual(record.reader, 'A')
        self.assertEqual(record.editer, 'Y')
        self.assertEqual(record.setter, 'Z')

    def test_precompute_editable(self):
        model = self.env['test_orm.precompute.editable']

        # no value for bar, no value for baz
        record = model.create({'foo': 'foo'})
        self.assertEqual(record.bar, 'COMPUTED')
        self.assertEqual(record.baz, 'COMPUTED')
        self.assertEqual(record.baz2, 'COMPUTED')

        # value for bar, no value for baz
        record = model.create({'foo': 'foo', 'bar': 'bar'})
        self.assertEqual(record.bar, 'bar')
        self.assertEqual(record.baz, 'COMPUTED')
        self.assertEqual(record.baz2, 'COMPUTED')

        # no value for bar, value for baz: the computation of bar should not
        # recompute baz in memory, in case a third field depends on it
        record = model.create({'foo': 'foo', 'baz': 'baz'})
        self.assertEqual(record.bar, 'COMPUTED')
        self.assertEqual(record.baz, 'baz')
        self.assertEqual(record.baz2, 'baz')

        # value for bar, value for baz
        record = model.create({'foo': 'foo', 'bar': 'bar', 'baz': 'baz'})
        self.assertEqual(record.bar, 'bar')
        self.assertEqual(record.baz, 'baz')
        self.assertEqual(record.baz2, 'baz')

    def test_precompute_readonly(self):
        """
        Ensures
        - a stored, precomputed, readonly field cannot be altered by the user,
        - a stored, precomputed, readonly field,
          but with a states attributes changing the readonly of the field according to the state of the record,
          can be altered by the user.
        The `bar` field is store=True, precompute=True, readonly=True
        The `baz` field is store=True, precompute=True, readonly=False,
        """
        model = self.env['test_orm.precompute.readonly']

        # no value for bar, no value for baz
        record = model.create({'foo': 'foo'})
        self.assertEqual(record.bar, 'COMPUTED')
        self.assertEqual(record.baz, 'COMPUTED')

        # value for bar, no value for baz
        # bar is readonly, it must ignore the value for bar in the create values
        record = model.create({'foo': 'foo', 'bar': 'bar'})
        self.assertEqual(record.bar, 'COMPUTED')
        self.assertEqual(record.baz, 'COMPUTED')

        # no value for bar, value for baz
        # baz is readonly=False
        # the value for baz must be taken into account
        record = model.create({'foo': 'foo', 'baz': 'baz'})
        self.assertEqual(record.bar, 'COMPUTED')
        self.assertEqual(record.baz, 'baz')

        # value for bar, value for baz
        # bar must be ignored
        # baz must be taken into account
        record = model.create({'foo': 'foo', 'bar': 'bar', 'baz': 'baz'})
        self.assertEqual(record.bar, 'COMPUTED')
        self.assertEqual(record.baz, 'baz')

    def test_precompute_required(self):
        model = self.env['test_orm.precompute.required']

        field = type(model).name
        self.assertTrue(field.related)
        self.assertTrue(field.store)
        self.assertTrue(field.required)

        partner = self.env['res.partner'].create({'name': 'Foo'})

        # this will crash if field is not precomputed
        record = model.create({'partner_id': partner.id})
        self.assertEqual(record.name, 'Foo')

        # check the queries being made
        QUERIES = [insert(model, 'partner_id', 'name')]
        with self.assertQueries(QUERIES):
            record = model.create({'partner_id': partner.id})

    def test_precompute_batch(self):
        model = self.env['test_orm.precompute.required']

        partners = self.env['res.partner'].create([
            {'name': name}
            for name in ["Foo", "Bar", "Baz"]
        ])

        # warmup
        model.create({'partner_id': partners[0].id})
        self.env.flush_all()
        self.env.invalidate_all()

        # check the number of queries: 1 SELECT + 1 INSERT
        with self.assertQueryCount(2):
            model.create([{'partner_id': pid} for pid in partners.ids])

    def test_precompute_monetary(self):
        """Make sure the rounding of monetaries correctly prefetches currency fields"""
        model = self.env['test_orm.precompute.monetary']
        currency = self.env['res.currency']

        # warmup
        model.create({})
        self.env.flush_all()
        self.env.invalidate_all()

        fnames = [fname for fname, field in currency._fields.items() if field.prefetch]
        QUERIES = [
            select(currency, *fnames),
            insert(model, 'amount', 'currency_id'),
            select(model, 'currency_id'),
        ]
        with self.assertQueries(QUERIES):
            model.create({})


class TestModifiedPerformance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Modified = cls.env['test_orm.modified']
        cls.ModifiedLine = cls.env['test_orm.modified.line']
        cls.modified_a = cls.Modified.create({
            'name': 'Test',
        })
        cls.modified_line_a = cls.ModifiedLine.create({
            'modified_id': cls.modified_a.id,
            'quantity': 5,
            'price': 1,
        })
        cls.modified_line_a_child = cls.ModifiedLine.create({
            'modified_id': cls.modified_a.id,
            'quantity': 5,
            'price': 2,
            'parent_id': cls.modified_line_a.id,
        })
        cls.modified_line_a_child_child = cls.ModifiedLine.create({
            'modified_id': cls.modified_a.id,
            'quantity': 5,
            'price': 3,
            'parent_id': cls.modified_line_a_child.id,
        })
        cls.env.invalidate_all()  # Clean the cache

    def test_modified_trigger_related(self):
        with self.assertQueryCount(0, flush=False):
            # No queries because `modified_name` has a empty cache
            self.modified_a.name = "Other"

        self.assertEqual(self.modified_line_a.modified_name, 'Other')  # check

    def test_modified_trigger_no_store_compute(self):
        with self.assertQueryCount(0, flush=False):
            # No queries because `total_quantity` has a empty cache
            self.modified_line_a.quantity = 8

        self.assertEqual(self.modified_a.total_quantity, 18)

    def test_modified_trigger_recursive_empty_cache(self):
        with self.assertQueryCount(0, flush=False):
            # No queries because `total_price` has a empty cache
            self.modified_line_a_child_child.price = 4

        self.assertEqual(self.modified_line_a.total_price, 7)
        self.assertEqual(self.modified_line_a.total_price_quantity, 35)
        self.assertEqual(self.modified_line_a_child.total_price, 6)
        self.assertEqual(self.modified_line_a_child.total_price_quantity, 30)

    def test_modified_trigger_recursive_fill_cache(self):
        self.assertEqual(self.modified_line_a.total_price, 6)
        self.assertEqual(self.modified_line_a.total_price_quantity, 30)
        with self.assertQueryCount(0, flush=False):
            # No query because the `modified_line_a.total_price` has fetch every data needed
            self.modified_line_a_child_child.price = 4

        self.assertEqual(self.modified_line_a.total_price_quantity, 35)
        self.assertEqual(self.modified_line_a.total_price, 7)

    def test_modified_trigger_recursive_partial_invalidate(self):
        self.assertEqual(self.modified_line_a_child.total_price_quantity, 25)
        self.modified_line_a_child_child.invalidate_recordset()

        self.modified_line_a_child.price
        with self.assertQueries(["""
            SELECT "test_orm_modified_line"."id",
                   "test_orm_modified_line"."modified_id",
                   "test_orm_modified_line"."quantity",
                   "test_orm_modified_line"."parent_id",
                   "test_orm_modified_line"."create_uid",
                   "test_orm_modified_line"."create_date"
            FROM "test_orm_modified_line"
            WHERE "test_orm_modified_line"."id" IN %s
        """, """
            SELECT "test_orm_modified_line"."id",
                   "test_orm_modified_line"."parent_id"
            FROM "test_orm_modified_line"
            WHERE "test_orm_modified_line"."id" IN %s
        """], flush=False):
            # Two requests:
            # - one for fetch modified_line_a_child_child data (invalidate just before)
            # - one because modified_line_a_child.parent_id (invalidate just before because we invalidate inverse in `_invalidate_cache`,
            # see TODO) -> We should change that
            self.modified_line_a_child_child.price = 4
        self.assertEqual(self.modified_line_a_child_child.total_price_quantity, 20)
        self.assertEqual(self.modified_line_a_child.total_price_quantity, 30)
        self.assertEqual(self.modified_line_a.total_price_quantity, 35)
        self.assertEqual(self.modified_line_a.total_price, 7)
