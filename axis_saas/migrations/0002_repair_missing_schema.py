from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("axis_saas", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'axis_saas_saleitem'
                    ) THEN
                        CREATE TABLE axis_saas_saleitem (
                            id BIGSERIAL PRIMARY KEY,
                            name VARCHAR(200) NOT NULL DEFAULT '',
                            quantity INTEGER NOT NULL DEFAULT 0,
                            unit_price NUMERIC(10,2) NOT NULL DEFAULT 0.00,
                            line_total NUMERIC(10,2) NOT NULL DEFAULT 0.00,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            payment_id BIGINT NOT NULL,
                            product_id BIGINT NULL
                        );
                    END IF;
                END $$;

                ALTER TABLE axis_saas_saleitem
                    ADD COLUMN IF NOT EXISTS name VARCHAR(200) NOT NULL DEFAULT '';
                ALTER TABLE axis_saas_saleitem
                    ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE axis_saas_saleitem
                    ADD COLUMN IF NOT EXISTS unit_price NUMERIC(10,2) NOT NULL DEFAULT 0.00;
                ALTER TABLE axis_saas_saleitem
                    ADD COLUMN IF NOT EXISTS line_total NUMERIC(10,2) NOT NULL DEFAULT 0.00;
                ALTER TABLE axis_saas_saleitem
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
                ALTER TABLE axis_saas_saleitem
                    ADD COLUMN IF NOT EXISTS payment_id BIGINT;
                ALTER TABLE axis_saas_saleitem
                    ADD COLUMN IF NOT EXISTS product_id BIGINT;

                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'axis_saas_saleitem_payment_id_fk'
                    ) THEN
                        ALTER TABLE axis_saas_saleitem
                            ADD CONSTRAINT axis_saas_saleitem_payment_id_fk
                            FOREIGN KEY (payment_id)
                            REFERENCES axis_saas_paymenttransaction (id)
                            ON DELETE CASCADE;
                    END IF;
                END $$;

                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'axis_saas_saleitem_product_id_fk'
                    ) THEN
                        ALTER TABLE axis_saas_saleitem
                            ADD CONSTRAINT axis_saas_saleitem_product_id_fk
                            FOREIGN KEY (product_id)
                            REFERENCES axis_saas_product (id)
                            ON DELETE SET NULL;
                    END IF;
                END $$;

                CREATE INDEX IF NOT EXISTS axis_saas_s_payment_bac01d_idx
                    ON axis_saas_saleitem (payment_id, product_id);
                CREATE INDEX IF NOT EXISTS axis_saas_s_product_92f579_idx
                    ON axis_saas_saleitem (product_id, created_at);
            """,
            reverse_sql="""
                DROP TABLE IF EXISTS axis_saas_saleitem CASCADE;
            """,
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE axis_saas_feerecord
                    ADD COLUMN IF NOT EXISTS late_fee_accrued NUMERIC(10,2) NOT NULL DEFAULT 0.00;
            """,
            reverse_sql="""
                ALTER TABLE axis_saas_feerecord
                    DROP COLUMN IF EXISTS late_fee_accrued;
            """,
        ),
    ]
