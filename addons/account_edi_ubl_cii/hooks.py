def uninstall_hook(env):
    env.cr.execute(
        """
        UPDATE res_partner
        SET invoice_edi_format = NULL
        WHERE invoice_edi_format IN (
            'facturx',
            'ubl_bis3',
            'xrechnung',
            'nlcius',
            'ubl_a_nz',
            'ubl_sg'
        )
        """
    )
