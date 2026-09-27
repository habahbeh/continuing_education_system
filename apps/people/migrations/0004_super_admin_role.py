# SUPER_ADMIN — full authority on every screen (the client's own account).
# The role constraint is derived from Role.values, so adding a role means
# re-freezing it here, exactly as 0002 did for FINANCE_MANAGER.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0003_participant"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="user",
            name="people_user_role_valid",
        ),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                blank=True,
                choices=[
                    ("CENTER_MANAGER", "مدير المركز"),
                    ("REGISTRATION_OFFICER", "موظف التسجيل"),
                    ("FINANCE_OFFICER", "الموظف المالي"),
                    ("FINANCE_MANAGER", "المدير المالي"),
                    ("CASHIER", "الصندوق"),
                    ("AUDIT_ACCOUNT", "حساب التدقيق"),
                    ("SYSTEM_ADMINISTRATOR", "مدير النظام"),
                    ("SUPER_ADMIN", "صلاحيات كاملة"),
                ],
                help_text="الدور يأتي من المصادقة — لا واجهة تغيّره على الجلسة (D-20)",
                max_length=32,
                verbose_name="الدور",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("role", ""),
                    (
                        "role__in",
                        [
                            "CENTER_MANAGER",
                            "REGISTRATION_OFFICER",
                            "FINANCE_OFFICER",
                            "FINANCE_MANAGER",
                            "CASHIER",
                            "AUDIT_ACCOUNT",
                            "SYSTEM_ADMINISTRATOR",
                            "SUPER_ADMIN",
                        ],
                    ),
                    _connector="OR",
                ),
                name="people_user_role_valid",
            ),
        ),
        # The account the client signs in with gets the role right away.
        migrations.RunSQL(
            sql="UPDATE people_user SET role = 'SUPER_ADMIN' WHERE username = 'admin'",
            reverse_sql="UPDATE people_user SET role = 'CENTER_MANAGER' WHERE username = 'admin'",
        ),
    ]
